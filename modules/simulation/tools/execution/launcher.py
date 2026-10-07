"""Execution backends; local now, an explicit extension point for a cluster."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from .binary import Binary
from .task import SimulationTask


@dataclass(frozen=True)
class SimulationResult:
    report_path: Path
    value: dict
    backend: str = 'local'

    def summary(self):
        job = self.value['job']
        return dict(status=self.value['status'], backend=self.backend, task_id=job['id'],
                    binary=self.value['binary'], result_path=str(self.report_path),
                    outputs=job['outputs'], checks=self.value['checks'])


class Launcher(ABC):
    @abstractmethod
    def check_available(self):
        """Fail before preparing a task if this backend is not configured."""

    @abstractmethod
    def run(self, task: SimulationTask, binary: Binary, run_dir: Path, wall_timeout: float) -> SimulationResult:
        """Execute the task with the supplied Binary, preserving failure evidence."""


def stop_worker(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


class LocalLauncher(Launcher):
    def check_available(self):
        return None

    def run(self, task, binary, run_dir, wall_timeout):
        env = binary.environment()
        worker = Path(__file__).with_name('worker.py')
        logfile = run_dir / 'launch.log'
        report = run_dir / 'result.json'
        with logfile.open('w') as output:
            process = subprocess.Popen(['/usr/bin/python3', str(worker), '--task', str(run_dir / 'task.json'),
                '--run-dir', str(run_dir)], cwd=binary.path, env=env,
                stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + wall_timeout
            try:
                with logfile.open() as log_reader:
                    while process.poll() is None:
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Simulation launch exceeded its wall timeout')
                        text = log_reader.read()
                        if text:
                            print(text, end='', file=sys.stderr, flush=True)
                        time.sleep(.2)
                    text = log_reader.read()
                    if text:
                        print(text, end='', file=sys.stderr, flush=True)
            except (KeyboardInterrupt, TimeoutError) as error:
                stop_worker(process)
                value = json.loads(report.read_text()) if report.is_file() else dict(binary=binary.to_dict(), job=None)
                value.update(status='CANCELLED' if isinstance(error, KeyboardInterrupt) else 'TIMEOUT',
                             error='Local launcher interrupted or timed out')
                report.write_text(json.dumps(value, indent=2) + '\n')
                raise
        if not report.is_file():
            raise RuntimeError(f'Simulation worker exited {process.returncode}; see {logfile}')
        value = json.loads(report.read_text())
        if process.returncode or value.get('status') != 'PASS':
            raise RuntimeError(f"Simulation failed: {value.get('error', process.returncode)}; see {report}")
        return SimulationResult(report, value)


class ClusterLauncher(Launcher):
    def check_available(self):
        raise RuntimeError('Cluster launcher is reserved and not configured; use -c local')

    def run(self, task, binary, run_dir, wall_timeout):
        raise RuntimeError('Cluster launcher is reserved and not configured; use -c local')


LAUNCHERS = {'local': LocalLauncher, 'cluster': ClusterLauncher}


def run_simulation(task: SimulationTask, binary: Binary, *, backend='local', output_root=None,
                   wall_timeout=None) -> SimulationResult:
    if backend not in LAUNCHERS:
        raise ValueError('Unknown launch backend: ' + backend)
    launcher = LAUNCHERS[backend]()
    launcher.check_available()
    if task.binary.to_dict() != binary.to_dict():
        raise ValueError('Task and run_simulation received different Binary objects')
    binary.verify_identity()
    task.verify_input()
    maximum = task.config['timeout_s']
    if wall_timeout is None:
        wall_timeout = max(600, task.config['repeat'] * (max(120, maximum * 1.5 + 60) + 180) + 60)
    if not math.isfinite(wall_timeout) or wall_timeout <= 0:
        raise ValueError('wall_timeout must be positive')
    root = Path(output_root or Path.cwd() / 'data/simulation/cli').expanduser().resolve()
    run_dir = root / uuid.uuid4().hex
    run_dir.mkdir(parents=True)
    (run_dir / 'task.json').write_text(json.dumps(task.to_dict(), indent=2) + '\n')
    print(f'[launch] binary {binary.binary_id}: {binary.path}', file=sys.stderr, flush=True)
    print(f'[launch] task output: {run_dir}', file=sys.stderr, flush=True)
    result = launcher.run(task, binary, run_dir, wall_timeout)
    print('[launch] PASS: ' + str(result.report_path), file=sys.stderr, flush=True)
    return result
