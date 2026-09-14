#!/usr/bin/env python3
"""Persistent FIFO simulation jobs. Private subprocess + immutable run inputs.

JSON-lines protocol is owned by rerun's /api/sim handler; never accepts shell
commands, executable paths or arbitrary environment variables from the browser.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET

# Apollo's generated Python schemas use the pure-Python runtime in this service;
# do not inherit a shell's `cpp` setting without the matching extension module.
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "simulation/logsim/tools"))
from bag_diff import compare, messages, difference_page
from configuration_tools import (apply_workspace_configuration, configuration_lock,
                                 update_global_flagfile, vehicle_geometry)

MODULES = {
    "PREDICTION": ("modules/prediction/dag/prediction.dag", "/apollo/prediction"),
    "PLANNING": ("modules/planning/planning_component/dag/planning.dag", "/apollo/planning"),
    "CONTROL": ("modules/control/control_component/dag/control.dag", "/apollo/control"),
    "ROUTING": ("modules/routing/dag/routing.dag", "/apollo/raw_routing_response"),
}
INPUTS = ["/apollo/canbus/chassis", "/apollo/localization/pose", "/apollo/perception/obstacles"]
STAGES = ["queued", "data_preparation", "map_update", "profile_update", "model_update",
          "simulation_start", "simulation_running", "simulation_end", "result_analysis", "completed"]
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def stop_process(child, sig=signal.SIGTERM):
    if child and child.poll() is None:
        try:
            os.killpg(child.pid, sig)
        except ProcessLookupError:
            pass


def preflight_plugins(runtime, modules):
    """Reject unavailable profile plugins before Apollo's plugin initialization."""
    if "PLANNING" not in modules:
        return
    available = set()
    for path in Path("/opt/apollo/neo/share/modules/planning").rglob("plugins.xml"):
        root = ET.parse(path).getroot()
        for entry in root.iter("class"):
            available.add(entry.attrib["type"].rsplit("::", 1)[-1])
    conf = runtime / "modules/planning/planning_component/conf"
    for name in ("public_road_planner_config.pb.txt", "traffic_rule_config.pb.txt"):
        path = conf / name
        # Commented-out profile extensions are not runtime plugin dependencies.
        active = re.sub(r"(?m)^\s*#.*$", "", path.read_text())
        required = set(re.findall(r'\btype\s*:\s*"([^"]+)"', active))
        missing = sorted(t for t in required if t.rsplit("::", 1)[-1] not in available)
        if missing:
            raise ValueError(f"Profile requires unavailable planning plugins: {', '.join(missing)} ({path}); install the matching plugins or explicitly select a compatible profile")


def preflight_world(path):
    text = Path(path).read_text()
    document = json.loads(text)
    if isinstance(document, dict) and (
        document.get("kind") == "mine-project" or
        any(isinstance(a, dict) and a.get("type") == "ego"
            for a in (document.get("agents") or []))
    ):
        raise ValueError(
            "Selected file is an editor project, not a WorldSim Scenario. "
            "请在 scene_editor 中打开项目，选择「项目 → 导出 WorldSim 场景…」，"
            "然后选择导出的 *.worldsim.scenario.json 运行仿真。"
            "不要仅删除 kind 字段：主车、路径和触发器也需要转换。")
    sys.path.insert(0, "/opt/apollo/neo/python")
    from google.protobuf.json_format import Parse
    from simulation.worldsim.proto.scenario_pb2 import Scenario
    scenario = Parse(text, Scenario(), ignore_unknown_fields=False)
    def finite(message):
        for field, value in message.ListFields():
            values = value if field.label == field.LABEL_REPEATED else [value]
            for item in values:
                if field.type == field.TYPE_MESSAGE: finite(item)
                elif field.type in (field.TYPE_DOUBLE, field.TYPE_FLOAT) and not math.isfinite(item):
                    raise ValueError(f"Non-finite scenario field: {field.full_name}")
    finite(scenario)
    agents = {a.id: a for a in scenario.agents}
    ids = set(agents) | {"", "ego", scenario.ego.id}
    triggers = set()
    for trigger in scenario.triggers:
        if not trigger.id or trigger.id in triggers or trigger.type not in (1, 2, 3, 4, 5):
            raise ValueError("Scenario has an invalid/duplicate trigger")
        triggers.add(trigger.id)
        references = [trigger.target_agent_id] if trigger.type in (2, 4) else [trigger.agent_a_id, trigger.agent_b_id] if trigger.type == 3 else [trigger.source_agent_id] if trigger.type == 5 else []
        if any(value not in ids for value in references):
            raise ValueError(f"Trigger {trigger.id} references an unknown agent")
        if trigger.type == 5 and trigger.event not in ("started", "stopped", "arrived"):
            raise ValueError(f"Unknown behavior event in trigger {trigger.id}")
        if trigger.type in (3, 4) and trigger.compare not in ("", "less", "greater"):
            raise ValueError(f"Unknown comparison in trigger {trigger.id}")
        for action in trigger.actions:
            if action.kind not in range(1, 7) or action.target_agent_id not in agents:
                raise ValueError(f"Trigger {trigger.id}: actions require a declared non-ego agent; direct ego trigger actions are not implemented by this closed-loop model")
            if action.kind in (2, 4) and action.route_id not in {r.id for r in agents[action.target_agent_id].routes}:
                raise ValueError(f"Trigger {trigger.id}: unknown target route")


