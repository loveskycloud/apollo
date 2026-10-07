"""Small runtime fixtures for unit tests; real Apollo acceptance is separate."""
import hashlib
import json
from pathlib import Path
import tarfile


def package(root, tag='local', blocking=False):
    root = Path(root)
    files = {
        'manifest.json': json.dumps(dict(format=4, profile=tag)),
        'bin/simulator_main': '#!/bin/sh\nexit 0\n# ' + tag,
        'lib/runtime/libtest.so': tag,
        'setup.bash': '''export APOLLO_ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export APOLLO_DISTRIBUTION_HOME="$APOLLO_ROOT_DIR"
export APOLLO_WORKSPACE="$APOLLO_ROOT_DIR"
export SIMULATOR_BINARY="$APOLLO_ROOT_DIR/bin/simulator_main"
export LD_LIBRARY_PATH="$APOLLO_ROOT_DIR/lib/runtime:/opt/image/lib"
export PYTHONPATH="$APOLLO_ROOT_DIR/python"
''',
        'modules/simulation/simulator/task_service.py': service_code(blocking),
        'modules/simulation/simulator/fixture_helper.py': 'SELECTED_HELPER = True\n',
        'data/map_data/test-map/base_map.bin': 'map',
        'data/map_data/test-map/sim_map.bin': 'map',
        'profiles/test-car/modules/common/data/vehicle_param.pb.txt': 'vehicle_param {}',
    }
    for name, value in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    (root / 'bin/simulator_main').chmod(0o755)
    checks = ''.join(hashlib.sha256((root / name).read_bytes()).hexdigest() + '  ' + name + '\n'
                     for name in sorted(files))
    (root / 'SHA256SUMS').write_text(checks)
    return root


def archive(root, destination):
    with tarfile.open(destination, 'w:gz') as stream:
        stream.add(root, arcname='binary')
    return destination


def scenario(path):
    path.write_text(json.dumps({'mapId': 'test-map', 'ego': {'vehicleProfile': 'test-car'}}))
    return path


def service_code(blocking):
    return '''import json, os, signal, subprocess, threading
from pathlib import Path
from fixture_helper import SELECTED_HELPER
assert SELECTED_HELPER
ROOT = Path(os.environ['APOLLO_WORKSPACE'])
DISTRIBUTION = Path(os.environ['APOLLO_DISTRIBUTION_HOME'])
INPUT_ROOTS = []
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}
def _dedupe_dirs(paths): return list(paths)
class TaskService:
 def __init__(self, state_dir, workers):
  self.root=Path(state_dir); self.root.mkdir(parents=True)
  self.lock=threading.RLock(); self.jobs={}; self.child=None
 def request(self, request):
  config=request['config']; job_dir=self.root/'test-job'; job_dir.mkdir()
  record=job_dir/'output.record'
  record.write_text(json.dumps({'distribution':str(DISTRIBUTION), 'ld':os.environ['LD_LIBRARY_PATH'],
   'tag':json.loads((DISTRIBUTION/'manifest.json').read_text())['profile']}))
  (job_dir/'manifest.json').write_text(json.dumps({'runtime_binary':str(DISTRIBUTION/'bin/simulator_main'),
   'runtime_libraries':{str(DISTRIBUTION/'lib/runtime/libtest.so'):'test'}}))
  self.jobs['test-job']={'id':'test-job', 'stage':'completed', 'execution':{'status':'PASS'},
   'outputs':[str(record)], 'config':config, 'analysis':{'topic_message_counts':{'/test':1},
   'missing_module_outputs':[], 'determinism':'not_tested'}}
''' + ('''  self.child=subprocess.Popen(['/usr/bin/python3','-c','import time; time.sleep(60)'],start_new_session=True)
  (self.root/'child.pid').write_text(str(self.child.pid))
  self.jobs['test-job']['stage']='simulation_running'
''' if blocking else '') + '''  return {'id':'test-job'}
 def close(self):
  if self.child and self.child.poll() is None:
   os.killpg(self.child.pid,signal.SIGTERM); self.child.wait(timeout=5)
   (self.root/'child.stopped').write_text(str(self.child.returncode))
'''
