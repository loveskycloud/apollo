"""Check exported Python/C++ actor inference parity on real traces."""
import argparse
import csv
import json
from pathlib import Path

import numpy as np
from interaction_observation import lateral_indices


def raw_actor(path, observations):
    tokens = Path(path).read_text().split()
    if tokens[:5] not in (["MLP_V2", "16", "64", "64", "2"], ["MLP_V3", "48", "64", "64", "2"]):
        raise ValueError("Invalid unified actor format")
    values = np.array([float(x) for x in tokens[5:]])
    offset, x = 0, observations[..., :int(tokens[1])]
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
    mirror=obs.copy();mirror[:,lateral_indices(obs.shape[-1])] *= -1
    mirror[:,[6,7]]=obs[:,[7,6]]
    a,b=raw_actor(path,obs),raw_actor(path,mirror)
    return np.stack([(a[:,0]-b[:,0])/2,(a[:,1]+b[:,1])/2],-1)


def verify_trace(weights, trace):
    with Path(trace).open() as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError('Empty ML policy trace: '+str(trace))
    dim = int(Path(weights).read_text().split()[1])
    obs = np.array([[float(row[f'obs{i}']) for i in range(dim)] for row in rows])
    actual = np.array([[float(row['action_l']), float(row['action_v'])] for row in rows])
    error = float(np.abs(actor(weights, obs)-actual).max())
    return {'observations': len(rows), 'actor_max_abs_error': error,
            'passed': bool(np.isfinite(error) and error < 1e-8)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("suite", type=Path, nargs='?')
    parser.add_argument('--task-dir', type=Path, help='Verify all repeats of a frozen Web Monitor task')
    args = parser.parse_args()
    if bool(args.suite) == bool(args.task_dir):
        parser.error('Choose exactly one of suite or --task-dir')
    if args.task_dir:
        config = json.loads((args.task_dir/'configuration.json').read_text())
        weights = Path(config['ml_planning_model']['frozen_weights'])
        traces = sorted(args.task_dir.glob('run-*/policy.csv'))
        task = json.loads((args.task_dir/'manifest.json').read_text())
        if len(traces) != task['config']['repeat']:
            raise ValueError('Missing ML repeat traces')
        results = [dict(verify_trace(weights, trace), instance=trace.parent.name) for trace in traces]
        report = {'passed': all(r['passed'] for r in results), 'results': results, 'tolerance': 1e-8}
        (args.task_dir/'policy-parity.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report, indent=2))
        return 0 if report['passed'] else 1
    results = []
    for result in json.loads((args.suite / "summary.json").read_text()):
        run = Path(result["directory"])
        rows = list(csv.DictReader((run / "policy.csv").open()))
        weights = run/"models/v0-candidate/unified.weights"
        if not weights.is_file():
            weights = run/"policy.weights"
        dim = int(weights.read_text().split()[1])
        obs=np.array([[float(row[f"obs{i}"]) for i in range(dim)] for row in rows])
        actual=np.array([[float(row["action_l"]),float(row["action_v"])] for row in rows])
        error=float(np.abs(actor(weights,obs)-actual).max())
        results.append({"instance":run.name,"observations":len(rows),"actor_max_abs_error":error,"passed":error<1e-8})
    report = {"passed": all(r["passed"] for r in results), "results": results,
              "tolerance": 1e-8, "note": "Same exported actor, reflection symmetry, exact recorded observations; 15-digit CSV"}
    (args.suite / "policy-parity.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
