#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Launch simulator_main with optional repeat/compare."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from bag_diff import compare


def compare_runs(task_dir):
    output_dir = os.path.join(task_dir, "output")
    left = os.path.join(output_dir, "sim_run_0.record")
    right = os.path.join(output_dir, "sim_run_1.record")
    compare_path = os.path.join(output_dir, "compare.json")
    def segment(path):
        candidates = [Path(path)] if Path(path).is_file() else sorted(Path(output_dir).glob(Path(path).name + ".*"))
        if len(candidates) != 1:
            raise ValueError(f"Expected one completed record segment for {path}, got {len(candidates)}")
        return candidates[0]
    data = compare(segment(left), segment(right), algorithm=True)
    Path(compare_path).write_text(json.dumps(data, indent=2))
    return 0 if data.get("result") == "PASS" else 1


def run_sim(task_dir, run_index):
    output_dir = os.path.join(task_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    env = os.environ.copy()
    env["SIM_OUTPUT_RECORD"] = os.path.join(
        output_dir, f"sim_run_{run_index}.record")
    cmd = [
        "/opt/apollo/neo/bin/simulator_main",
        f"--task_dir={task_dir}",
    ]
    return subprocess.call(cmd, env=env)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-dir", required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--compare", action="store_true")
    args = parser.parse_args()
    args.task_dir = str(Path(args.task_dir).resolve(strict=True))
    if args.repeat < 1 or (args.compare and args.repeat != 2):
        parser.error("repeat must be positive; --compare requires --repeat 2")
    codes = []
    for i in range(args.repeat):
        codes.append(run_sim(args.task_dir, i))
    if any(c != 0 for c in codes):
        return 1
    if args.compare:
        return compare_runs(args.task_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