def atomic_json(path, value):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    tmp.replace(path)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def resolve(path, directory=False):
    value = str(path)
    host_prefix = "/home/wangsheng/code/apollo/"
    if value.startswith(host_prefix):
        value = str(ROOT / value[len(host_prefix):])
    out = Path(value)
    if not out.is_absolute():
        out = ROOT / out
    out = out.resolve(strict=True)
    allowed = [ROOT.resolve(), Path("/opt/apollo/neo/share").resolve()]
    if not any(out.is_relative_to(root) for root in allowed):
        raise ValueError(f"Path outside simulation input roots: {out}")
    if directory != out.is_dir() or (not directory and not out.is_file()):
        raise ValueError(f"Wrong input path type: {out}")
    return out


def validate(request):
    config = copy.deepcopy(request)
    if config.get("kind") not in ("bag", "world"):
        raise ValueError("Choose bag or world input")
    config["source"] = str(resolve(config.get("source", "")))
    config["map"] = str(resolve(config.get("map", ""), directory=True))
    config["vehicle"] = str(resolve(config.get("vehicle", "")))
    if not any((Path(config["map"]) / name).is_file() for name in ("base_map.bin", "base_map.txt")):
        raise ValueError("Selected map has no base_map.bin / base_map.txt")
    selected = config.get("modules", [])
    if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected):
        raise ValueError("Choose distinct algorithm modules")
    if any(module not in MODULES for module in selected):
        raise ValueError("Unsupported algorithm module")
    config["modules"] = [module for module in MODULES if module in selected]
    config["profile"] = str(resolve(config["profile"], directory=True)) if config.get("profile") else ""
    config["repeat"] = int(config.get("repeat", 2))
    if config["repeat"] not in (1, 2, 3):
        raise ValueError("Repeat count must be 1, 2 or 3")
    config["seed"] = int(config.get("seed", 1))
    if not 0 <= config["seed"] <= 2**32 - 1:
        raise ValueError("Seed must fit uint32")
    config["step_ms"] = int(config.get("step_ms", 10))
    if config["step_ms"] not in (1, 2, 5, 10):
        raise ValueError("Step must be 1, 2, 5 or 10 ms")
    config["model"] = config.get("model", "perfect_planning")
    if config["model"] not in ("perfect_planning", "kinematic_control"):
        raise ValueError("Unsupported ego model")
    config["timeout_s"] = int(config.get("timeout_s", 600))
    if not 10 <= config["timeout_s"] <= 7200:
        raise ValueError("Job wall timeout must be between 10 and 7200 s")
    for name in ("begin_s", "end_s"):
        config[name] = float(config.get(name, 0))
        if not math.isfinite(config[name]) or config[name] < 0:
            raise ValueError(f"Invalid {name}")
    if config["end_s"] and config["end_s"] <= config["begin_s"]:
        raise ValueError("End timestamp must be greater than begin timestamp")
    if config["kind"] == "world":
        if Path(config["source"]).suffix != ".json":
            raise ValueError("World input must be a scenario JSON")
        if not {"ROUTING", "PREDICTION", "PLANNING"}.issubset(selected):
            raise ValueError("World closed loop requires ROUTING, PREDICTION and PLANNING")
        if config["model"] == "kinematic_control" and "CONTROL" not in selected:
            raise ValueError("kinematic_control requires CONTROL")
    elif ".record" not in Path(config["source"]).name:
        raise ValueError("LogSim input must be an Apollo Cyber record")
    return config


