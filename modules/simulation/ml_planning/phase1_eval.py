"""Deterministic held-out surrogate evaluation, separate from native acceptance."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from env import VectorEnv, DT
from verify_policy import actor


def evaluate(weights, seed=20261011, episodes=128, runtime_nudge=False):
    env = VectorEnv(episodes, "interaction", seed, runtime_nudge=runtime_nudge)
    finished = np.zeros(episodes, dtype=bool)
    kinds = env.episode_kind.copy()
    returns = np.zeros(episodes)
    peak_jerk = np.zeros(episodes)
    accel_square = np.zeros(episodes)
    samples = np.zeros(episodes)
    results = []
    for _ in range(1100):
        speed, previous_accel = env.speed.copy(), env.last_accel.copy()
        actions = actor(weights, env.obs())
        _, reward, done, info = env.step(actions)
        live = ~finished
        returns[live] += reward[live]
        # Terminating step resets env state; omit it from comfort statistics.
        valid = live & ~done
        acceleration = (env.speed-speed)/DT
        peak_jerk[valid] = np.maximum(peak_jerk[valid], abs((acceleration-previous_accel)/DT)[valid])
        accel_square[valid] += acceleration[valid]**2
        samples[valid] += 1
        for row in info:
            i = row["index"]
            if finished[i]:
                continue
            finished[i] = True
            results.append(dict(index=i, kind=str(kinds[i]), success=row["success"],
                                collision=row["collision"], offroad=row["offroad"],
                                return_value=float(returns[i]), max_jerk_mps3=float(peak_jerk[i]),
                                rms_acceleration_mps2=float(np.sqrt(accel_square[i]/max(1,samples[i])))))
        if finished.all():
            break
    if not finished.all():
        raise RuntimeError("Incomplete held-out episodes")
    groups = {}
    for kind in ["all", "mixed", "nudge", "meeting", "meeting_nudge"]:
        rows = [r for r in results if kind == "all" or r["kind"] == kind]
        groups[kind] = dict(episodes=len(rows), success=sum(r["success"] for r in rows),
                            collision=sum(r["collision"] for r in rows), offroad=sum(r["offroad"] for r in rows))
    return dict(weights=str(weights), sha256=hashlib.sha256(weights.read_bytes()).hexdigest(),
                seed=seed, runtime_nudge=runtime_nudge,
                scope="actor / Frenet surrogate / no geometric safety search; optional deployed nudge speed cap",
                groups=groups, results=results)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--weights",type=Path,action="append",required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--episodes",type=int,default=128)
    p.add_argument("--seed",type=int,default=20261011)
    p.add_argument("--runtime-nudge",action="store_true")
    a=p.parse_args()
    if a.episodes <= 0:
        p.error("episodes must be positive")
    results=[evaluate(w,a.seed,a.episodes,a.runtime_nudge) for w in a.weights]
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(results,indent=2)+"\n")
    for r in results:
        print(json.dumps({k:r[k] for k in ("weights","groups")}),flush=True)


if __name__=="__main__":
    main()
