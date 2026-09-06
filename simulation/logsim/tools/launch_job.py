#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Launch simulator_main with optional repeat/compare."""

import argparse
import json
import os
import subprocess
import sys


def compare_runs(task_dir):
    output_dir = os.path.join(task_dir, "output")
    left = os.path.join(output_dir, "sim_run_0.record")
    right = os.path.join(output_dir, "sim_run_1.record")
    compare_path = os.path.join(output_dir, "compare.json")
    if not os.path.exists(left) or not os.path.exists(right):
        return 1
    cmd = [
        sys.executable,
        "simulation/logsim/tools/bag_diff.py",
        "--left", left,
        "--right", right,
        "--output", compare_path,
    ]
    if subprocess.call(cmd) != 0:
        return 1
    with open(compare_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return 0 if data.get("result") == "PASS" else 1


def run_sim(task_dir, run_index):
    output_dir = os.path.join(task_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    os.environ["SIM_OUTPUT_RECORD"] = os.path.join(
        output_dir, f"sim_run_{run_index}.record")
    cmd = [
        "/opt/apollo/neo/bin/logsim_main",
        f"--task_dir={task_dir}",
    ]
    return subprocess.call(cmd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-dir", required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--compare", action="store_true")
    args = parser.parse_args()
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
