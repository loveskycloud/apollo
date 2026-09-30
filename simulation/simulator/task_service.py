#!/usr/bin/env python3
"""Persistent bounded simulation jobs. Private subprocess + immutable run inputs.

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
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from driving_quality import analyze_trace

# Apollo's generated Python schemas use the pure-Python runtime in this service;
# do not inherit a shell's `cpp` setting without the matching extension module.
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"



def collision_summary(runs):
    """Any collision in any repeat fails; absent/incomplete checks never pass."""
    count = sum(r.get("collision_count", 0) for r in runs)
    complete = bool(runs) and all(r.get("complete") is True and r.get("checked_frames", 0) > 0 for r in runs)
    return {"status": "FAIL" if count or any(r.get("status") == "FAIL" for r in runs) else
            "PASS" if complete and all(r.get("status") == "PASS" for r in runs) else
            "INCOMPLETE" if runs else "NOT_EVALUATED",
            "collision_count": count, "runs": runs}

def _dedupe_dirs(paths):
    """Keep both bind-mount aliases (e.g. /apollo_workspace vs host checkout)."""
    ordered = []
    seen = set()
    for raw in paths:
        if not raw:
            continue
        path = Path(raw)
        if not path.is_dir():
            continue
        for form in (path, path.absolute()):
            try:
                resolved = form.resolve()
            except OSError:
                resolved = form
            for candidate in (form, resolved):
                key = str(candidate)
                if key in seen or not Path(key).exists():
                    continue
                seen.add(key)
                ordered.append(Path(key))
    return ordered


def _simulation_package_root() -> Path:
    """Directory that contains simulator/, logsim/, worldsim/ (follows symlinks)."""
    return Path(__file__).resolve().parents[1]


def _workspace_root():
    """Prefer the container/workspace mount over a symlinked simulation/ checkout.

    ``modules/simulation`` (or legacy ``simulation``) may point at another repo
    (e.g. apollo-private). Using ``Path(__file__).resolve()`` alone would make
    ROOT that other repo and reject map paths under application-core /
    /apollo_workspace.
    """
    def _looks_like_workspace(path: Path) -> bool:
        if not path.is_dir() or not (path / "data").is_dir():
            return False
        return (path / "modules/simulation").exists() or (path / "simulation").exists()

    for candidate in (
        os.environ.get("APOLLO_WORKSPACE"),
        os.environ.get("APOLLO_ENV_WORKROOT"),
        "/apollo_workspace",
        "/home/wangsheng/test/application-core",
    ):
        if not candidate:
            continue
        path = Path(candidate)
        if _looks_like_workspace(path):
            return path
    for parent in Path(__file__).resolve().parents:
        if _looks_like_workspace(parent):
            return parent
    return Path(__file__).resolve().parents[3]


ROOT = _workspace_root()
# Simulation package root (…/simulation or …/modules/simulation), not the workspace.
SIM_PKG = _simulation_package_root()
CODE_ROOT = SIM_PKG
INPUT_ROOTS = _dedupe_dirs([
    ROOT,
    SIM_PKG,
    ROOT / "modules/simulation",
    ROOT / "simulation",
    "/apollo_workspace",
    "/home/wangsheng/test/application-core",
    "/opt/apollo/neo/share",
    "/apollo/modules/map/data",
])
_SIM_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SIM_DIR))
_logsim_tools = SIM_PKG / "logsim/tools"
sys.path.insert(0, str(_logsim_tools))
if str(ROOT / "modules/simulation/logsim/tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "modules/simulation/logsim/tools"))
from bag_diff import compare, messages, difference_page
from configuration_tools import APOLLO_GLOBAL_FLAGS, update_global_flagfile, vehicle_geometry
from scenario_evaluation import evaluation_path, load_evaluation, evaluate_record

MODULES = {
    "PREDICTION": ("modules/prediction/dag/prediction.dag", "/apollo/prediction"),
    "fake_prediction": ("modules/fake_prediction/dag/fake_prediction.dag", "/apollo/prediction"),
    "PLANNING": ("modules/planning/planning_component/dag/planning.dag", "/apollo/planning"),
    "ML_PLANNING": ("modules/simulation/ml_planning/dag/ml_planning.dag", "/apollo/planning"),
    "CONTROL": ("modules/control/control_component/dag/control.dag", "/apollo/control"),
    "ROUTING": ("modules/routing/dag/routing.dag", "/apollo/raw_routing_response"),
}
INPUTS = ["/apollo/canbus/chassis", "/apollo/localization/pose", "/apollo/perception/obstacles"]
STAGES = ["queued", "data_preparation", "map_update", "profile_update", "model_update",
          "simulation_start", "simulation_running", "simulation_end", "result_analysis", "completed"]
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
APOLLO_MODULES = Path("/apollo/modules")


def _prediction_selected(modules):
    return "PREDICTION" in modules or "fake_prediction" in modules


def _module_dir_name(module):
    """modules/<name>/... → name."""
    return Path(MODULES[module][0]).parts[1]


def _module_base(name):
    for candidate in (APOLLO_MODULES / name, ROOT / "modules" / name):
        if candidate.is_dir():
            return candidate
    return None


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
    try:
        from modules.simulation.worldsim.proto.scenario_pb2 import Scenario
    except ModuleNotFoundError:
        # Legacy install path when simulation lived outside modules/.
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


def atomic_json(path, value, compact=False):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=None if compact else 2, allow_nan=False))
    tmp.replace(path)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def resolve(path, directory=False):
    value = str(path)
    # Normalize common host → container workspace aliases.
    aliases = (
        ("/home/wangsheng/code/apollo/", str(ROOT) + "/"),
        ("/home/wangsheng/test/application-core/", "/apollo_workspace/"),
    )
    for src, dst in aliases:
        if value.startswith(src) and Path(dst).is_dir():
            value = dst + value[len(src):]
            break
    out = Path(value)
    if not out.is_absolute():
        out = ROOT / out
    out = out.resolve(strict=True)
    if not any(out.is_relative_to(root) for root in INPUT_ROOTS):
        raise ValueError(f"Path outside simulation input roots: {out}")
    if directory != out.is_dir() or (not directory and not out.is_file()):
        raise ValueError(f"Wrong input path type: {out}")
    return out


def _expand_vehicle_profile(config):
    """Dreamview-style vehicle pick: identity is a profile/pack dir.

    UI sends the vehicle/profile directory; backend applies profile overlay and
    derives modules/common/data/vehicle_param.pb.txt. Legacy clients may still
    send a vehicle_param file path (optionally with profile).
    """
    raw = str(config.get("vehicle", "") or "").strip()
    if not raw:
        raise ValueError("Choose a vehicle")
    try:
        selected = resolve(raw, directory=True)
        as_dir = True
    except ValueError:
        selected = resolve(raw, directory=False)
        as_dir = False

    if as_dir:
        profile = Path(selected)
        vehicle_param = profile / "modules/common/data/vehicle_param.pb.txt"
        if not vehicle_param.is_file():
            raise ValueError(f"Vehicle has no vehicle parameters: {vehicle_param}")
        config["profile"] = str(profile)
        config["vehicle"] = str(vehicle_param.resolve())
        return

    vehicle_param = Path(selected)
    config["vehicle"] = str(vehicle_param)
    if config.get("profile"):
        config["profile"] = str(resolve(config["profile"], directory=True))
        expected = Path(config["profile"]) / "modules/common/data/vehicle_param.pb.txt"
        if not expected.is_file() or expected.resolve() != vehicle_param.resolve():
            raise ValueError(
                f"Vehicle config does not match the selected profile; select {expected}")
        return

    # Derive profile when vehicle_param lives under <profile>/modules/common/data/.
    marker = ("modules", "common", "data", "vehicle_param.pb.txt")
    parts = vehicle_param.parts
    profile = ""
    for i in range(len(parts) - len(marker) + 1):
        if parts[i:i + len(marker)] == marker:
            profile = str(Path(*parts[:i])) if i else ""
            break
    config["profile"] = profile


def validate(request):
    config = copy.deepcopy(request)
    if config.get("kind") not in ("bag", "world"):
        raise ValueError("Choose bag or world input")
    config["source"] = str(resolve(config.get("source", "")))
    if config["kind"] == "world":
        config["evaluation"] = load_evaluation(config["source"])
        validity = config["evaluation"].get("validity", {})
        if validity.get("status") == "INVALID":
            raise ValueError("Invalid scenario excluded from validation: " + validity["reason"])
    config["map"] = str(resolve(config.get("map", ""), directory=True))
    if not any((Path(config["map"]) / name).is_file() for name in ("base_map.bin", "base_map.txt")):
        raise ValueError("Selected map has no base_map.bin / base_map.txt")
    _expand_vehicle_profile(config)
    selected = config.get("modules", [])
    if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected):
        raise ValueError("Choose distinct algorithm modules")
    if any(module not in MODULES for module in selected):
        raise ValueError("Unsupported algorithm module")
    if "PREDICTION" in selected and "fake_prediction" in selected:
        raise ValueError("Choose either PREDICTION or fake_prediction, not both")
    if "ML_PLANNING" in selected and "PLANNING" in selected:
        raise ValueError("Choose either PLANNING or ML_PLANNING")
    if "ML_PLANNING" in selected and config.get("model", "perfect_planning") != "perfect_planning":
        raise ValueError("ML_PLANNING currently requires perfect_planning")
    config["modules"] = [module for module in MODULES if module in selected]
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
        raise ValueError("Max duration / wall timeout must be between 10 and 7200 s")
    for name in ("begin_s", "end_s"):
        config[name] = float(config.get(name, 0))
        if not math.isfinite(config[name]) or config[name] < 0:
            raise ValueError(f"Invalid {name}")
    if config["end_s"] and config["end_s"] <= config["begin_s"]:
        raise ValueError("End timestamp must be greater than begin timestamp")
    if config["kind"] == "world":
        if Path(config["source"]).suffix != ".json":
            raise ValueError("World input must be a scenario JSON")
        if "ROUTING" not in selected or not ({"PLANNING", "ML_PLANNING"} & set(selected)) or ("PLANNING" in selected and not _prediction_selected(selected)):
            raise ValueError("World closed loop requires ROUTING and either ML_PLANNING or PLANNING + prediction")
        document = json.loads(Path(config["source"]).read_text())
        map_id = document.get("mapId", document.get("map_id", ""))
        if map_id and Path(config["map"]).name != map_id:
            raise ValueError(f"Scenario map {map_id} does not match selected map {config['map']}")
        if "timeout_s" not in request:
            config["timeout_s"] = max(10, min(7200, math.ceil(document.get("duration", 60))))
        if config["model"] == "kinematic_control" and "CONTROL" not in selected:
            raise ValueError("kinematic_control requires CONTROL")
    elif ".record" not in Path(config["source"]).name:
        raise ValueError("LogSim input must be an Apollo Cyber record")
    return config


def catalog():
    search_roots = _dedupe_dirs([ROOT, SIM_PKG, ROOT / "modules/simulation", ROOT / "simulation"])
    records = []
    worlds = []
    maps = {str(p.parent) for p in Path("/apollo/modules/map/data").glob("*/base_map.bin")}
    profiles = []
    for base_root in search_roots:
        records += sorted(str(p) for p in (base_root / "data/bag").glob("*.record*") if p.is_file())
        records += sorted(
            str(p) for p in (base_root / "data/bag/data_with_map/extracted").glob("*.record*") if p.is_file())
        for examples in (
            base_root / "modules/simulation/scene_editor/examples",
            base_root / "simulation/scene_editor/examples",
            base_root / "scene_editor/examples",  # when base_root is SIM_PKG
        ):
            worlds += sorted(str(p) for p in examples.glob("*.json"))
        worlds += sorted(str(p) for p in (base_root / "data/scenarios").glob("*.json"))
        for base in (
            base_root / "modules/map/data",
            base_root / "data/map_data",
            base_root / "data/bag/data_with_map/extracted",
            base_root / "modules/simulation/scene_editor/public/maps",
            base_root / "simulation/scene_editor/public/maps",
            base_root / "scene_editor/public/maps",
        ):
            for name in ("base_map.bin", "base_map.txt"):
                maps.update(str(p.parent) for p in base.glob("*/" + name))
        if (base_root / "profiles").is_dir():
            profiles += sorted(
                str(p) for p in (base_root / "profiles").iterdir()
                if p.is_dir() and p.name != "current")
        extracted = base_root / "data/bag/data_with_map/extracted/Jiyu_01"
        if extracted.is_dir():
            profiles.append(str(extracted))
    records = sorted(set(records))
    worlds += [str(p) for p in (SIM_PKG / "scene_editor/examples").rglob("*.worldsim.scenario.json")]
    worlds += [str(p) for p in (SIM_PKG / "ml_planning/examples").rglob("*.worldsim.scenario.json")]
    worlds = sorted(set(p for p in worlds if not p.endswith((".mineproj.json", ".suite.json"))))
    suites = sorted(str(p) for p in (SIM_PKG / "scene_editor/examples").rglob("*.suite.json"))
    profiles = sorted(set(profiles))
    # Dreamview-style vehicle list: named packs/profiles that carry vehicle_param.
    vehicles = sorted(
        p for p in profiles
        if (Path(p) / "modules/common/data/vehicle_param.pb.txt").is_file())
    source_maps = {}
    for path in worlds + suites:
        if path.endswith((".worldsim.scenario.json", ".suite.json")):
            document = json.loads(Path(path).read_text())
            source_maps[path] = document.get("mapId", "")
    return {"source_maps": source_maps, "suites": suites, "max_concurrency": 30, "modules": list(MODULES), "bags": records, "worlds": worlds, "maps": sorted(maps),
            "profiles": profiles, "vehicles": vehicles, "stages": STAGES,
            "models": ["perfect_planning", "kinematic_control"]}


class TaskService:
    def __init__(self, state_dir, workers=1):
        if type(workers) is not int or not 1 <= workers <= 30:
            raise ValueError("Worker count must be 1..30")
        self.worker_count = workers
        self.root = Path(state_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.file_lock = (self.root / "queue.lock").open("a")
        try:
            fcntl.flock(self.file_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file_lock.close()
            raise
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.events_condition = threading.Condition(self.lock)
        self.pending_ids = []
        self.running_counts = {}
        self.jobs = {}
        self.revision = 0
        self.job_revisions = {}
        self.cancelled = set()
        self.children = {}
        self.limits = {"single": 1}
        self.stopping = False
        self.last_progress_save = 0.0
        saved = self.root / "jobs.json"
        if saved.exists():
            self.jobs = json.loads(saved.read_text())
            for job in self.jobs.values():
                if job["stage"] not in TERMINAL:
                    job.update(stage="interrupted", error="Simulation service restarted; submit a new run from the saved configuration")
            self.save()
        self.threads = [threading.Thread(target=self.work, name=f"simulation-{i}", daemon=True)
                        for i in range(workers)]
        for thread in self.threads:
            thread.start()

    def save(self):
        # Compact encoding uses Python's C encoder; pretty-printing hundreds of
        # histories under the queue lock serialized otherwise parallel workers.
        atomic_json(self.root / "jobs.json", self.jobs, compact=True)
        self.last_progress_save = time.monotonic()

    def update(self, job_id, **fields):
        with self.lock:
            job = self.jobs[job_id]
            if "stage" in fields:
                job["history"].append({"stage": fields["stage"], "wall_time": time.time()})
            job.update(fields)
            self.notify_jobs([job_id])
            if fields.get("stage") not in TERMINAL and time.monotonic()-self.last_progress_save < 1:
                return  # Live state stays current; terminal states always persist immediately.
            self.save()

    def notify_jobs(self, ids):
        """Called under the queue lock; readers receive only changed jobs."""
        self.revision += 1
        for job_id in ids:
            self.job_revisions[job_id] = self.revision
        self.events_condition.notify_all()

    def events(self, after=None, timeout=None):
        with self.events_condition:
            if after is not None:
                self.events_condition.wait_for(lambda: self.stopping or self.revision > after, timeout)
                if self.stopping or self.revision == after:
                    return None
            jobs = [job for key, job in self.jobs.items()
                    if after is None or self.job_revisions.get(key, 0) > after]
            return {"event": "simulation_jobs", "snapshot": after is None,
                    "revision": self.revision, "jobs": copy.deepcopy(jobs)}

    def request(self, request):
        action = request.get("action")
        if action == "subscribe":
            return {"status": "ok"}
        if action == "catalog":
            value = catalog()
            value["max_concurrency"] = self.worker_count
            return {"status": "ok", "catalog": value}
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
        if action in ("enqueue", "enqueue_suite"):
            config_request = request.get("config", {})
            suite = None
            if action == "enqueue_suite":
                path = resolve(config_request.get("suite", ""))
                suite = json.loads(path.read_text())
                if suite.get("kind") != "worldsim-suite" or suite.get("version") != 1:
                    raise ValueError("Expected a version 1 worldsim-suite manifest")
                if not isinstance(suite.get("name"), str) or not suite["name"].strip():
                    raise ValueError("Suite name is required")
                if not isinstance(suite.get("mapId"), str) or not suite["mapId"]:
                    raise ValueError("Suite mapId is required")
                scenes = suite.get("scenarios")
                if not isinstance(scenes, list) or not 1 <= len(scenes) <= 512:
                    raise ValueError("A suite requires 1..512 scenarios")
                concurrency = config_request.get("concurrency", self.worker_count)
                if type(concurrency) is not int or not 1 <= concurrency <= self.worker_count:
                    raise ValueError(f"Concurrency must be 1..{self.worker_count}")
                configs = []
                for scene in scenes:
                    if not isinstance(scene, str) or Path(scene).is_absolute() or ".." in Path(scene).parts:
                        raise ValueError("Suite scenarios must be relative paths inside its directory")
                    config = dict(config_request, kind="world", source=str(path.parent / scene))
                    config.pop("suite", None)
                    config.pop("concurrency", None)
                    if suite.get("mapId") != Path(config.get("map", "")).name:
                        raise ValueError("Select the suite's matching map: " + str(suite.get("mapId")))
                    configs.append(validate(config))
                # Check every member before publishing any jobs.
                for config in configs:
                    preflight_world(config["source"])
            else:
                configs = [validate(config_request)]
            with self.lock:
                if self.stopping:
                    raise ValueError("Simulation service is stopping")
                if sum(j["stage"] not in TERMINAL for j in self.jobs.values()) + len(configs) > 1024:
                    raise ValueError("Queue limit reached (1024 active / waiting tasks)")
                suite_id = uuid.uuid4().hex[:16] if suite else None
                if suite:
                    self.limits[suite_id] = concurrency
                ids = []
                for index, config in enumerate(configs):
                    job_id = uuid.uuid4().hex[:16]
                    ids.append(job_id)
                    self.jobs[job_id] = {"id": job_id, "stage": "queued", "config": config,
                        "progress": 0, "history": [{"stage": "queued", "wall_time": time.time()}],
                        "outputs": [], "error": None, "analysis": None}
                    if suite:
                        self.jobs[job_id].update(suite_id=suite_id, suite_name=suite["name"],
                                                suite_index=index + 1, suite_size=len(configs),
                                                concurrency=concurrency)
                self.save()
                self.notify_jobs(ids)
                self.pending_ids.extend(ids)
                self.condition.notify_all()
            return {"status": "ok", "id": ids[0], "ids": ids, "suite_id": suite_id}
        if action == "cancel":
            with self.lock:
                job_id = request["id"]
                if job_id not in self.jobs:
                    raise ValueError("Unknown task")
                if self.jobs[job_id]["stage"] not in TERMINAL:
                    self.cancelled.add(job_id)
                    stop_process(self.children.get(job_id))
                    if self.jobs[job_id]["stage"] == "queued":
                        if job_id in self.pending_ids:
                            self.pending_ids.remove(job_id)
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
            with self.condition:
                while True:
                    if self.stopping:
                        return
                    # Pick the earliest eligible member while holding the queue
                    # lock. A semaphore after dequeue lets later members jump
                    # ahead of waiting members when concurrency is one.
                    job_id = next((key for key in self.pending_ids
                                   if self.running_counts.get(self.jobs[key].get("suite_id", "single"), 0)
                                   < self.limits[self.jobs[key].get("suite_id", "single")]), None)
                    if job_id is not None:
                        self.pending_ids.remove(job_id)
                        group = self.jobs[job_id].get("suite_id", "single")
                        self.running_counts[group] = self.running_counts.get(group, 0) + 1
                        break
                    self.condition.wait()
            try:
                self.check_cancel(job_id)
                self.run(job_id)
            except InterruptedError as error:
                self.update(job_id, stage="cancelled", error=str(error))
            except Exception as error:
                self.update(job_id, stage="failed", error=str(error))
            finally:
                with self.lock:
                    child = self.children.pop(job_id, None)
                if child and child.poll() is None:
                    stop_process(child)
                    try:
                        child.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        stop_process(child, signal.SIGKILL)
                        child.wait()
                with self.condition:
                    self.running_counts[group] -= 1
                    self.condition.notify_all()

    def run(self, job_id):
        # Each process reads its own frozen profile and flagfile. Never apply a
        # profile to the live workspace: other running jobs must stay unchanged.
        self.check_cancel(job_id)
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
            sidecar = evaluation_path(config["source"])
            if sidecar.exists():
                snapshot(sidecar, evaluation_path(source))
            if load_evaluation(source) != config.get("evaluation", load_evaluation(source)):
                raise RuntimeError("Scene expectation changed after enqueue; submit the audited scene again")
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
        effective = {"configuration_scope": "task", "profile": str(profile or ""),
                     "map_dir": config["map"], "vehicle_config_path": config["vehicle"],
                     **vehicle_geometry(vehicle)}
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
        # Prefer /apollo/modules as the base; overlay workspace/profile on top.
        modules_dir = runtime / "modules"
        modules_dir.mkdir()
        names = set()
        if (ROOT / "modules").is_dir():
            names.update(p.name for p in (ROOT / "modules").iterdir())
        if profile and (profile / "modules").is_dir():
            names.update(p.name for p in (profile / "modules").iterdir())
        names.update(_module_dir_name(module) for module in config["modules"] if module != "ML_PLANNING")
        names.discard("simulation")
        names.add("common")
        for name in sorted(names):
            base = _module_base(name)
            if base is None:
                continue
            workspace_item = ROOT / "modules" / name
            custom = profile / "modules" / name if profile else None
            if custom and custom.exists():
                override = custom
            elif base.resolve() != workspace_item.resolve() and workspace_item.is_dir():
                override = workspace_item
            else:
                override = None
            selected = any(_module_dir_name(module) == name for module in config["modules"])
            if name == "common" or selected:
                overlay(base, override, modules_dir / name)
            else:
                (modules_dir / name).symlink_to(base.resolve(), target_is_directory=base.is_dir())
        if profile and (profile / "cyber").is_dir():
            overlay(ROOT / "cyber", profile / "cyber", runtime / "cyber")
        else:
            (runtime / "cyber").symlink_to(ROOT / "cyber", target_is_directory=True)
        # Never link the workspace data/simulation roots back into a job: this
        # creates recursive trees for buildtool and lets modules write globally.
        (runtime / "data").mkdir()
        # Actual component flagfiles include this path. Freeze the selected
        # vehicle here too; direct relative reads must agree with gflags.
        # Seed from the canonical container flagfile, not /apollo_workspace.
        runtime_vehicle = snapshot(vehicle, runtime / "modules/common/data/vehicle_param.pb.txt")
        global_flags = runtime / "modules/common/data/global_flagfile.txt"
        flag_source = profile / "modules/common/data/global_flagfile.txt" if profile else APOLLO_GLOBAL_FLAGS
        snapshot(flag_source if flag_source.is_file() else APOLLO_GLOBAL_FLAGS, global_flags)
        update_global_flagfile(global_flags, map_dir, runtime_vehicle)
        manifests[str(global_flags.relative_to(job_dir))] = digest(global_flags)
        if vehicle_geometry(runtime_vehicle) != vehicle_geometry(vehicle):
            raise RuntimeError("Runtime vehicle parameters differ from task input")
        effective.update(runtime_global_flagfile=str(global_flags), runtime_map_dir=str(map_dir),
                         runtime_vehicle_config_path=str(runtime_vehicle), task_vehicle_config_path=str(vehicle))
        atomic_json(job_dir / "configuration.json", effective)
        self.update(job_id, effective_configuration=effective)
        ml_weights = None
        if "ML_PLANNING" in config["modules"]:
            snapshot(SIM_PKG / "ml_planning/dag/ml_planning.dag", runtime / MODULES["ML_PLANNING"][0])
            sys.path.insert(0, str(SIM_PKG / "ml_planning"))
            sys.path.insert(0, "/opt/apollo/neo/python")
            from model_selection import read_model_selection
            ml_root = runtime / "modules/simulation/ml_planning"
            frozen_config = snapshot(SIM_PKG / "ml_planning/conf/ml_planning.pb.txt",
                                     ml_root / "conf/ml_planning.pb.txt")
            # The frozen selector chooses the source version exactly once.
            selected = read_model_selection(frozen_config, SIM_PKG / "ml_planning/models")
            ml_weights = snapshot(selected["weights"], ml_root / "models" / selected["version"] / "unified.weights")
            if digest(ml_weights) != selected["sha256"]:
                raise RuntimeError("ML model weights changed during selection; resubmit the task")
            effective["ml_planning_model"] = dict(selected, frozen_weights=str(ml_weights))
            atomic_json(job_dir / "configuration.json", effective)
            self.update(job_id, effective_configuration=effective)
        self.update(job_id, stage="model_update")
        preflight_plugins(runtime, config["modules"])
        binary = Path(os.environ.get("SIMULATOR_BINARY", "/opt/apollo/neo/bin/simulator_main")).resolve(strict=True)
        if not os.access(binary, os.X_OK):
            raise ValueError("Simulator binary is not executable")
        manifests["simulator_binary"] = digest(binary)
        runtime_libraries = sorted(Path("/opt/apollo/neo/lib/simulation").rglob("*.so"))
        runtime_libraries += sorted(Path("/opt/apollo/neo/lib/modules/simulation").rglob("*.so"))
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
        collision_runs = []
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
                if not _prediction_selected(config["modules"]):
                    inject.append("/apollo/prediction")
                if not {"PLANNING", "ML_PLANNING"}.intersection(config["modules"]):
                    inject.append("/apollo/planning")
            if "ML_PLANNING" in config["modules"]:
                inject.append("/apollo/planning/pad")
            suppress = [MODULES[m][1] for m in config["modules"]]
            if "ML_PLANNING" in config["modules"]:
                suppress += ["/apollo/planning/command_status", "/apollo/planning/reference_line_offset_command_status"]
            fields = {"scenario_id": job_id, "task_dir": str(run_dir), "map_dir": str(map_dir),
                "vehicle_config_path": str(vehicle), "output_record_path": str(output),
                "progress_path": str(progress), "random_seed": config["seed"], "step_ms": config["step_ms"],
                "ego_model": config["model"], "profile_path": str(runtime),
                "cyber_conf_path": str(
                    next(
                        (p for p in (
                            SIM_PKG / "simulator/conf/cyber_sim.pb.conf",
                            ROOT / "modules/simulation/simulator/conf/cyber_sim.pb.conf",
                            ROOT / "simulation/simulator/conf/cyber_sim.pb.conf",
                        ) if p.is_file()),
                        SIM_PKG / "simulator/conf/cyber_sim.pb.conf",
                    ))}
            if config["kind"] == "world":
                # UI timeout_s is the max sim-clock duration. Scenario JSON often
                # defaults to 60s; patch a run-local copy so the config takes effect.
                scenario = json.loads(Path(source).read_text())
                scenario["duration"] = float(config["timeout_s"])
                patched = run_dir / "scenario.timeout.json"
                patched.write_text(json.dumps(scenario, ensure_ascii=False, indent=2) + "\n")
                fields["world_scenario_path"] = str(patched)
            else:
                fields.update(record_paths=str(source), log_start_s=config["begin_s"], log_end_s=config["end_s"])
            def pb(value): return json.dumps(value) if isinstance(value, str) else str(value)
            def simulator_module(name):
                # simulator_main ModuleCatalog only knows PREDICTION/PLANNING/...;
                # fake_prediction reuses that slot with an overridden dag path.
                return {"fake_prediction": "PREDICTION", "ML_PLANNING": "PLANNING"}.get(name, name)
            lines = [f"{key}: {pb(value)}" for key, value in fields.items()]
            lines += ["input_kind: " + ("WORLD" if config["kind"] == "world" else "BAG")]
            lines += [f"runtime_modules: {pb(simulator_module(m))}" for m in config["modules"]]
            lines += [f"dag_paths: {pb(str(runtime / MODULES[m][0]))}" for m in config["modules"]]
            lines += ["channel_policy {"]
            for key, values in (("inject_channels", inject), ("suppress_channels", suppress), ("record_channels", list(dict.fromkeys(inject + suppress)))):
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
            (run_dir / "log").mkdir()
            env["GLOG_log_dir"] = str(run_dir / "log")
            if ml_weights:
                env.pop("ML_PLANNING_WEIGHTS", None)
                env.update(ML_PLANNING_TRACE=str(run_dir / "policy.csv"))
            log = run_dir / "runtime.log"
            # World AFAP can be slower than sim-clock; keep timeout_s as sim duration
            # but give the subprocess a larger wall budget.
            wall_s = config["timeout_s"]
            if config["kind"] == "world":
                wall_s = min(7200, max(config["timeout_s"] * 5, config["timeout_s"] + 120))
            with log.open("wb") as stream:
                child = subprocess.Popen([str(binary), "--task_dir=" + str(run_dir)], cwd=runtime,
                    env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
                with self.lock:
                    self.children[job_id] = child
                start = time.monotonic()
                self.update(job_id, stage="simulation_running", log_path=str(log), process_id=child.pid)
                while child.poll() is None:
                    if job_id in self.cancelled or self.stopping or time.monotonic() - start > wall_s:
                        stop_process(child)
                        try: child.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            stop_process(child, signal.SIGKILL)
                            child.wait()
                        self.check_cancel(job_id)
                        raise TimeoutError("Simulation exceeded its wall-time limit; see runtime.log")
                    if progress.is_file():
                        state = json.loads(progress.read_text())
                        self.update(job_id, progress=state["percent"], simulation=state)
                    time.sleep(.2)
                self.check_cancel(job_id)
                if config["kind"] == "world":
                    report_path = run_dir / "collision.json"
                    report = json.loads(report_path.read_text()) if report_path.is_file() else {
                        "status": "INCOMPLETE", "complete": False, "error": "Missing collision report"}
                    report["run"] = run + 1
                    collision_runs.append(report)
                    early_analysis = {"collision": collision_summary(collision_runs)}
                    atomic_json(job_dir / "analysis.json", early_analysis)
                    self.update(job_id, analysis=early_analysis)
                if child.returncode != 0:
                    # Preserve a readable partial replay when contact causes
                    # the planner/simulator to stop before normal completion.
                    partial = [output] if output.is_file() else sorted(run_dir.glob("simulation.record.*"))
                    self.update(job_id, outputs=outputs + [str(p) for p in partial])
                    with log.open("rb") as tail:
                        tail.seek(max(0, log.stat().st_size - 4000))
                        detail = tail.read().decode(errors="replace")
                    raise RuntimeError(f"Simulator exited {child.returncode}; {detail}")
            self.update(job_id, stage="simulation_end")
            # Prefer a single unsplit file (ResultSink disables Cyber segmenting).
            # Fall back to one segment suffix for older binaries.
            candidates = [output] if output.is_file() else sorted(run_dir.glob("simulation.record.*"))
            if len(candidates) != 1:
                raise RuntimeError(f"Expected one simulation record (no split), found {len(candidates)}: {candidates}")
            outputs.append(str(candidates[0]))
            self.update(job_id, outputs=outputs)
        self.update(job_id, stage="result_analysis")
        analysis = self.analyze_results(job_id, {"config": config, "job_dir": str(job_dir),
            "source": str(source), "outputs": outputs,
            "collision_runs": collision_runs, "effective": effective})
        self.update(job_id, analysis=analysis)
        if config["kind"] == "world" and analysis["collision"]["status"] != "PASS":
            raise RuntimeError("Collision check " + analysis["collision"]["status"] + "; contact evidence retained in Sim result")
        if analysis.get("planning_continuity", {}).get("status") == "FAIL":
            raise RuntimeError("Planning trajectory continuity failed: empty/invalid trajectory or missing publication; see planning_continuity evidence")
        if analysis.get("driving_quality", {}).get("status") == "FAIL":
            raise RuntimeError("Driving quality failed: straight-road offset or repeated lateral reversals; replay evidence retained")
        if "ML_PLANNING" in config["modules"] and analysis["estop_frames"]:
            raise RuntimeError(f"ML Planning reported emergency stop in {analysis['estop_frames']} frames; replay evidence retained")
        if analysis.get("scenario_expectation", {}).get("status") == "FAIL":
            raise RuntimeError("Scenario expectation failed: " + analysis["scenario_expectation"]["expectation"] +
                               "; replay evidence retained")
        if analysis["missing_module_outputs"]:
            raise RuntimeError("Selected modules produced no output: " + ", ".join(analysis["missing_module_outputs"]))
        if {"PLANNING", "ML_PLANNING"}.intersection(config["modules"]) and analysis["valid_planning_frames"] == 0:
            raise RuntimeError("Planning produced no valid trajectory frames; inspect algorithm status and replay the output bag")
        if analysis["determinism"] == "FAIL":
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

    def analyze_results(self, job_id, request):
        job_dir = Path(request["job_dir"])
        request_path = job_dir / "analysis_request.json"
        atomic_json(request_path, request)
        analysis_log = job_dir / "analysis.log"
        with analysis_log.open("wb") as stream:
            child = subprocess.Popen([sys.executable, str(_SIM_DIR / "result_analysis.py"),
                "--request", str(request_path)], stdout=stream, stderr=subprocess.STDOUT,
                start_new_session=True)
            with self.lock:
                self.children[job_id] = child
            self.update(job_id, analysis_process_id=child.pid)
            started = time.monotonic()
            while child.poll() is None:
                if self.stopping or job_id in self.cancelled or time.monotonic()-started > 1800:
                    stop_process(child)
                    try:
                        child.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        stop_process(child, signal.SIGKILL)
                        child.wait()
                    self.check_cancel(job_id)
                    raise TimeoutError("Result analysis exceeded 1800 seconds; see analysis.log")
                time.sleep(.2)
            self.check_cancel(job_id)
            if child.returncode != 0:
                with analysis_log.open("rb") as tail:
                    tail.seek(max(0, analysis_log.stat().st_size-4000))
                    detail = tail.read().decode(errors="replace")
                raise RuntimeError(f"Result analysis exited {child.returncode}; {detail}")
        return json.loads((job_dir / "analysis.json").read_text())

    def close(self):
        with self.lock:
            self.stopping = True
            for child in self.children.values():
                stop_process(child)
            for job_id in self.pending_ids:
                if self.jobs[job_id]["stage"] not in TERMINAL:
                    self.update(job_id, stage="cancelled", error="Simulation service stopped")
            self.pending_ids.clear()
            self.condition.notify_all()
            self.events_condition.notify_all()
        for thread in self.threads:
            thread.join(timeout=10)
        if not any(thread.is_alive() for thread in self.threads):
            self.file_lock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", default=os.environ.get("SIM_TASK_ROOT", str(ROOT / "data/simulation/jobs")))
    parser.add_argument("--events", action="store_true")
    args = parser.parse_args()
    service = TaskService(args.state_dir, workers=30)
    output_lock = threading.Lock()
    def emit(value):
        with output_lock:
            print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)
    def publish():
        revision = None
        try:
            while True:
                event = service.events(revision)
                if event is None:
                    return
                emit(event)
                revision = event["revision"]
                # Coalesce bursts from concurrent workers, without polling jobs.
                time.sleep(0.1)
        except Exception as error:
            emit({"event": "simulation_jobs", "error": str(error)})
    publisher = None
    if args.events:
        publisher = threading.Thread(target=publish, name="simulation-events", daemon=True)
        publisher.start()
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
            emit(result)
    finally:
        service.close()
        if publisher is not None:
            publisher.join()


if __name__ == "__main__":
    main()
