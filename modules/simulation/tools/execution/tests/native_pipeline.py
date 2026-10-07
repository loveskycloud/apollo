#!/usr/bin/env python3
"""Opt-in real WorldSim acceptance; run inside a clean Apollo image, no mocks of algorithms."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-binary', type=Path, required=True)
    parser.add_argument('--remote-archive', type=Path, required=True)
    parser.add_argument('--service-script', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume', action='store_true', help='Reuse already passed cases with unchanged binary/input fingerprints')
    args = parser.parse_args()
    root = args.local_binary.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=args.resume)
    scene = root / 'data/scenarios/ranger_mini_v3_1haolou_smoke.scenario.json'
    obstacle = root / 'modules/simulation/ml_planning/examples/1haolou/straight_centered.worldsim.scenario.json'
    report = dict(status='RUNNING', cases={}, service_url='http://127.0.0.1:8088')
    summary = output / 'summary.json'
    if args.resume:
        report = json.loads(summary.read_text())
        report['status'] = 'RUNNING'
        report.pop('error', None)
    remaining_remote = sum('downloaded-' + name not in report['cases'] for name in ('pnc', 'ml', 'ml-obstacle'))
    archive_requests = int(bool(remaining_remote) and not any((output / 'cache/123143').glob('*/binary')))

    def digest(path):
        value = hashlib.sha256()
        with Path(path).open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                value.update(chunk)
        return value.hexdigest()

    def save():
        summary.write_text(json.dumps(report, indent=2) + '\n')

    def stats():
        with urllib.request.urlopen(report['service_url'] + '/stats', timeout=5) as response:
            return json.load(response)

    with (output / 'mock-service.log').open('w') as service_log:
        server = subprocess.Popen(['/usr/bin/python3', str(args.service_script), '--binary-id', '123143',
            '--archive', str(args.remote_archive), '--port', '8088'], stdout=service_log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 90
            while True:
                if server.poll() is not None:
                    raise RuntimeError('Mock service failed; see mock-service.log')
                try:
                    stats()
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError('Mock service startup timeout')
                    time.sleep(.2)
            for identity, label in [('-1', 'local'), ('123143', 'downloaded')]:
                for name, planner, source in [('pnc', 'pnc', scene), ('ml', 'ml', scene),
                                              ('ml-obstacle', 'ml', obstacle)]:
                    key = label + '-' + name
                    if key in report['cases']:
                        passed = report['cases'][key]['summary']
                        saved_task = json.loads(Path(passed['result_path']).with_name('task.json').read_text())
                        selected = Path(passed['binary']['path'])
                        if (passed['status'] != 'PASS' or digest(selected / 'manifest.json') != passed['binary']['manifest_sha256'] or
                            digest(selected / 'bin/simulator_main') != passed['binary']['simulator_sha256'] or
                            digest(source) != saved_task['input']['sha256']):
                            raise RuntimeError('Previously passed case changed: ' + key)
                        print('[acceptance] reuse PASS ' + key, flush=True)
                        continue
                    print('[acceptance] ' + key, flush=True)
                    command = ('set -e -o pipefail; source ' + shlex.quote(str(root / 'setup.bash')) + '; '
                        'export SIMULATION_BINARY_CACHE=' + shlex.quote(str(output / 'cache')) + '; '
                        'export BINARY_SERVICE_URL=' + shlex.quote(report['service_url']) + '; '
                        'gen_simulation_task.py -B ' + identity + ' -f ' + shlex.quote(str(source)) +
                        ' --planner ' + planner + ' --repeat 2 --timeout-s 90 ' +
                        ('--profile ' + shlex.quote(str(root / 'profiles/ranger_mini_v3')) + ' ' if name == 'ml-obstacle' else '') + '| '
                        'launch.py -c local --output-root ' + shlex.quote(str(output / 'runs' / key)))
                    (output / (key + '.command.sh')).write_text(command + '\n')
                    with (output / (key + '.stdout.json')).open('w') as stdout, \
                         (output / (key + '.stderr.log')).open('w') as stderr:
                        result = subprocess.run(['bash', '--noprofile', '--norc', '-c', command],
                                                cwd='/tmp', stdout=stdout, stderr=stderr, timeout=900)
                    if result.returncode:
                        raise RuntimeError(f'{key} exited {result.returncode}; see {key}.stderr.log')
                    value = json.loads((output / (key + '.stdout.json')).read_text())
                    full = json.loads(Path(value['result_path']).read_text())
                    expected = 'local' if identity == '-1' else 'downloaded'
                    if value['status'] != 'PASS' or value['binary']['kind'] != expected or value['binary']['id'] != identity:
                        raise RuntimeError('Wrong selected binary/result: ' + key)
                    if label == 'local' and Path(value['binary']['path']) != root:
                        raise RuntimeError('Local path changed')
                    if label == 'downloaded' and not Path(value['binary']['path']).is_relative_to(output / 'cache'):
                        raise RuntimeError('Downloaded binary did not use cache')
                    if not all(v == 'PASS' for v in value['checks'].values()):
                        raise RuntimeError('Some checks did not pass: ' + key)
                    if len(value['outputs']) != 2:
                        raise RuntimeError('Missing repeated output records')
                    report['cases'][key] = dict(summary=value, analysis=full['job']['analysis'])
                    save()
                    print('[acceptance] PASS ' + key, flush=True)
                if label == 'local' and any(route.startswith('/binaries/') or route.startswith('/archives/') for route in stats()):
                    raise RuntimeError('Local binary unexpectedly contacted service')
            report['service_requests'] = stats()
            if (report['service_requests'].get('/binaries/123143', 0) != remaining_remote or
                report['service_requests'].get('/archives/123143/binary.tar.gz', 0) != archive_requests):
                raise RuntimeError('Cache did not reuse the downloaded archive')
            local = report['cases']['local-pnc']['summary']['binary']
            remote = report['cases']['downloaded-pnc']['summary']['binary']
            if local['simulator_sha256'] == remote['simulator_sha256']:
                raise RuntimeError('Acceptance needs two distinct native binaries')
            report['status'] = 'PASS'
            save()
            print('[acceptance] PASS all six pipelines / twelve native runs', flush=True)
        except BaseException as error:
            report.update(status='FAIL', error=str(error))
            save()
            raise
        finally:
            server.terminate()
            server.wait(timeout=10)


if __name__ == '__main__':
    main()
