"""Submit a real ML task through Web Monitor and verify its frozen pipeline."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:9090")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[3]
    from model_selection import read_model_selection
    ml = Path(__file__).resolve().parent
    selected_model = read_model_selection(ml / "conf/ml_planning.pb.txt", ml / "models")
    assert selected_model["version"] == "v5", selected_model
    flags = root / "modules/common/data/global_flagfile.txt"
    before = hashlib.sha256(flags.read_bytes()).hexdigest()
    profile = (root / "profiles/current").readlink()

    def request(body):
        req = urllib.request.Request(args.url + "/api/sim", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.load(response)
        assert result["status"] == "ok", result
        return result

    catalog = request({"action": "catalog"})["catalog"]
    source = next(p for p in catalog["worlds"] if p.endswith("/1haolou/turn_obstacle.worldsim.scenario.json"))
    config = {"kind": "world", "source": source, "modules": ["ROUTING", "ML_PLANNING"], "repeat": 2}
    job_id = request({"action": "enqueue", "config": config})["id"]
    (args.out / "job-id.txt").write_text(job_id)
    start = time.monotonic()
    while True:
        job = next(j for j in request({"action": "list"})["jobs"] if j["id"] == job_id)
        if job["stage"] in ("completed", "failed", "cancelled", "interrupted"):
            break
        assert time.monotonic() - start < 120, job
        time.sleep(.25)
    (args.out / "job.json").write_text(json.dumps(job, indent=2))
    assert job["stage"] == "completed", job.get("error", job)
    assert job["analysis"]["determinism"] == "PASS", job["analysis"]
    assert job["analysis"]["valid_planning_frames"] == 601
    assert job["analysis"]["ml_estop_frames"] == 0
    assert job["analysis"]["ego_displacement_m"] > 15
    assert hashlib.sha256(flags.read_bytes()).hexdigest() == before
    assert (root / "profiles/current").readlink() == profile
    assert job["effective_configuration"]["configuration_scope"] == "task"
    task = Path(job["outputs"][0]).parent
    manifest = json.loads(Path(job["analysis"]["manifest"]).read_text())
    model = job["effective_configuration"]["ml_planning_model"]
    assert model["version"] == "v5", model
    assert model["sha256"] == selected_model["sha256"], model
    frozen_weights = Path(model["frozen_weights"])
    assert frozen_weights.parent.name == model["version"]
    assert hashlib.sha256(frozen_weights.read_bytes()).hexdigest() == model["sha256"]
    assert any(p.endswith(f'models/{model["version"]}/unified.weights') for p in manifest["sha256"])
    assert 'runtime_modules: "ML_PLANNING"' not in (task / "task.pb.txt").read_text()
    assert 'runtime_modules: "PLANNING"' in (task / "task.pb.txt").read_text()
    from evaluate_1haolou import evaluate
    source = next((task.parent / "input").glob("*.json"))
    metrics = evaluate(Path(job["outputs"][0]), source, task / "policy.csv")
    assert metrics["passed"], metrics
    (args.out / "metrics.json").write_text(json.dumps(metrics, indent=2))
    print(f"PASS: ML task {job_id}; repeat exact; actor, DAG, map snapshots; workspace unchanged")


if __name__ == "__main__":
    main()
