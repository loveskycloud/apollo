#!/usr/bin/env python3
"""Real module integration (not mocked); run inside the Apollo container."""
import argparse
import json
from pathlib import Path
import time

from task_service import ROOT, TaskService, TERMINAL


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--kind", default="bag")
    parser.add_argument("--modules", default="PREDICTION,PLANNING,CONTROL")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--profile", default="")
    parser.add_argument("--model", default="perfect_planning")
    args = parser.parse_args()
    service = TaskService(args.state_dir)
    config = {"kind": args.kind, "source": args.source,
        "map": str(ROOT / "data/bag/data_with_map/extracted/od_hq_map"),
        "vehicle": str(ROOT / "data/bag/data_with_map/extracted/Jiyu_01/modules/common/data/vehicle_param.pb.txt"),
        "profile": args.profile,
        "model": args.model,
        "modules": args.modules.split(","), "repeat": args.repeat, "timeout_s": 120}
    try:
        result = service.request({"action": "enqueue", "config": config})
        last = None
        while True:
            job = next(j for j in service.request({"action": "list"})["jobs"] if j["id"] == result["id"])
            if job["stage"] != last:
                print(job["id"], job["stage"], flush=True)
                last = job["stage"]
            if last in TERMINAL:
                print(json.dumps(job, indent=2), flush=True)
                assert last == "completed", job.get("error")
                if args.kind == "world":
                    assert job["analysis"]["ego_displacement_m"] > .1, "Test route ego never moved"
                    from task_service import messages
                    from modules.common_msgs.perception_msgs.perception_obstacle_pb2 import PerceptionObstacles
                    samples = [(t, len(PerceptionObstacles.FromString(b).perception_obstacle))
                               for c, t, b in messages(job["outputs"][0]) if c == "/apollo/perception/obstacles"]
                    assert all(n == (1 if 2000000000 <= t < 3000000000 else 0) for t, n in samples), samples
                break
            time.sleep(.5)
    finally:
        service.close()


if __name__ == "__main__":
    main()
