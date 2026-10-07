"""Exercise CLI discovery, workspace relocation and shared exit behavior."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
SIMULATION = ROOT / "modules/simulation"


class CliTests(unittest.TestCase):
    def shell(self, script, *args, cwd=None):
        env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}
        return subprocess.run(["bash", "--noprofile", "--norc", "-c", script, "test", *map(str, args)],
                              cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)

    def test_generator_works_outside_workspace_and_setup_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="cli cwd ") as directory:
            result = self.shell('source "$1/setup.bash"; source "$1/setup.bash"; '
                                'command -v gen_binary.py; gen_binary.py --list-profiles; '
                                'python3 -c \'import json,os; print(json.dumps({"cwd":os.getcwd(),'
                                '"paths":os.environ["PATH"].split(":"),"simulation":os.environ["SIMULATION_ROOT_DIR"]}))\'',
                                SIMULATION, cwd=directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.splitlines()
            self.assertEqual(lines[0], str(SIMULATION / "apps/gen_binary.py"))
            self.assertTrue(any(line.startswith("simulation:") for line in lines))
            self.assertTrue(any(line.startswith("all:") for line in lines))
            state = json.loads(lines[-1])
            self.assertEqual(state["cwd"], directory)
            self.assertEqual(state["simulation"], str(SIMULATION))
            self.assertEqual(state["paths"].count(str(SIMULATION / "apps")), 1)

    def test_apollo_environment_exposes_generator(self):
        # Suppress existing GPU detection; this test concerns only the PATH hook.
        result = self.shell('export GPU_SETUP_COMPLETED=1; source "$1/cyber/setup.bash"; '
                            'command -v gen_binary.py; gen_binary.py --help', ROOT, cwd="/tmp")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(SIMULATION / "apps/gen_binary.py"), result.stdout)
        self.assertIn("--list-profiles", result.stdout)
        self.assertNotIn("--container", result.stdout)

    def test_future_app_is_discovered_after_source_without_registration(self):
        with tempfile.TemporaryDirectory(prefix="cli workspace ") as directory:
            workspace = Path(directory)
            simulation = workspace / "modules/simulation"
            apps = simulation / "apps"
            apps.mkdir(parents=True)
            shutil.copy2(SIMULATION / "setup.bash", simulation / "setup.bash")
            shutil.copy2(SIMULATION / "apps/_cli.py", apps / "_cli.py")
            result = self.shell('source "$1/setup.bash"; cp "$2" "$1/apps/gen_future.py"; '
                                'chmod +x "$1/apps/gen_future.py"; gen_future.py --mode ok',
                                simulation, self.fixture_script(workspace), cwd="/tmp")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(workspace))
            for mode, code in (("error", 1), ("interrupt", 130), ("invalid", 2)):
                result = self.shell('source "$1/setup.bash"; gen_future.py --mode "$2"',
                                    simulation, mode, cwd="/tmp")
                self.assertEqual(result.returncode, code, result.stderr)
                if mode == "error":
                    self.assertIn("[gen_future] ERROR: requested failure", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

    def fixture_script(self, workspace):
        path = workspace / "fixture.py"
        path.write_text('''#!/usr/bin/env python3
import argparse
import sys
from _cli import WORKSPACE_ROOT, run_cli
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("ok", "error", "interrupt"), required=True)
    args = parser.parse_args()
    if args.mode == "error":
        raise RuntimeError("requested failure")
    if args.mode == "interrupt":
        raise KeyboardInterrupt
    print(WORKSPACE_ROOT)
    return 0
if __name__ == "__main__":
    sys.exit(run_cli(main))
''')
        return path


if __name__ == "__main__":
    unittest.main()