def catalog():
    records = sorted(str(p) for p in (ROOT / "data/bag").glob("*.record*") if p.is_file())
    records += sorted(str(p) for p in (ROOT / "data/bag/data_with_map/extracted").glob("*.record*") if p.is_file())
    worlds = sorted(str(p) for p in (ROOT / "simulation/scene_editor/examples").glob("*.json"))
    worlds += sorted(str(p) for p in (ROOT / "data/scenarios").glob("*.json"))
    maps = set()
    for base in (ROOT / "modules/map/data", ROOT / "data/bag/data_with_map/extracted"):
        for name in ("base_map.bin", "base_map.txt"):
            maps.update(str(p.parent) for p in base.glob("*/" + name))
    profiles = sorted(str(p) for p in (ROOT / "profiles").iterdir() if p.is_dir()) if (ROOT / "profiles").is_dir() else []
    extracted = ROOT / "data/bag/data_with_map/extracted/Jiyu_01"
    if extracted.is_dir():
        profiles.append(str(extracted))
    vehicles = [str(ROOT / "modules/common/data/vehicle_param.pb.txt")]
    vehicles += [str(Path(p) / "modules/common/data/vehicle_param.pb.txt") for p in profiles]
    vehicles = [p for p in vehicles if Path(p).is_file()]
    return {"modules": list(MODULES), "bags": records, "worlds": worlds, "maps": sorted(maps),
            "profiles": profiles, "vehicles": vehicles, "stages": STAGES,
            "models": ["perfect_planning", "kinematic_control"]}


