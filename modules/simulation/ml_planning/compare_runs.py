"""Verify parallel/serial equivalence and export the measured benchmark."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "simulation/logsim/tools"))
from bag_diff import compare


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("serial", type=Path)
    parser.add_argument("parallel", type=Path)
    args = parser.parse_args()
    serial = json.loads((args.serial / "summary.json").read_text())
    parallel = json.loads((args.parallel / "summary.json").read_text())
    a = sorted(serial["results"], key=lambda r: r["directory"])
    b = sorted(parallel["results"], key=lambda r: r["directory"])
    if len(a) != len(b):
        raise ValueError("Different task counts")
    differences = []
    for left, right in zip(a, b):
        if any(left[k] != right[k] for k in ("kind", "obstacle_offset", "policy_sha256")):
            raise ValueError("Different task or policy inputs")
        # Raw protobuf payloads, record timestamps and ordering; no tolerance
        # and no wall-time field exclusions are needed by this PPO component.
        diff = compare(left["metrics"]["record"], right["metrics"]["record"])
        differences.append(diff)
    result = {"passed": serial["passed"] and parallel["passed"] and
              all(d["result"] == "PASS" for d in differences),
              "instances": len(a), "workers": parallel["workers"],
              "peak_simulator_processes": parallel["peak_simulator_processes"],
              "serial_end_to_end_s": serial["elapsed_s"],
              "parallel_end_to_end_s": parallel["elapsed_s"],
              "end_to_end_speedup": serial["elapsed_s"] / parallel["elapsed_s"],
              "scope": "Same 6 short tasks; includes startup, record writing and evaluation; one measurement, not a scaling guarantee",
              "comparisons": differences}
    output = args.parallel / "benchmark.json"
    output.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "comparisons"}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
