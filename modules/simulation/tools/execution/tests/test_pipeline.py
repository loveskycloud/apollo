import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
from modules.simulation.tools.execution.binary import get_binary
from modules.simulation.tools.execution.launcher import run_simulation
from modules.simulation.tools.execution.task import SimulationTask, generate_task
from fixtures import package, scenario

APPS = ROOT / 'modules/simulation/apps'


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='task paths ')
        self.path = Path(self.tmp.name)
        self.binary_root = package(self.path / 'binary with spaces')
        self.scene = scenario(self.path / 'scene with spaces.json')
        self.record = self.path / 'input.record'
        self.record.write_text('record fixture')
        self.binary = get_binary(-1, local_path=self.binary_root)

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, name, *args, input=None):
        return subprocess.run([sys.executable, str(APPS / name), *map(str, args)], cwd='/tmp',
                              input=input, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def bag(self, binary=None):
        binary = binary or self.binary
        return generate_task(binary, self.record, workspace=self.path, kind='bag',
            map_dir=binary.path / 'data/map_data/test-map', profile=binary.path / 'profiles/test-car')

    def test_generator_stdout_is_only_a_relocatable_task_document(self):
        result = self.cli('gen_simulation_task.py', '-B', '-1', '--local-binary', self.binary_root, '-f', self.scene)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['binary']['path'], str(self.binary_root))
        self.assertEqual(value['config']['source'], str(self.scene))
        self.assertEqual(value['config']['model'], 'kinematic_control')
        self.assertEqual(SimulationTask.from_dict(value).binary, self.binary)
        self.assertIn('Resolving binary', result.stderr)

    def test_scene_id_and_cluster_remain_explicitly_unavailable(self):
        result = self.cli('gen_simulation_task.py', '-B', '123143', '-i', '123413',
                          '--binary-service-url', 'http://127.0.0.1:1')
        self.assertEqual(result.returncode, 1)
        self.assertIn('use -f', result.stderr)
        self.assertEqual(result.stdout, '')
        out = self.path / 'should-not-exist'
        result = self.cli('launch.py', '-c', 'cluster', '--output-root', out,
                          input=json.dumps(self.bag().to_dict()))
        self.assertEqual(result.returncode, 1)
        self.assertIn('not configured', result.stderr)
        self.assertFalse(out.exists())

    def test_invalid_protocol_empty_pipe_and_changed_input_fail(self):
        for input in ('', json.dumps({'protocol': 'wrong', 'version': 1})):
            result = self.cli('launch.py', input=input)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, '')
        task = self.bag().to_dict()
        self.record.write_text('changed input')
        result = self.cli('launch.py', input=json.dumps(task))
        self.assertEqual(result.returncode, 1)
        self.assertIn('changed after task generation', result.stderr)

    def test_local_launcher_passes_the_selected_object_to_an_isolated_worker(self):
        other = package(self.path / 'second binary', 'second')
        other_binary = get_binary(-1, local_path=other)
        for binary in (self.binary, other_binary):
            result = run_simulation(self.bag(binary), binary, output_root=self.path / 'results')
            observed = json.loads(Path(result.value['job']['outputs'][0]).read_text())
            self.assertEqual(observed['distribution'], str(binary.path))
            self.assertEqual(observed['tag'], json.loads((binary.path / 'manifest.json').read_text())['profile'])
            self.assertTrue(observed['ld'].startswith(str(binary.path / 'lib/runtime')))

    def test_source_exposes_both_commands_and_real_pipe_preserves_json(self):
        script = '''set -o pipefail
source "$1/modules/simulation/setup.bash"
command -v gen_simulation_task.py >&2
command -v launch.py >&2
gen_simulation_task.py -B -1 --local-binary "$2" -f "$3" --kind bag --map-dir "$2/data/map_data/test-map" --profile "$2/profiles/test-car" |
launch.py -c local --output-root "$4"
'''
        result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script, 'test', str(ROOT),
            str(self.binary_root), str(self.record), str(self.path / 'pipe outputs')], cwd='/tmp',
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'PASS')
        self.assertIn(str(APPS / 'gen_simulation_task.py'), result.stderr)

    def test_worker_timeout_stops_native_child_and_preserves_failure(self):
        root = package(self.path / 'blocking binary', blocking=True)
        binary = get_binary(-1, local_path=root)
        output = self.path / 'timeout'
        with self.assertRaises(TimeoutError):
            run_simulation(self.bag(binary), binary, output_root=output, wall_timeout=2)
        run = next(output.iterdir())
        self.assertEqual(json.loads((run / 'result.json').read_text())['status'], 'TIMEOUT')
        pid = int((run / 'jobs/child.pid').read_text())
        self.assertFalse(Path('/proc/' + str(pid)).exists())
        self.assertTrue((run / 'jobs/child.stopped').is_file())

    def test_ctrl_c_stops_native_child_and_returns_130(self):
        root = package(self.path / 'interrupt binary', blocking=True)
        binary = get_binary(-1, local_path=root)
        document = self.path / 'task.json'
        document.write_text(json.dumps(self.bag(binary).to_dict()))
        output = self.path / 'interrupt outputs'
        process = subprocess.Popen([sys.executable, str(APPS / 'launch.py'), '--task-file', str(document),
            '--output-root', str(output)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not list(output.glob('*/jobs/child.pid')):
                if process.poll() is not None or time.monotonic() >= deadline:
                    self.fail('Worker child did not start: ' + process.communicate()[1])
                time.sleep(.05)
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 130, stderr)
            run = next(output.iterdir())
            self.assertEqual(json.loads((run / 'result.json').read_text())['status'], 'CANCELLED')
            self.assertFalse(Path('/proc/' + (run / 'jobs/child.pid').read_text()).exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


if __name__ == '__main__':
    unittest.main()
