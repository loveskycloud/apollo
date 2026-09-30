"""Check exported Python/C++ actor inference parity on real traces."""
import argparse
import csv
import json
from pathlib import Path

import numpy as np


def raw_actor(path, observations):
    tokens = Path(path).read_text().split()
    if tokens[:5] != ["MLP_V2", "16", "64", "64", "2"]:
        raise ValueError("Invalid unified actor format")
    values = np.array([float(x) for x in tokens[5:]])
    offset, x = 0, observations
    for width in (64, 64, 2):
        count = x.shape[-1] * width
        weights = values[offset:offset+count].reshape(width, x.shape[-1])
        offset += count
        bias = values[offset:offset+width]
        offset += width
        x = x @ weights.T + bias
        if width != 2:
            x = np.tanh(x)
    return x


def actor(path, obs):
    obs=obs.copy();obs[:,[9,10,11]]=abs(obs[:,[9,10,11]])
    obs[:,4]=np.where((obs[:,5]>.5)&(abs(obs[:,4])<.01),.01,obs[:,4])
    mirror=obs.copy();mirror[:,[0,1,4,15]] *= -1
    mirror[:,[6,7]]=obs[:,[7,6]]
    a,b=raw_actor(path,obs),raw_actor(path,mirror)
    return np.stack([(a[:,0]-b[:,0])/2,(a[:,1]+b[:,1])/2],-1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("suite", type=Path)
    args = parser.parse_args()
    results = []
    for result in json.loads((args.suite / "summary.json").read_text()):
        run = Path(result["directory"])
        rows = list(csv.DictReader((run / "policy.csv").open()))
        obs=np.array([[float(row[f"obs{i}"]) for i in range(16)] for row in rows])
        actual=np.array([[float(row["action_l"]),float(row["action_v"])] for row in rows])
        error=float(np.abs(actor(run/"policy.weights",obs)-actual).max())
        results.append({"instance":run.name,"observations":len(rows),"actor_max_abs_error":error,"passed":error<1e-8})
    report = {"passed": all(r["passed"] for r in results), "results": results,
              "tolerance": 1e-8, "note": "Same exported actor, reflection symmetry, exact recorded observations; 15-digit CSV"}
    (args.suite / "policy-parity.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
