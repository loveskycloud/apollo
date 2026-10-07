#!/usr/bin/env python3
"""Write a simulation task JSON to stdout for `launch.py -c local`."""
import argparse
import json
from pathlib import Path
import sys

from _cli import WORKSPACE_ROOT, run_cli
from modules.simulation.tools.execution.binary import get_binary
from modules.simulation.tools.execution.task import generate_task, get_scenario


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-B', '--binary-id', default='-1', help='-1: current local binary; positive ID: download from service')
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('-f', '--file', type=Path, help='Scenario JSON / Cyber record path')
    inputs.add_argument('-i', '--input-id', help='Reserved scenario ID resolver; currently use -f')
    parser.add_argument('--local-binary', type=Path, help='Extracted portable binary directory or local tar.gz (-B -1 only)')
    parser.add_argument('--binary-service-url', help='Default: BINARY_SERVICE_URL')
    parser.add_argument('--binary-cache', type=Path, help='Default: SIMULATION_BINARY_CACHE or ~/.cache/apollo_simulation/binaries')
    parser.add_argument('--kind', choices=('world', 'bag'), default='world')
    parser.add_argument('--planner', choices=('pnc', 'ml'), default='pnc')
    parser.add_argument('--modules', help='Explicit comma-separated module names')
    parser.add_argument('--model', choices=('kinematic_control', 'perfect_planning'))
    parser.add_argument('--map-dir', type=Path)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--repeat', type=int, choices=(1, 2, 3), default=1)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--step-ms', type=int, choices=(1, 2, 5, 10), default=10)
    parser.add_argument('--timeout-s', type=int, default=600, help='Maximum simulation duration / native wall budget')
    return parser


def main(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)
    # Reject unresolved scenario IDs before downloading a potentially large binary.
    source = get_scenario(input_file=args.file, input_id=args.input_id)
    print('[gen_simulation_task] Resolving binary ' + args.binary_id, file=sys.stderr, flush=True)
    binary = get_binary(args.binary_id, local_path=args.local_binary,
                        service_url=args.binary_service_url, cache_dir=args.binary_cache,
                        workspace=WORKSPACE_ROOT)
    task = generate_task(binary, source, workspace=WORKSPACE_ROOT, kind=args.kind,
                         planner=args.planner, map_dir=args.map_dir, profile=args.profile,
                         modules=[m.strip() for m in args.modules.split(',')] if args.modules is not None else None,
                         model=args.model, repeat=args.repeat, seed=args.seed,
                         step_ms=args.step_ms, timeout_s=args.timeout_s)
    print(json.dumps(task.to_dict(), ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    sys.exit(run_cli(main))
