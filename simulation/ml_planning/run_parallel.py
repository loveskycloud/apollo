"""Isolated, bounded parallel REAL WorldSim processes with terminal validation.

Does not mutate profiles/current, global_flagfile or the Web Monitor FIFO queue.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from fixtures import scenario

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def prepare(run, kind, offset, assets, model, scene=None):
    run.mkdir(parents=True)
    (run / "log").mkdir()
    map_dir, vehicle = assets
    (run / "scenario.json").write_text(json.dumps(scene if scene is not None else scenario(kind, offset), indent=2))
    # Experiment CLI inputs become a frozen versioned model, just like queue
    # jobs; the native component never reads a weight environment variable.
    weights = run / "models/v0-candidate/unified.weights"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(model.read_bytes())
    (run / "conf").mkdir()
    # Freeze the same planner settings used by queued simulation and the car;
    # only the experiment's versioned weight location differs.
    from google.protobuf import text_format
    from modules.simulation.ml_planning.proto.ml_planning_config_pb2 import MLPlanningConfig
    config = text_format.Parse((HERE / "conf/ml_planning.pb.txt").read_text(), MLPlanningConfig())
    config.model_version = "v0-candidate"
    (run / "conf/ml_planning.pb.txt").write_text(text_format.MessageToString(config))
    routing_config = "/opt/apollo/neo/share/modules/routing/conf/routing_config.pb.txt"
    (run / "routing.flags").write_text("--use_road_id=true\n--enable_change_lane_in_result=true\n"
                                      + "--routing_conf_file=" + routing_config + "\n")
    (run / "routing.dag").write_text(f'''module_config {{
  module_library: "/opt/apollo/neo/lib/modules/routing/librouting_component.so"
  components {{ class_name: "RoutingComponent" config {{
    name: "routing" config_file_path: "{routing_config}"
    flag_file_path: "{run / 'routing.flags'}"
    readers {{ channel: "/apollo/raw_routing_request" }}
  }} }}
}}
''')
    (run / "planning.dag").write_text(f'''module_config {{
  module_library: "/opt/apollo/neo/lib/modules/simulation/ml_planning/libml_planning.so"
  components {{ class_name: "MLPlanning" config {{
    name: "ml_planning" config_file_path: "{run / 'conf/ml_planning.pb.txt'}"
    readers {{ channel: "/apollo/perception/obstacles" }}
  }} }}
}}
''')
    fields = {"scenario_id": run.name, "task_dir": str(run), "input_kind": None,
              "world_scenario_path": str(run / "scenario.json"), "world_start_ns": 1000000000,
              "step_ms": 10, "ego_model": "perfect_planning", "random_seed": 7,
              "map_dir": str(map_dir), "vehicle_config_path": str(vehicle),
              "output_record_path": str(run / "simulation.record"),
              "progress_path": str(run / "progress.json"),
              "cyber_conf_path": str(ROOT / "simulation/simulator/conf/cyber_sim.pb.conf")}
    lines = [f"{k}: {json.dumps(v)}" for k, v in fields.items() if v is not None]
    lines += ['input_kind: WORLD', 'runtime_modules: "ROUTING"', 'runtime_modules: "PLANNING"',
              f'dag_paths: "{run / "routing.dag"}"', f'dag_paths: "{run / "planning.dag"}"', "channel_policy {"]
    inputs = ["/apollo/canbus/chassis", "/apollo/localization/pose", "/apollo/perception/obstacles",
              "/apollo/raw_routing_request", "/apollo/planning/command"]
    lines += [f'  inject_channels: "{c}"' for c in inputs]
    lines += [f'  record_channels: "{c}"' for c in inputs + ["/apollo/planning", "/apollo/raw_routing_response", "/apollo/planning/command_status"]]
    lines += ['  suppress_channels: "/apollo/planning"', '}']
    (run / "task.pb.txt").write_text("\n".join(lines) + "\n")
    return {"directory": str(run), "kind": kind, "obstacle_offset": offset,
            "policy_sha256": hashlib.sha256(weights.read_bytes()).hexdigest()}


def execute(metadata, timeout):
    run = Path(metadata["directory"])
    env = os.environ.copy()
    env.pop("ML_PLANNING_WEIGHTS", None)
    env.update(ML_PLANNING_TRACE=str(run / "policy.csv"), GLOG_log_dir=str(run / "log"),
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", CYBER_IP="127.0.0.1",
               SIM_PARENT_PID=str(os.getpid()))
    env.pop("SIM_OUTPUT_RECORD", None)
    started = time.time()
    with (run / "runtime.log").open("w") as log:
        child = subprocess.Popen(["/opt/apollo/neo/bin/simulator_main", "--task_dir=" + str(run)],
                                 cwd=run, env=env, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        metadata.update(pid=child.pid, started_unix=started)
        try:
            code = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGTERM)
            try: child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            code = -1
    metadata.update(returncode=code, ended_unix=time.time(), wall_s=time.time() - started)
    evaluation_start = time.monotonic()
    if code == 0:
        try:
            if metadata.get("evaluator") == "1haolou":
                from evaluate_1haolou import evaluate
            else:
                from evaluate import evaluate
            records = sorted(run.glob("simulation.record*"))
            if len(records) != 1:
                raise ValueError(f"Expected one record, got {len(records)}")
            metadata["metrics"] = evaluate(records[0], run / "scenario.json", run / "policy.csv")
        except Exception as exc:
            metadata["evaluation_error"] = str(exc)
    metadata["evaluation_s"] = time.monotonic() - evaluation_start
    metadata["passed"] = code == 0 and metadata.get("metrics", {}).get("passed", False)
    (run / "result.json").write_text(json.dumps(metadata, indent=2))
    return metadata


def main():
    # The public runner now always uses the unified model and real 1haolou map.
    from run_1haolou import main as run_suite
    return run_suite()


if __name__ == "__main__":
    raise SystemExit(main())