class TaskService:
    def __init__(self, state_dir):
        self.root = Path(state_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.file_lock = (self.root / "queue.lock").open("a")
        try:
            fcntl.flock(self.file_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file_lock.close()
            raise
        self.lock = threading.RLock()
        self.queue = queue.Queue()
        self.jobs = {}
        self.cancelled = set()
        self.child = None
        self.active = None
        self.stopping = False
        saved = self.root / "jobs.json"
        if saved.exists():
            self.jobs = json.loads(saved.read_text())
            for job in self.jobs.values():
                if job["stage"] not in TERMINAL:
                    job.update(stage="interrupted", error="Simulation service restarted; submit a new run from the saved configuration")
            self.save()
        self.thread = threading.Thread(target=self.work, name="simulation-fifo", daemon=True)
        self.thread.start()

    def save(self):
        atomic_json(self.root / "jobs.json", self.jobs)

    def update(self, job_id, **fields):
        with self.lock:
            job = self.jobs[job_id]
            if "stage" in fields:
                job["history"].append({"stage": fields["stage"], "wall_time": time.time()})
            job.update(fields)
            self.save()

    def request(self, request):
        action = request.get("action")
        if action == "catalog":
            return {"status": "ok", "catalog": catalog()}
        if action == "differences":
            with self.lock:
                job = copy.deepcopy(self.jobs.get(request.get("id")))
            if job is None:
                raise ValueError("Unknown task")
            if job["stage"] not in TERMINAL:
                raise ValueError("Difference inspection requires a finished task")
            comparison = request.get("comparison", 0)
            outputs = job.get("outputs", [])
            if type(comparison) is not int or not 0 <= comparison < len(outputs) - 1:
                raise ValueError("Select an available run comparison")
            # Only immutable outputs of this task, never browser-supplied paths.
            page = difference_page(outputs[0], outputs[comparison + 1],
                                   offset=request.get("offset", 0),
                                   limit=request.get("limit", 1),
                                   include_messages=request.get("include_messages", False))
            page.update(task_id=job["id"], comparison=comparison,
                        left_run=1, right_run=comparison + 2)
            return {"status": "ok", "differences": page}
        if action == "enqueue":
            config = validate(request.get("config", {}))
            with self.lock:
                if sum(j["stage"] not in TERMINAL for j in self.jobs.values()) >= 32:
                    raise ValueError("Queue limit reached (32 active / waiting tasks)")
                job_id = uuid.uuid4().hex[:16]
                self.jobs[job_id] = {"id": job_id, "stage": "queued", "config": config,
                    "progress": 0, "history": [{"stage": "queued", "wall_time": time.time()}],
                    "outputs": [], "error": None, "analysis": None}
                self.save()
                self.queue.put(job_id)
            return {"status": "ok", "id": job_id}
        if action == "cancel":
            with self.lock:
                job_id = request["id"]
                if job_id not in self.jobs:
                    raise ValueError("Unknown task")
                if self.jobs[job_id]["stage"] not in TERMINAL:
                    self.cancelled.add(job_id)
                    if self.active == job_id and self.child and self.child.poll() is None:
                        stop_process(self.child)
                    if self.jobs[job_id]["stage"] == "queued":
                        self.update(job_id, stage="cancelled")
            return {"status": "ok"}
        if action == "list":
            with self.lock:
                return {"status": "ok", "jobs": copy.deepcopy(list(self.jobs.values()))}
        raise ValueError("Unknown simulation API action")

    def check_cancel(self, job_id):
        if self.stopping or job_id in self.cancelled:
            raise InterruptedError("Task cancelled")

    def work(self):
        while True:
            job_id = self.queue.get()
            if job_id is None:
                return
            try:
                self.check_cancel(job_id)
                self.active = job_id
                self.run(job_id)
            except InterruptedError as error:
                self.update(job_id, stage="cancelled", error=str(error))
            except Exception as error:
                self.update(job_id, stage="failed", error=str(error))
            finally:
                self.active = self.child = None

    def run(self, job_id):
        # Configuration is workspace-wide, just like aem profile use. Serialize
        # services too; failures never recover a different profile implicitly.
        with configuration_lock(ROOT):
            self.check_cancel(job_id)
            self.run_with_configuration_lock(job_id)

    def run_with_configuration_lock(self, job_id):
        config = self.jobs[job_id]["config"]
        job_dir = self.root / job_id
        job_dir.mkdir()
        manifests = {}

        def snapshot(source, target):
            self.check_cancel(job_id)
            source, target = Path(source), Path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            before = source.stat()
            temporary = target.with_name(target.name + ".preparing")
            checksum = hashlib.sha256()
            with source.open("rb") as reader, temporary.open("wb") as writer:
                for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                    self.check_cancel(job_id)
                    writer.write(chunk)
                    checksum.update(chunk)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError(f"Input changed during snapshot: {source}")
            shutil.copystat(source, temporary)
            temporary.replace(target)
            manifests[str(target.relative_to(job_dir))] = checksum.hexdigest()
            return target

        self.update(job_id, stage="data_preparation")
        source = snapshot(config["source"], job_dir / "input" / Path(config["source"]).name)
        if config["kind"] == "world":
            preflight_world(source)
        self.update(job_id, stage="map_update")
        map_dir = job_dir / "map"
        for item in sorted(Path(config["map"]).iterdir()):
            if item.is_file() and item.name.startswith(("base_map.", "sim_map.", "routing_map.", "default_end_waypoint")):
                snapshot(item, map_dir / item.name)
        self.update(job_id, stage="profile_update")
        vehicle = snapshot(config["vehicle"], job_dir / "vehicle/vehicle_param.pb.txt")
        profile = Path(config["profile"]) if config["profile"] else None
        preflight_plugins(profile or ROOT, config["modules"])
        effective = apply_workspace_configuration(ROOT, profile, config["map"], config["vehicle"])
        if any(effective[key] != value for key, value in vehicle_geometry(vehicle).items()):
            raise RuntimeError("Vehicle parameters changed during configuration preparation")
        atomic_json(job_dir / "configuration.json", effective)
        self.update(job_id, effective_configuration=effective)
        # buildtool discovers Cyber packages recursively, excluding dot folders.
        # Keep the overlay private so it cannot be mistaken for another checkout.
        runtime = job_dir / ".runtime"
        runtime.mkdir()
        # Copy configuration files; code / large model files remain read-only inputs.
        # Freeze the applied profile for this task, independently of later switches.
        def overlay(base, override, target):
            target.mkdir(exist_ok=True)
            names = {p.name for p in base.iterdir()} if base.is_dir() else set()
            if override and override.is_dir():
                names |= {p.name for p in override.iterdir()}
            for name in sorted(names):
                self.check_cancel(job_id)
                primary = base / name
                custom = override / name if override else None
                chosen = custom if custom and custom.exists() else primary
                dest = target / name
                if chosen.is_dir():
                    overlay(primary, custom, dest)
                elif chosen.suffix in (".txt", ".conf", ".json", ".yaml", ".yml", ".dag", ".xml", ".launch"):
                    snapshot(chosen, dest)
                else:
                    dest.symlink_to(chosen.resolve())
        # Module roots need directory traversal so each selected module's conf is frozen.
        modules_dir = runtime / "modules"
        modules_dir.mkdir()
        for item in sorted((ROOT / "modules").iterdir()):
            custom = profile / "modules" / item.name if profile else None
            if item.name in ("common", "planning", "prediction", "control", "routing") or (custom and custom.exists()):
                overlay(item, custom, modules_dir / item.name)
            else:
                (modules_dir / item.name).symlink_to(item.resolve(), target_is_directory=item.is_dir())
        if profile and (profile / "cyber").is_dir():
            overlay(ROOT / "cyber", profile / "cyber", runtime / "cyber")
        else:
            (runtime / "cyber").symlink_to(ROOT / "cyber", target_is_directory=True)
        # Never link the workspace data/simulation roots back into a job: this
        # creates recursive trees for buildtool and lets modules write globally.
        (runtime / "data").mkdir()
        # Actual component flagfiles include this path. Freeze the selected
        # vehicle here too; direct relative reads must agree with gflags.
        runtime_vehicle = snapshot(vehicle, runtime / "modules/common/data/vehicle_param.pb.txt")
        global_flags = runtime / "modules/common/data/global_flagfile.txt"
        update_global_flagfile(global_flags, map_dir, runtime_vehicle)
        manifests[str(global_flags.relative_to(job_dir))] = digest(global_flags)
        if vehicle_geometry(runtime_vehicle) != vehicle_geometry(vehicle):
            raise RuntimeError("Runtime vehicle parameters differ from task input")
        effective.update(runtime_global_flagfile=str(global_flags), runtime_map_dir=str(map_dir),
                         runtime_vehicle_config_path=str(runtime_vehicle), task_vehicle_config_path=str(vehicle))
        atomic_json(job_dir / "configuration.json", effective)
        self.update(job_id, effective_configuration=effective)
        self.update(job_id, stage="model_update")
        preflight_plugins(runtime, config["modules"])
        binary = Path(os.environ.get("SIMULATOR_BINARY", "/opt/apollo/neo/bin/simulator_main")).resolve(strict=True)
        if not os.access(binary, os.X_OK):
            raise ValueError("Simulator binary is not executable")
        manifests["simulator_binary"] = digest(binary)
        runtime_libraries = sorted(Path("/opt/apollo/neo/lib/simulation").rglob("*.so"))
        for module in ["common", "map"] + [m.lower() for m in config["modules"]]:
            runtime_libraries += sorted((Path("/opt/apollo/neo/lib/modules") / module).rglob("*.so"))
        library_hashes = {str(p): digest(p) for p in runtime_libraries}
        model_hashes = {str(p.resolve()): digest(p) for p in modules_dir.rglob("*")
                        if p.is_file() and p.suffix.lower() in (".pt", ".onnx", ".bin", ".params", ".model", ".pb", ".weights")}
        atomic_json(job_dir / "manifest.json", {"config": config, "sha256": manifests,
                     "runtime_libraries": library_hashes,
                     "model_inputs": model_hashes,
                     "runtime_binary": str(binary), "ego_model": config["model"], "seed": config["seed"]})
        outputs = []
        for run in range(config["repeat"]):
            self.check_cancel(job_id)
            run_dir = job_dir / f"run-{run + 1}"
            run_dir.mkdir()
            output = run_dir / "simulation.record"
            progress = run_dir / "progress.json"
            inject = list(INPUTS)
            if config["kind"] == "world":
                inject += ["/apollo/raw_routing_request", "/apollo/planning/command"]
            else:
                inject += ["/apollo/planning/command", "/apollo/planning_command_history"]
                if "ROUTING" in config["modules"]:
                    inject.append("/apollo/raw_routing_request")
                if "PREDICTION" not in config["modules"]:
                    inject.append("/apollo/prediction")
                if "PLANNING" not in config["modules"]:
                    inject.append("/apollo/planning")
            suppress = [MODULES[m][1] for m in config["modules"]]
            fields = {"scenario_id": job_id, "task_dir": str(run_dir), "map_dir": str(map_dir),
                "vehicle_config_path": str(vehicle), "output_record_path": str(output),
                "progress_path": str(progress), "random_seed": config["seed"], "step_ms": config["step_ms"],
                "ego_model": config["model"], "profile_path": str(runtime),
                "cyber_conf_path": str(ROOT / "simulation/simulator/conf/cyber_sim.pb.conf")}
            if config["kind"] == "world":
                fields["world_scenario_path"] = str(source)
            else:
                fields.update(record_paths=str(source), log_start_s=config["begin_s"], log_end_s=config["end_s"])
            def pb(value): return json.dumps(value) if isinstance(value, str) else str(value)
            lines = [f"{key}: {pb(value)}" for key, value in fields.items()]
            lines += ["input_kind: " + ("WORLD" if config["kind"] == "world" else "BAG")]
            lines += [f"runtime_modules: {pb(m)}" for m in config["modules"]]
            lines += [f"dag_paths: {pb(str(runtime / MODULES[m][0]))}" for m in config["modules"]]
            lines += ["channel_policy {"]
            for key, values in (("inject_channels", inject), ("suppress_channels", suppress), ("record_channels", suppress)):
                lines += [f"  {key}: {pb(value)}" for value in values]
            lines += ["}"]
            (run_dir / "task.pb.txt").write_text("\n".join(lines) + "\n")
            self.update(job_id, stage="simulation_start", run=run + 1, progress=0)
            env = os.environ.copy()
            env.update(PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION="python", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
                APOLLO_DISTRIBUTION_HOME="/opt/apollo/neo", APOLLO_PLUGIN_INDEX_PATH="/opt/apollo/neo/share/cyber_plugin_index",
                APOLLO_PLUGIN_DESCRIPTION_PATH="/opt/apollo/neo", APOLLO_PLUGIN_LIB_PATH="/opt/apollo/neo/lib",
                APOLLO_LIB_PATH="/opt/apollo/neo/lib", APOLLO_DAG_PATH=str(runtime),
                APOLLO_RUNTIME_PATH=str(runtime), APOLLO_ENV_WORKROOT=str(runtime),
                APOLLO_FLAG_PATH=str(runtime), APOLLO_CONF_PATH=str(runtime),
                LD_LIBRARY_PATH="/opt/apollo/neo/lib:" + env.get("LD_LIBRARY_PATH", ""))
            env.pop("SIM_OUTPUT_RECORD", None)
            env["SIM_PARENT_PID"] = str(os.getpid())
            log = run_dir / "runtime.log"
            with log.open("wb") as stream:
                self.child = subprocess.Popen([str(binary), "--task_dir=" + str(run_dir)], cwd=runtime,
                    env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
                start = time.monotonic()
                self.update(job_id, stage="simulation_running", log_path=str(log), process_id=self.child.pid)
                while self.child.poll() is None:
                    if job_id in self.cancelled or self.stopping or time.monotonic() - start > config["timeout_s"]:
                        stop_process(self.child)
                        try: self.child.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            stop_process(self.child, signal.SIGKILL)
                            self.child.wait()
                        self.check_cancel(job_id)
                        raise TimeoutError("Simulation exceeded its wall-time limit; see runtime.log")
                    if progress.is_file():
                        state = json.loads(progress.read_text())
                        self.update(job_id, progress=state["percent"], simulation=state)
                    time.sleep(.2)
                self.check_cancel(job_id)
                if self.child.returncode != 0:
                    with log.open("rb") as tail:
                        tail.seek(max(0, log.stat().st_size - 4000))
                        detail = tail.read().decode(errors="replace")
                    raise RuntimeError(f"Simulator exited {self.child.returncode}; {detail}")
            self.update(job_id, stage="simulation_end")
            # Cyber RecordWriter may append a segment suffix; require exactly one
            # completed segment here rather than accidentally replaying an index.
            candidates = [output] if output.is_file() else sorted(run_dir.glob("simulation.record.*"))
            if len(candidates) != 1:
                raise RuntimeError(f"Expected one simulation record segment, found {len(candidates)}")
            outputs.append(str(candidates[0]))
            self.update(job_id, outputs=outputs)
        self.update(job_id, stage="result_analysis")
        counts = {}
        stamps = {}
        status_counts = {}
        healthy_plans = 0
        first_pose = last_pose = None
        sys.path.insert(0, "/opt/apollo/neo/python")
        from modules.common_msgs.planning_msgs.planning_pb2 import ADCTrajectory
        from modules.common_msgs.control_msgs.control_cmd_pb2 import ControlCommand
        from modules.common_msgs.localization_msgs.localization_pb2 import LocalizationEstimate
        for channel, stamp, payload in messages(outputs[0]):
            counts[channel] = counts.get(channel, 0) + 1
            if channel in stamps and stamp < stamps[channel]:
                raise RuntimeError(f"Output clock moved backwards: {channel}")
            stamps[channel] = stamp
            if channel in ("/apollo/planning", "/apollo/control"):
                msg = (ADCTrajectory if channel == "/apollo/planning" else ControlCommand).FromString(payload)
                code = msg.header.status.error_code
                key = f"{channel}:{code}"
                status_counts[key] = status_counts.get(key, 0) + 1
                if channel == "/apollo/planning" and code == 0 and msg.trajectory_point and not msg.decision.main_decision.HasField("not_ready"):
                    healthy_plans += 1
            if channel == "/apollo/localization/pose":
                p = LocalizationEstimate.FromString(payload).pose.position
                last_pose = (p.x, p.y)
                if first_pose is None: first_pose = last_pose
        missing = [MODULES[m][1] for m in config["modules"] if not counts.get(MODULES[m][1])]
        comparisons = [compare(outputs[0], item, algorithm=True) for item in outputs[1:]]
        analysis = {"topic_message_counts": counts, "missing_module_outputs": missing,
            "effective_configuration": effective,
            "algorithm_status_counts": status_counts, "valid_planning_frames": healthy_plans,
            "ego_displacement_m": math.dist(first_pose, last_pose) if first_pose is not None else None,
            "determinism": "not_tested" if not comparisons else "PASS" if all(c["result"] == "PASS" for c in comparisons) else "FAIL",
            "comparisons": comparisons, "manifest": str(job_dir / "manifest.json"),
            "scope": "Exact ordered messages, nanosecond timestamps and protobuf values; only listed wall profiling fields excluded and map ordering canonicalized; raw differences retained. Same runtime environment."}
        atomic_json(job_dir / "analysis.json", analysis)
        self.update(job_id, analysis=analysis)
        if missing:
            raise RuntimeError("Selected modules produced no output: " + ", ".join(missing))
        if "PLANNING" in config["modules"] and healthy_plans == 0:
            raise RuntimeError("Planning produced no valid trajectory frames; inspect algorithm status and replay the output bag")
        if comparisons and any(c["result"] != "PASS" for c in comparisons):
            raise RuntimeError("Determinism comparison failed; output bags and first differences are retained")
        if digest(binary) != manifests["simulator_binary"]:
            raise RuntimeError("Simulator binary changed during task execution")
        if any(digest(path) != value for path, value in library_hashes.items()):
            raise RuntimeError("Simulator libraries changed during task execution")
        if any(digest(path) != value for path, value in model_hashes.items()):
            raise RuntimeError("Algorithm model inputs changed during task execution")
        if any(digest(job_dir / path) != value for path, value in manifests.items() if path != "simulator_binary"):
            raise RuntimeError("Task input/config snapshots changed during execution")
        self.update(job_id, stage="completed", progress=100)

    def close(self):
        self.stopping = True
        if self.child and self.child.poll() is None:
            stop_process(self.child)
        self.queue.put(None)
        self.thread.join(timeout=5)
        if not self.thread.is_alive():
            self.file_lock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", default=os.environ.get("SIM_TASK_ROOT", str(ROOT / "data/simulation/jobs")))
    args = parser.parse_args()
    service = TaskService(args.state_dir)
    def shutdown(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        for line in sys.stdin:
            try:
                if len(line) > 65536: raise ValueError("Request exceeds 64 KiB")
                result = service.request(json.loads(line))
            except Exception as error:
                result = {"status": "error", "message": str(error)}
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        service.close()


if __name__ == "__main__":
    main()
