"""Seeded held-out actor variations, evaluated in real isolated WorldSim processes."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import copy
import json
import math
import multiprocessing
from pathlib import Path
import random

from fixtures_1haolou import create, MAP, VEHICLE
from run_parallel import prepare, execute

HERE = Path(__file__).resolve().parent
SCENES = ["pedestrian_left", "pedestrian_right", "pedestrian_sudden",
          "crossing_vehicle", "turn_obstacle", "turn_obstacle_right",
          "static_slalom", "moving_lead", "user_overtake"]


def run(item):
    return execute(item, 120)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--weights", type=Path, default=HERE / "models/v5/unified.weights")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--instances", type=int, default=32)
    parser.add_argument("--seed", type=int, default=20260929)
    args = parser.parse_args()
    if not 1 <= args.workers <= 32 or not 1 <= args.instances <= 1000:
        parser.error("workers must be 1..32; instances must be 1..1000")
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    rng = random.Random(args.seed)
    sources = create()
    jobs = []
    for i in range(args.instances):
        kind = SCENES[i % len(SCENES)]
        scene = copy.deepcopy(json.loads((sources / f"{kind}.worldsim.scenario.json").read_text()))
        changes = []
        if kind.startswith("pedestrian") or kind == "crossing_vehicle":
            distance = rng.uniform(2.4, 3.8)
            speed = rng.uniform(.45, .85) if kind.startswith("pedestrian") else rng.uniform(.3, .55)
            scene["triggers"][0]["distance"] = distance
            scene["agents"][0]["speed"] = speed
            changes.append({"trigger_distance": distance, "speed": speed})
        else:
            for agent in scene["agents"]:
                along, lateral = rng.uniform(-1, 1), rng.uniform(-.04, .04)
                heading = agent["heading"]
                dx = along * math.cos(heading) - lateral * math.sin(heading)
                dy = along * math.sin(heading) + lateral * math.cos(heading)
                positions = [agent["position"]]
                for route in agent.get("routes", []):
                    positions.extend(point["position"] for point in route["waypoints"])
                for point in positions:
                    point["x"] += dx
                    point["y"] += dy
                change = {"agent": agent["id"], "along": along, "lateral": lateral}
                if kind in ("moving_lead", "user_overtake"):
                    agent["speed"] = rng.uniform(.25, .45)
                    change["speed"] = agent["speed"]
                    # WorldSim route waypoint speeds override the actor default.
                    for route in agent.get("routes", []):
                        for point in route["waypoints"]:
                            if "speed" in point:
                                point["speed"] = agent["speed"]
                if kind == "user_overtake":
                    agent["size"]["x"] = rng.uniform(.8, 1.4)
                    agent["size"]["y"] = rng.uniform(.3, .45)
                    change["length"] = agent["size"]["x"]
                    change["width"] = agent["size"]["y"]
                changes.append(change)
        name = f"{i:03d}-{kind}"
        scene["id"] = f"stress-{args.seed}-{name}"
        scene["name"] = scene["id"]
        item = prepare(args.out / name, kind, 0, (MAP, VEHICLE), args.weights.resolve(), scene)
        item.update(evaluator="1haolou", seed=args.seed, variations=changes)
        jobs.append(item)
    with ProcessPoolExecutor(max_workers=args.workers,
                             mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(run, jobs))
    (args.out / "summary.json").write_text(json.dumps(results, indent=2))
    for result in results:
        print(Path(result["directory"]).name, result["passed"],
              result.get("metrics", result.get("evaluation_error")), flush=True)
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
