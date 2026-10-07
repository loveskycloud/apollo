#!/usr/bin/env python3
"""Read a generated task JSON from stdin and execute its selected binary."""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import sys

from _cli import WORKSPACE_ROOT, run_cli
from modules.simulation.tools.execution.launcher import LAUNCHERS, run_simulation
from modules.simulation.tools.execution.task import SimulationTask


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-c', '--cluster', choices=tuple(LAUNCHERS), default='local')
    parser.add_argument('--task-file', type=Path, help='Read a saved task document instead of stdin')
    parser.add_argument('--output-root', type=Path, default=WORKSPACE_ROOT / 'data/simulation/cli')
    parser.add_argument('--wall-timeout', type=float, help='Total launcher deadline in seconds')
    return parser


def main(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)
    with args.task_file.open() if args.task_file is not None else nullcontext(sys.stdin) as stream:
        document = stream.read(65537)
    if not document.strip():
        raise ValueError('No task received; check gen_simulation_task.py stderr and enable pipefail')
    if len(document) > 65536:
        raise ValueError('Task document exceeds 64 KiB')
    task = SimulationTask.from_dict(json.loads(document))
    result = run_simulation(task, task.binary, backend=args.cluster,
                            output_root=args.output_root, wall_timeout=args.wall_timeout)
    print(json.dumps(result.summary(), ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    sys.exit(run_cli(main))
