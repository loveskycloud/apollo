#!/usr/bin/env python3
"""Verify an extracted binary package inside a fresh Apollo container.

This uses only Python's standard library and the packaged native programs.
Use --map/--scene/--vehicle-profile to also exercise a real WorldSim run.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

SYSTEM = re.compile(r"^(ld-linux.*|lib(c|m|pthread|dl|rt|resolv|util|anl)\.so\..*)$")
DRIVER = re.compile(r"^lib(cuda|nvidia-[\w-]+)\.so(?:\..*)?$")


def run(command, env, logfile=None, timeout=120):
    result = subprocess.run(command, env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout)
    if logfile:
        logfile.write_text(result.stdout)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {command}\n{result.stdout[-12000:]}")
    return result.stdout


def overlay(base, profile, destination):
    destination.mkdir(parents=True, exist_ok=True)
    names = {p.name for p in base.iterdir()} if base.is_dir() else set()
    names |= {p.name for p in profile.iterdir()} if profile.is_dir() else set()
    for name in sorted(names):
        src, replacement, dest = base / name, profile / name, destination / name
        if replacement.is_dir():
            overlay(src, replacement, dest)
        elif replacement.is_file():
            shutil.copy2(replacement, dest)
        elif src.exists():
            dest.symlink_to(src)


def verify_private_libraries(root):
    # Run in a separate process with the supplied setup environment. Cyber's
    # loader uses GLOBAL; NOW also checks symbols in functions not called here.
    import ctypes
    manifest = json.loads((root / "manifest.json").read_text())
    names = sorted(name for name in manifest["library_sources"]
                   if name.startswith("libmodules__") and any(
                       name.endswith("__" + private)
                       for private in manifest.get("private_library_names", [])))
    handles = [ctypes.CDLL(str(root / "lib/runtime" / name),
                          mode=os.RTLD_NOW | os.RTLD_GLOBAL) for name in names]
    if len({handle._handle for handle in handles}) != len(handles):
        raise RuntimeError("Distinct private libraries reused one loader handle")
    report = {"result": "PASS", "mode": "RTLD_NOW | RTLD_GLOBAL",
              "process_count": 1, "libraries": names, "distinct_handles": len(handles)}
    evidence = root / "data/binary-verification"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "private-libraries.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


def verify_image_libraries(manifest):
    checked = {}
    report = {"sha256_matches": [], "abi_compatible": []}
    for name, requirement in manifest.get("image_libraries", {}).items():
        path = Path(requirement["path"])
        if not path.is_file():
            raise RuntimeError(f"Apollo image is missing required {name}: {path}")
        if path not in checked:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            checked[path] = digest.hexdigest()
        if checked[path] != requirement["sha256"]:
            required = requirement.get("required_symbols", [])
            if requirement.get("require_exact") or not required:
                raise RuntimeError(f"Apollo image provides a different {name}: {path}; use the recorded image/dependency versions")
            output = subprocess.check_output(["readelf", "--wide", "--dyn-syms", path], text=True)
            defined = set()
            for line in output.splitlines():
                fields = line.split()
                if len(fields) >= 8 and fields[0].endswith(":") and fields[4] in {"GLOBAL", "WEAK"} and fields[6] != "UND":
                    symbol = fields[7]
                    defined.add(symbol.replace("@@", "@"))
                    if "@@" in symbol or "@" not in symbol:
                        defined.add(symbol.split("@")[0])
            missing = set(required) - defined
            if missing:
                raise RuntimeError(f"Image {name} lacks required ELF symbols/versions: {sorted(missing)[:20]}")
            report["abi_compatible"].append(name)
        else:
            report["sha256_matches"].append(name)
    return report


def package_environment(root):
    data = subprocess.check_output(["bash", "--noprofile", "--norc", "-c",
        'source "$1/setup.bash"; env -0', "verify", str(root)], env={"PATH": "/usr/bin:/bin"})
    return dict(item.decode().split("=", 1) for item in data.split(b"\0") if b"=" in item)


def verify_replay(url, record, evidence, name):
    report_path = evidence / f'replay-{name}.json'
    report = {'record': str(record)}

    def request(route, body=None):
        req = urllib.request.Request(url+route, body.encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'Replay API {route}: HTTP {error.code}: {error.read().decode()}') from error
        if result.get('status') == 'error':
            raise RuntimeError(f'Replay API {route}: {result}')
        return result

    report['conversion'] = request('/api/convert_record', json.dumps({'path': str(record), 'map': ''}))
    deadline = time.monotonic()+180
    while report['conversion']['status'] not in ('done', 'ready', 'error'):
        report_path.write_text(json.dumps(report, indent=2)+'\n')
        if time.monotonic() >= deadline:
            raise RuntimeError(f'Replay conversion timed out: {report_path}')
        time.sleep(.5)
        report['conversion'] = request('/api/convert_record?job_id='+report['conversion']['job_id'])
    report_path.write_text(json.dumps(report, indent=2)+'\n')
    mcap = Path(report['conversion']['output_path'])
    if not mcap.is_file() or mcap.stat().st_size == 0:
        raise RuntimeError('Replay conversion has no MCAP output')
    topics = report['topics'] = request('/api/mcap_topics?path='+urllib.parse.quote(str(mcap)))
    selected = ['/apollo/localization/pose', '/apollo/planning']
    if not set(selected).issubset(topics['topics']) or not topics['end_ns'] > topics['begin_ns']:
        raise RuntimeError(f'Replay lacks planning/pose or timestamps: {topics}')
    for topic in selected:
        decoded = request('/api/topic_debug', f'{mcap}\n{topic}\n{topics["begin_ns"]}')
        if decoded.get('status') != 'ok' or not decoded.get('debug_string'):
            raise RuntimeError(f'Replay topic decode returned no payload: {decoded}')
        report.setdefault('decoded_topics', {})[topic] = decoded
    report['playback'] = request('/api/playback_window', '\n'.join([
        str(mcap), str(topics['begin_ns']), str(min(topics['end_ns'], topics['begin_ns']+2_000_000_000)),
        '1', *selected]))
    if report['playback'].get('status') != 'ok' or not report['playback'].get('receipt'):
        raise RuntimeError(f'Replay window did not reach the gRPC store: {report["playback"]}')
    report['result'] = 'PASS'
    report_path.write_text(json.dumps(report, indent=2)+'\n')
    return {'result': 'PASS', 'conversion': 'PASS', 'topic_decode': 'PASS',
            'playback_window': 'PASS', 'mcap_bytes': mcap.stat().st_size, 'evidence': str(report_path)}


def verify_simulation_api(url, root, env, evidence, simulation=None, record=None, ml_scene=None):
    """Exercise the same worker and HTTP commands used by the Sim sidebar."""
    def request(body):
        call = urllib.request.Request(url + "/api/sim", json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(call, timeout=60) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Simulation API HTTP {error.code}: {error.read().decode()}") from error
        if result.get("status") != "ok":
            raise RuntimeError(f"Simulation API rejected {body['action']}: {result}")
        return result

    report = {"catalog": request({"action": "catalog"}), "initial_jobs": request({"action": "list"})}
    report_path = evidence / "simulation-api.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    if simulation:
        scene, map_dir, profile = (root / value for value in simulation)
        base_config = {"kind": "bag" if record else "world", "source": str(record or scene), "map": str(map_dir),
            "vehicle": str(profile / "modules/common/data/vehicle_param.pb.txt"), "profile": str(profile),
            "modules": ["PREDICTION", "PLANNING", "CONTROL", "ROUTING"],
            "model": "kinematic_control", "step_ms": 10, "repeat": 1 if record else 2,
            "seed": 1, "timeout_s": 120 if record else 90}
        configs = {'pnc': base_config}
        if not record:
            configs['ml'] = dict(base_config, modules=['ML_PLANNING', 'ROUTING'], model='perfect_planning')
            if ml_scene:
                configs['ml-obstacle'] = dict(configs['ml'], source=str(root / ml_scene))
        report['tasks'] = {}
        for name, config in configs.items():
            task = report['tasks'][name] = {'request': config}
            task['enqueue'] = request({"action": "enqueue", "config": config})
            deadline = time.monotonic() + 600
            while True:
                jobs = request({"action": "list"})["jobs"]
                job = next(item for item in jobs if item["id"] == task['enqueue']['id'])
                task['job'] = job
                report_path.write_text(json.dumps(report, indent=2) + "\n")
                if job["stage"] in ("completed", "failed", "cancelled", "interrupted"):
                    break
                if time.monotonic() >= deadline:
                    request({"action": "cancel", "id": job["id"]})
                    raise RuntimeError(f"Simulation API {name} timed out; see {report_path}")
                time.sleep(0.5)
            if job["stage"] != "completed" or job.get("execution", {}).get("status") != "PASS":
                raise RuntimeError(f"Simulation API {name} {job['stage']}: {job.get('error')}; see {report_path}")
            if not job.get("outputs") or not all(Path(path).is_file() and Path(path).stat().st_size for path in job["outputs"]):
                raise RuntimeError("Simulation API completed without nonempty output records")
            analysis = job.get("analysis") or {}
            if not analysis.get("topic_message_counts") or analysis.get("missing_module_outputs"):
                raise RuntimeError(f"Simulation API analysis has missing module outputs: {analysis}")
            if not record:
                for check in ('collision', 'planning_continuity', 'scenario_expectation', 'quality_metrics'):
                    if analysis.get(check, {}).get('status') != 'PASS':
                        raise RuntimeError(f'{name} missing successful {check}: {analysis}')
                if analysis.get('determinism') != 'PASS' or analysis.get('estop_frames'):
                    raise RuntimeError(f'{name} failed repeat/estop checks: {analysis}')
                if 'ML_PLANNING' in config['modules'] and analysis.get('driving_quality', {}).get('status') != 'PASS':
                    raise RuntimeError(f'{name} failed ML driving quality: {analysis}')
            job_dir = Path(env["SIM_TASK_ROOT"]) / job["id"]
            runtime_manifest = json.loads((job_dir / "manifest.json").read_text())
            if Path(runtime_manifest["runtime_binary"]) != root / "bin/simulator_main":
                raise RuntimeError("Simulation task used a different distribution's executable")
            if not all(Path(path).is_relative_to(root) for path in runtime_manifest["runtime_libraries"]):
                raise RuntimeError("Simulation task fingerprint includes another distribution's libraries")
            task['runtime_binary'] = runtime_manifest['runtime_binary']
            if not record:
                task['algorithm_health'] = verify_algorithm_health(root, env, evidence, job, name)
            if 'ML_PLANNING' in config['modules']:
                run(['python3', str(root/'modules/simulation/ml_planning/verify_policy.py'),
                     '--task-dir', str(job_dir)], env, evidence/f'policy-parity-{name}.log')
                task['policy_parity'] = json.loads((job_dir/'policy-parity.json').read_text())
            task['replay'] = verify_replay(url, Path(job['outputs'][0]), evidence, name)
            task['result'] = 'PASS'
            report_path.write_text(json.dumps(report, indent=2) + "\n")
    report["result"] = "PASS"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    return {"result": "PASS", "catalog": "PASS", "list": "PASS",
            **({"tasks": {name: {'result': task['result'], 'task_id': task['job']['id'],
                'stage': task['job']['stage'], 'determinism': task['job']['analysis']['determinism'],
                'goal_distance_m': task['job']['analysis'].get('goal_distance_m'),
                'topic_message_counts': task['job']['analysis']['topic_message_counts'], 'replay': task['replay']}
                for name, task in report['tasks'].items()}}
               if simulation else {})}


def verify_algorithm_health(root, env, evidence, job, name):
    # Check every repeat, including Control's initial wait for its first
    # observed trajectory. Keep these status messages in the evidence.
    script = r'''
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root/'modules/simulation/simulator'))
from task_service import messages
from modules.common_msgs.planning_msgs.planning_pb2 import ADCTrajectory
from modules.common_msgs.control_msgs.control_cmd_pb2 import ControlCommand
report = []
for output in sys.argv[2:]:
    first_plan = None
    plans, controls, startup = 0, 0, []
    for channel, stamp, payload in messages(output):
        if channel == '/apollo/planning':
            msg = ADCTrajectory.FromString(payload)
            if msg.header.status.error_code or not msg.trajectory_point or msg.decision.main_decision.HasField('not_ready'):
                raise RuntimeError('Unhealthy Planning: '+str(msg.header.status))
            first_plan = stamp if first_plan is None else first_plan
            plans += 1
        elif channel == '/apollo/control':
            msg = ControlCommand.FromString(payload)
            if msg.header.status.error_code:
                # Control observes the first trajectory at the following
                # 100 ms planning cycle; any later error is a failure.
                if (first_plan is None or stamp >= first_plan + 100000000 or
                    msg.header.status.error_code != 1002 or
                    msg.header.status.msg != 'planning has no trajectory point. planning_seq_num:0'):
                    raise RuntimeError('Unhealthy Control at '+str(stamp)+': '+str(msg.header.status))
                startup.append({'stamp_ns': stamp, 'status': str(msg.header.status)})
            else:
                controls += 1
    if not plans:
        raise RuntimeError('Missing Planning messages')
    report.append({'record': output, 'planning_ok': plans, 'control_ok': controls,
        'control_initial_wait': startup, 'control_errors_after_initial_wait': 0})
print(json.dumps({'result': 'PASS', 'runs': report}, indent=2))
'''
    report = json.loads(run(['python3', '-c', script, str(root), *job['outputs']], env))
    (evidence / f'algorithm-health-{name}.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def verify_web_monitor(root, env, evidence, simulation=None, record=None, ml_scene=None):
    viewer = root / "bin/rerun"
    layouts = root / "modules/simulation/web_monitor/layouts"
    if not os.access(viewer, os.X_OK) or not all((layouts / name).is_file()
            for name in ("planner.rbl", "perception.rbl", "control.rbl")):
        raise RuntimeError("Web monitor needs packaged bin/rerun and planner/perception/control layouts")
    version = run([str(viewer), "--version"], env, evidence / "rerun-version.log").strip()
    env = dict(env, SIM_TASK_ROOT=str(evidence / "web-monitor-jobs"))
    url = "http://127.0.0.1:9090"
    assets = {}
    log_path = evidence / "web-monitor.log"
    with log_path.open("w") as log_stream:
        process = subprocess.Popen([str(root / "bin/web_monitor_main"), "--grpc_host=127.0.0.1"],
            cwd="/tmp", env=env, stdout=log_stream, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 45
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Web monitor exited before serving HTTP:\n" + log_path.read_text())
                try:
                    with urllib.request.urlopen(url + "/", timeout=1) as response:
                        html = response.read()
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Web monitor HTTP startup timed out:\n" + log_path.read_text())
                    time.sleep(0.2)
            if b"sim scope" not in html.lower():
                raise RuntimeError("Web monitor served a different viewer page")
            assets["/"] = {"bytes": len(html), "sha256": hashlib.sha256(html).hexdigest()}
            for route in ("/re_viewer.js", "/re_viewer_bg.wasm", "/favicon.ico"):
                with urllib.request.urlopen(url + route, timeout=30) as response:
                    content = response.read()
                    content_type = response.headers.get("Content-Type", "")
                if not content or (route.endswith(".wasm") and content[:4] != b"\0asm"):
                    raise RuntimeError(f"Web monitor served an invalid asset: {route}")
                assets[route] = {"bytes": len(content), "content_type": content_type,
                                 "sha256": hashlib.sha256(content).hexdigest()}
            with socket.create_connection(("127.0.0.1", 9876), timeout=5):
                pass
            simulation_report = verify_simulation_api(url, root, env, evidence, simulation, record, ml_scene)
            if process.poll() is not None:
                raise RuntimeError("Web monitor exited during HTTP/asset checks")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    report = {"result": "PASS", "rerun_version": version,
              "cwd": "/tmp", "http_port": 9090, "grpc_port": 9876, "assets": assets,
              "simulation_api": simulation_report}
    (evidence / "web-monitor.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def verify(root, args):
    root = root.resolve()
    if (root / "bazel-bin").exists() or (root / "bazel-bin").is_symlink():
        raise RuntimeError("Runtime package contains a bazel-bin build-tree alias")
    empty_runtime = [path for path in (root / "lib/runtime").rglob("*")
                     if path.is_dir() and not any(path.iterdir())]
    if empty_runtime:
        raise RuntimeError(f"Empty runtime library directories: {empty_runtime[:10]}")
    if any(path.is_dir() for path in (root / "lib/runtime").iterdir()):
        raise RuntimeError("Runtime libraries must be a flat directory with one selected library per SONAME")
    evidence = root / "data/binary-verification"
    evidence.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((root / "manifest.json").read_text())
    image_report = verify_image_libraries(manifest)
    (evidence / "image-libraries.json").write_text(json.dumps(image_report, indent=2) + "\n")
    print(f"[verify_binary] Image libraries passed: {len(manifest.get('image_libraries', {}))}", flush=True)
    # Obtain only the supplied setup environment; avoid .bashrc/old Apollo setup.
    env = package_environment(root)
    if env["APOLLO_DISTRIBUTION_HOME"] != str(root):
        raise RuntimeError("Setup did not point to extraction directory")
    checked_files = 0
    for line in (root / "SHA256SUMS").read_text().splitlines():
        expected, filename = line.split("  ", 1)
        digest = hashlib.sha256()
        with (root / filename).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise RuntimeError(f"Checksum mismatch: {filename}")
        checked_files += 1
    print(f"[verify_binary] Checksums passed: {checked_files} files", flush=True)
    elfs = []
    for path in root.rglob("*"):
        if path.is_symlink():
            if not path.exists() or not path.resolve().is_relative_to(root):
                raise RuntimeError(f"Package-external or broken symlink: {path}")
        elif path.is_file():
            with path.open("rb") as stream:
                if stream.read(4) == b"\x7fELF":
                    elfs.append(path)
    def check(path):
        result = subprocess.run(["ldd", path], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for line in result.stdout.splitlines():
            if "not found" in line:
                name = line.split()[0]
                if args.allow_host_drivers and DRIVER.fullmatch(name):
                    continue
                raise RuntimeError(f"{path}: {line}")
            match = re.match(r"\s*(\S+)\s+=>\s+(/.+?)\s+\(0x", line)
            if match and not Path(match.group(2)).resolve().is_relative_to(root):
                provider = manifest.get("image_libraries", {}).get(match.group(1))
                if provider and Path(match.group(2)).resolve() == Path(provider["path"]).resolve():
                    continue
                if not SYSTEM.fullmatch(match.group(1)) and not DRIVER.fullmatch(match.group(1)):
                    raise RuntimeError(f"Library resolved outside package: {path}: {line}")
        return str(path.relative_to(root)) + "\n" + result.stdout
    with ThreadPoolExecutor(max_workers=6) as pool:
        print(f"[verify_binary] Checking {len(elfs)} ELF files", flush=True)
        (evidence / "ldd.log").write_text("\n".join(pool.map(check, sorted(elfs))))
    print("[verify_binary] Dependencies passed; starting runtime checks", flush=True)
    run([str(root / "bin/cyber_recorder"), "info", "--help"], env, evidence / "cyber-help.log")
    if (root / "bin/cyber_channel").exists():
        run([str(root / "bin/cyber_channel"), "list", "--help"], env, evidence / "cyber-python-help.log")
    report = {"profile": manifest["profile"], "elf_files": len(elfs),
              "layout": "PASS", "empty_runtime_directories": 0,
              "checksum_files": checked_files, "setup": "PASS", "dependencies": "PASS",
              "image_libraries_checked": len(manifest.get("image_libraries", {})),
              "image_libraries": image_report,
              "cyber_recorder": "PASS", "extraction_directory": str(root)}
    if manifest.get("private_library_names"):
        run([sys.executable, str(Path(__file__).resolve()), str(root), "--private-libraries-only"],
            env, evidence / "private-libraries.log")
        report["private_libraries"] = "PASS"
    record = None
    if args.scene or args.map or args.vehicle_profile:
        if not all((args.scene, args.map, args.vehicle_profile)):
            raise ValueError("Simulation verification needs --scene, --map and --vehicle-profile together")
        scene_path, map_path, profile = (root / args.scene, root / args.map, root / args.vehicle_profile)
        task = Path(tempfile.mkdtemp(prefix="task-", dir=evidence))
        runtime = task / "runtime"
        overlay(root / "modules", profile / "modules", runtime / "modules")
        (runtime / "cyber").symlink_to(root / "cyber")
        scene = json.loads(scene_path.read_text())
        scene["duration"] = args.duration
        scene_out = task / "world.json"
        scene_out.write_text(json.dumps(scene))
        vehicle = runtime / "modules/common/data/vehicle_param.pb.txt"
        record = task / "output.record"
        values = {"scenario_id": "binary-clean-smoke", "input_kind": "WORLD",
                  "world_scenario_path": str(scene_out), "world_start_ns": 1000000000,
                  "step_ms": 10, "random_seed": 1, "sim_mode": "DEFAULT",
                  "ego_model": "kinematic_control", "map_dir": str(map_path),
                  "vehicle_config_path": str(vehicle), "profile_path": str(runtime),
                  "output_record_path": str(record), "progress_path": str(task / "progress.json")}
        enum_fields = {"input_kind", "sim_mode"}
        lines = [f"{key}: {value if key in enum_fields or isinstance(value, int) else json.dumps(value)}"
                 for key, value in values.items()]
        modules = ("PREDICTION", "PLANNING", "CONTROL", "ROUTING")
        lines += [f'runtime_modules: "{name}"' for name in modules]
        contract = json.loads((root / "modules/simulation/simulator/conf/simulation.manifest.json").read_text())
        entries = {entry["name"]: entry for entry in contract["modules"]}
        suppress = {topic for name in modules for topic in entries[name]["outputs"] if not topic.startswith("/bag/")}
        inject = list(contract["common"]) + [topic for name in modules for topic in entries[name]["inputs"]]
        inject += ["/apollo/perception/obstacles", "/apollo/raw_routing_request", "/apollo/planning/command"]
        inject = sorted({topic for topic in inject if not topic.startswith("/bag/") and topic not in suppress})
        record_channels = set(inject) | suppress
        lines.append("channel_policy {")
        for key, topics in (("inject_channels", inject), ("suppress_channels", sorted(suppress)),
                            ("record_channels", sorted(record_channels))):
            lines += [f"  {key}: {json.dumps(topic)}" for topic in topics]
        lines.append("}")
        (task / "task.pb.txt").write_text("\n".join(lines) + "\n")
        output = run([str(root / "bin/simulator_main"), "--task_dir=" + str(task)], env,
                     evidence / "simulation.log", timeout=300)
        if "simulator_main finished, exit=0" not in output or not record.is_file():
            raise RuntimeError("Simulation did not complete or output record is missing")
        info = run([str(root / "bin/cyber_recorder"), "info", str(record)], env, evidence / "record-info.log")
        for channel in ("/apollo/planning", "/apollo/control", "/apollo/prediction", "/apollo/localization/pose"):
            if channel not in info:
                raise RuntimeError(f"Simulation record has no {channel}")
        report.update(simulation="PASS", simulation_duration_s=args.duration,
                      record_bytes=record.stat().st_size, record_info=info)
    if (root / "bin/web_monitor_main").is_file():
        simulation = (args.scene, args.map, args.vehicle_profile) if all((args.scene, args.map, args.vehicle_profile)) else None
        api_record = record if args.web_monitor_task_kind == "bag" else None
        report["web_monitor"] = verify_web_monitor(root, env, evidence, simulation, api_record, args.ml_scene)
    (evidence / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--map")
    parser.add_argument("--scene")
    parser.add_argument("--ml-scene", help="Additional WORLD obstacle scenario for ML acceptance")
    parser.add_argument("--vehicle-profile")
    parser.add_argument("--duration", type=float, default=3)
    parser.add_argument("--web-monitor-task-kind", choices=("world", "bag"), default="world",
        help="Web Monitor API input: WORLD scene or BAG generated by the direct simulation check")
    parser.add_argument("--allow-host-drivers", action="store_true")
    parser.add_argument("--private-libraries-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.root = args.root.resolve()
    os.chdir(args.root)
    if args.private_libraries_only:
        verify_private_libraries(args.root)
    else:
        verify(args.root, args)
