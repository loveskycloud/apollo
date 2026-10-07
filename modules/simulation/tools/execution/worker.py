#!/usr/bin/env python3
"""Run the selected distribution's TaskService in an isolated Python process."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time
import traceback

from checks import verify_result


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    document = json.loads(args.task.read_text())
    binary_root = Path(document['binary']['path'])
    if Path(os.environ['APOLLO_DISTRIBUTION_HOME']) != binary_root:
        raise ValueError('Worker environment points at another binary')
    service_file = binary_root / 'modules/simulation/simulator/task_service.py'
    # TaskService is normally executed as a script and imports sibling helpers.
    # Reproduce that import path using only the selected distribution.
    sys.path.insert(0, str(service_file.parent))
    spec = importlib.util.spec_from_file_location('task_service', service_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules['task_service'] = module
    spec.loader.exec_module(module)
    if module.DISTRIBUTION.resolve() != binary_root or module.ROOT.resolve() != binary_root:
        raise ValueError('Selected task service points at another distribution/workspace')
    # CLI inputs are explicitly provided paths; extend only this private
    # process, without changing the web service's input-root policy.
    config = copy.deepcopy(document['config'])
    roots = [args.run_dir, Path(config['source']).parent, Path(config['map']), Path(config['profile'])]
    module.INPUT_ROOTS = module._dedupe_dirs([*module.INPUT_ROOTS, *roots])
    frozen_source = args.run_dir / 'input' / Path(config['source']).name
    frozen_source.parent.mkdir()
    import shutil
    shutil.copyfile(config['source'], frozen_source)
    if hashlib.sha256(frozen_source.read_bytes()).hexdigest() != document['input']['sha256']:
        raise RuntimeError('Input changed before worker preparation')
    config['source'] = str(frozen_source)
    result = dict(status='FAIL', binary=document['binary'], job=None, checks={})
    service = module.TaskService(args.run_dir / 'jobs', workers=1)

    def interrupt(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, interrupt)
    try:
        queued = service.request({'action': 'enqueue', 'config': config})
        identity = queued['id']
        stage = None
        while True:
            with service.lock:
                job = copy.deepcopy(service.jobs[identity])
            if job['stage'] != stage:
                stage = job['stage']
                print(f'[simulation] {identity}: {stage}', flush=True)
            if stage in module.TERMINAL:
                result['job'] = job
                break
            time.sleep(.2)
        job_dir = service.root / identity
        result['checks'] = verify_result(module, job, binary_root, os.environ.copy(), job_dir)
        result['status'] = 'PASS'
        return 0
    except Exception as error:
        result['error'] = str(error)
        traceback.print_exc()
        return 1
    finally:
        service.close()
        atomic_json(args.run_dir / 'result.json', result)


if __name__ == '__main__':
    sys.exit(main())
