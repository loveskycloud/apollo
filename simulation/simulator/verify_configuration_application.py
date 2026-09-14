"""Read-only integration assertions for completed /api/sim tasks (in container)."""
import argparse
import json
from pathlib import Path
import urllib.request

from configuration_tools import GLOBAL_FLAGS, VEHICLE_CONFIG, vehicle_geometry


def active_flags(path):
    return dict(line[2:].split("=", 1) for line in path.read_text().splitlines()
                if line.startswith("--") and "=" in line)


def verify(job):
    assert job["stage"] == "completed", (job["id"], job.get("error"))
    config = job["config"]
    effective = job["effective_configuration"]
    profile = Path(config["profile"])
    geometry = vehicle_geometry(profile / VEHICLE_CONFIG)
    assert all(effective[key] == value for key, value in geometry.items())
    assert job["analysis"]["effective_configuration"] == effective
    runtime_flags = Path(effective["runtime_global_flagfile"])
    job_dir = runtime_flags.parents[4]
    runtime = job_dir / ".runtime"
    assert json.loads((job_dir / "configuration.json").read_text()) == effective
    flags = active_flags(runtime_flags)
    assert flags["map_dir"] == effective["runtime_map_dir"]
    assert flags["vehicle_config_path"] == effective["runtime_vehicle_config_path"]
    assert float(flags["half_vehicle_width"]) == geometry["half_vehicle_width"]
    vehicle_bytes = (profile / VEHICLE_CONFIG).read_bytes()
    for key in ("task_vehicle_config_path", "runtime_vehicle_config_path"):
        assert Path(effective[key]).read_bytes() == vehicle_bytes
    checked = 0
    for source in profile.rglob("*"):
        relative = source.relative_to(profile)
        if source.is_file() and relative.parts[0] in ("modules", "cyber") and relative != GLOBAL_FLAGS:
            assert (runtime / relative).read_bytes() == source.read_bytes(), relative
            checked += 1
    for source in Path(config["map"]).glob("base_map.*"):
        assert (Path(effective["runtime_map_dir"]) / source.name).read_bytes() == source.read_bytes()
    run = job_dir / "run-1"
    log = (run / "runtime.log").read_text()
    verification = "Simulation environment verified:"
    assert log.index(verification) < log.index("EgoCar HDMap reloaded")
    assert f"half_vehicle_width={geometry['half_vehicle_width']}" in log
    assert "Simulation environment overwritten" not in log
    overrides = list((run / "sim_flags").glob("*.flags"))
    assert len(overrides) == len(config["modules"])
    for override in overrides:
        flags = active_flags(override)
        assert flags["map_dir"] == effective["runtime_map_dir"]
        assert flags["vehicle_config_path"] == effective["task_vehicle_config_path"]
        assert float(flags["half_vehicle_width"]) == geometry["half_vehicle_width"]
    return {"id": job["id"], "kind": config["kind"], "configuration_checks": "PASS",
            "checked_profile_runtime_files": checked, "module_overrides": len(overrides),
            "effective_configuration": effective, "analysis": job["analysis"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="+")
    args = parser.parse_args()
    request = urllib.request.Request("http://127.0.0.1:9090/api/sim",
        data=b'{"action":"list"}', headers={"Content-Type": "application/json"})
    jobs = {job["id"]: job for job in json.load(urllib.request.urlopen(request))["jobs"]}
    results = [verify(jobs[job_id]) for job_id in args.ids]
    root = Path("/apollo_workspace")
    last = results[-1]["effective_configuration"]
    assert (root / "profiles/current").resolve() == Path(last["profile"])
    assert (root / VEHICLE_CONFIG).resolve() == Path(last["vehicle_config_path"])
    flags = active_flags(root / GLOBAL_FLAGS)
    assert flags["map_dir"] == last["map_dir"]
    assert flags["vehicle_config_path"] == last["vehicle_config_path"]
    assert float(flags["half_vehicle_width"]) == last["half_vehicle_width"]
    print(json.dumps({"workspace_checks": "PASS", "jobs": results}, indent=2))
