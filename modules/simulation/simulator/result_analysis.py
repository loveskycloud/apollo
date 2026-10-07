#!/usr/bin/env python3
"""Analyze one frozen simulation task in its own cancellable CPU process."""
import argparse
import json
import math
import resource
import os
import sys
import time
from pathlib import Path
from planning_continuity import PlanningContinuity
from quality_metrics import evaluate as evaluate_quality
from scenario_evaluation import pnc_destination_parameters
from task_service import (MODULES, analyze_trace, atomic_json, collision_summary,
                          compare, evaluate_record, load_evaluation, messages)


def analyze(request):
    config = request["config"]
    job_dir = Path(request["job_dir"])
    source = Path(request["source"])
    outputs = request["outputs"]
    collision_runs = request["collision_runs"]
    effective = request["effective"]
    counts = {}
    stamps = {}
    status_counts = {}
    healthy_plans = 0
    estop_frames = 0
    first_pose = last_pose = None
    last_pose_payload = None
    continuity = PlanningContinuity()
    sys.path.insert(0, str(Path(os.environ.get("APOLLO_DISTRIBUTION_HOME", "/opt/apollo/neo")) / "python"))
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
            if channel == "/apollo/planning" and msg.estop.is_estop:
                estop_frames += 1
            if channel == "/apollo/planning":
                continuity.planning(stamp, msg)
            code = msg.header.status.error_code
            key = f"{channel}:{code}"
            status_counts[key] = status_counts.get(key, 0) + 1
            if channel == "/apollo/planning" and code == 0 and msg.trajectory_point and not msg.decision.main_decision.HasField("not_ready"):
                healthy_plans += 1
        if channel == "/apollo/localization/pose":
            continuity.world(stamp)
            # Counts/clock checks still cover every pose. Arrival uses only
            # the first and last, so avoid decoding 18,000 full poses per
            # 180 s scenario while ten jobs compete for the Python GIL.
            if first_pose is None:
                p = LocalizationEstimate.FromString(payload).pose.position
                first_pose = (p.x, p.y)
            last_pose_payload = payload
    if last_pose_payload is not None:
        p = LocalizationEstimate.FromString(last_pose_payload).pose.position
        last_pose = (p.x, p.y)
    missing = [MODULES[m][1] for m in config["modules"] if not counts.get(MODULES[m][1])]
    comparisons = [compare(outputs[0], item, algorithm=True) for item in outputs[1:]]
    analysis = {"collision": collision_summary(collision_runs), "topic_message_counts": counts, "missing_module_outputs": missing,
        "effective_configuration": effective,
        "algorithm_status_counts": status_counts, "valid_planning_frames": healthy_plans,
        "estop_frames": estop_frames,
        "ego_displacement_m": math.dist(first_pose, last_pose) if first_pose is not None else None,
        "determinism": "not_tested" if not comparisons else "PASS" if all(c["result"] == "PASS" for c in comparisons) else "FAIL",
        "comparisons": comparisons, "manifest": str(job_dir / "manifest.json"),
        "scope": "Exact ordered messages, nanosecond timestamps and protobuf values; only listed wall profiling fields excluded and map ordering canonicalized; raw differences retained. Same runtime environment."}
    if config["kind"] == "world":
        ego = json.loads(source.read_text())["ego"]
        route = next((r for r in ego.get("routes", [])
                      if r["id"] == ego.get("activeRouteId")), None)
        if route and route.get("waypoints"):
            goal = route["waypoints"][-1]["position"]
            analysis["goal_distance_m"] = (math.dist(last_pose, (goal["x"], goal["y"]))
                                           if last_pose is not None else None)
    parking = config['kind']=='world' and bool(json.loads(source.read_text()).get('ego',{}).get('parkingSpaceId'))
    if "ML_PLANNING" in config["modules"] and not parking:
        quality = [analyze_trace(job_dir / f"run-{i+1}" / "policy.csv") for i in range(config["repeat"])]
        analysis["driving_quality"] = {"status": "FAIL" if any(q["status"] == "FAIL" for q in quality) else
            "PASS" if all(q["status"] == "PASS" for q in quality) else "NOT_EVALUATED", "runs": quality}
    if config["kind"] == "world" and {"PLANNING", "ML_PLANNING"}.intersection(config["modules"]):
        checks = [continuity.result()]
        for output in outputs[1:]:
            check = PlanningContinuity()
            for channel, stamp, payload in messages(output):
                if channel == "/apollo/planning":
                    check.planning(stamp, ADCTrajectory.FromString(payload))
                elif channel == "/apollo/localization/pose":
                    check.world(stamp)
            checks.append(check.result())
        analysis["planning_continuity"] = {
            "status": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL",
            "runs": checks}
        expectation = load_evaluation(source)
        pnc = (pnc_destination_parameters(job_dir / '.runtime', job_dir / 'vehicle/vehicle_param.pb.txt')
               if 'PLANNING' in config['modules'] and not parking else None)
        outcomes = [evaluate_record(output, json.loads(source.read_text()), expectation,
                                   pnc=pnc, map_dir=job_dir / 'map') for output in outputs]
        # Parking's routing endpoint is only the approach lane. The visible
        # goal distance must refer to the audited parking pose instead.
        if parking or pnc is not None:
            analysis['requested_goal_distance_m'] = analysis.get('goal_distance_m')
            analysis['goal_distance_m'] = outcomes[0]['goal_distance_m']
        analysis["scenario_expectation"] = {
            "expectation": expectation["expectation"], "reason": expectation["reason"],
            "status": "PASS" if all(r["status"] == "PASS" for r in outcomes) else "FAIL", "runs": outcomes}
        metrics = [evaluate_quality(output, json.loads(source.read_text()), job_dir / 'map',
                                   job_dir / 'vehicle/vehicle_param.pb.txt', job_dir / f'run-{i+1}/policy.csv', expectation)
                   for i, output in enumerate(outputs)]
        analysis['quality_metrics'] = {
            'version': 'closed-loop-v1',
            'status': 'PASS' if all(m['status']=='PASS' for m in metrics) and analysis['scenario_expectation']['status']=='PASS' else 'FAIL',
            'runs': metrics}
    return analysis


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text())
    started = time.monotonic()
    cpu_started = time.process_time()
    analysis = analyze(request)
    analysis["analysis_resources"] = {
        "wall_s": time.monotonic()-started,
        "cpu_s": time.process_time()-cpu_started,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    atomic_json(Path(request["job_dir"]) / "analysis.json", analysis)


if __name__ == "__main__":
    main()
