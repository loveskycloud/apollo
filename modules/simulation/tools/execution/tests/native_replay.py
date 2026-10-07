#!/usr/bin/env python3
"""Verify actual CLI output records through the packaged Web Monitor replay APIs."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--verifier-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    acceptance = json.loads(args.summary.read_text())
    if acceptance['status'] != 'PASS':
        raise RuntimeError('Native acceptance must pass before replay')
    spec = importlib.util.spec_from_file_location('verifier', args.verifier_file)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    root = args.binary.resolve(strict=True)
    args.output.mkdir(parents=True, exist_ok=False)
    env = dict(verifier.package_environment(root), SIM_TASK_ROOT=str(args.output / 'viewer-jobs'))
    report = dict(status='RUNNING', viewer_binary=str(root), cases={})

    def save():
        (args.output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')

    with (args.output / 'web-monitor.log').open('w') as log:
        process = subprocess.Popen([str(root / 'bin/web_monitor_main'), '--grpc_host=127.0.0.1'],
            cwd='/tmp', env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 45
            while True:
                if process.poll() is not None:
                    raise RuntimeError('Web Monitor exited during startup')
                try:
                    with urllib.request.urlopen('http://127.0.0.1:9090/', timeout=1) as response:
                        if b'sim scope' not in response.read().lower():
                            raise RuntimeError('Unexpected viewer page')
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError('Web Monitor startup timeout')
                    time.sleep(.2)
            for name, case in acceptance['cases'].items():
                print('[replay] ' + name, flush=True)
                report['cases'][name] = verifier.verify_replay('http://127.0.0.1:9090',
                    Path(case['summary']['outputs'][0]), args.output, name)
                save()
                print('[replay] PASS ' + name, flush=True)
            report['status'] = 'PASS'
            save()
        except BaseException as error:
            report.update(status='FAIL', error=str(error))
            save()
            raise
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


if __name__ == '__main__':
    main()
