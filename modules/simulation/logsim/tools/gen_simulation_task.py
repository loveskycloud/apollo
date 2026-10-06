#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate SimulationTask task_dir for LogSim."""

import argparse
import json
import os
import textwrap

from simulation_manifest import load_manifest, record_topics, channel_policy
from perception_pipeline import DEFAULT_LAUNCH, prepare_perception_pipeline


def dag_paths_for_modules(modules, prepared_dags=None):
    dags = {
        "PERCEPTION": "modules/perception/lidar_output/dag/lidar_output.dag",
        "LOCALIZATION": "modules/localization/dag/dag_streaming_rtk_localization.dag",
        "PREDICTION": "modules/prediction/dag/prediction.dag",
        "PLANNING": "modules/planning/planning_component/dag/planning.dag",
        "CONTROL": "modules/control/control_component/dag/control.dag",
        "ROUTING": "modules/routing/dag/routing.dag",
    }
    dags.update(prepared_dags or {})
    return [dags[module] for module in modules]


def write_scenario_pb(task_dir, scenario_id, modules, map_dir, vehicle, prepared_dags=None):
    lines = [
        f'name: "{scenario_id}"',
        f'scenario_id: "{scenario_id}"',
        "mode: LOGSIM",
        f'map_dir: "{map_dir}"',
        f'vehicle: "{vehicle}"',
    ]
    for m in modules:
        lines.append(f"enabled_modules: {m}")
        lines.append(f'module_specs {{ type: {m} dag_path: "{dag_paths_for_modules([m], prepared_dags)[0]}" }}')
    with open(os.path.join(task_dir, "scenario.pb.txt"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def write_task_pb(task_dir, scenario_id, record_paths, modules, map_dir,
                  vehicle_config_path, output_record_dir="data/simulation",
                  manifest=None, prepared_dags=None):
    dags = dag_paths_for_modules(modules, prepared_dags)
    policy = channel_policy(modules, manifest if manifest is not None else load_manifest())
    mappings = "\n".join(
        f'bag_topic_mappings {{ source_topic: "{source}" target_topic: "{target}" }}'
        for source, target in policy["bag_topic_mappings"].items())
    content = textwrap.dedent(
        f"""\
        scenario_id: "{scenario_id}"
        task_dir: "{task_dir}"
        record_paths: {record_paths!r}
        runtime_modules: {modules!r}
        dag_paths: {dags!r}
        map_dir: "{map_dir}"
        vehicle_config_path: "{vehicle_config_path}"
        cyber_conf_path: "modules/simulation/simulator/conf"
        output_record_dir: "{output_record_dir}"
        channel_policy {{
          inject_channels: {policy['inject_channels']!r}
          suppress_channels: {policy['suppress_channels']!r}
          record_channels: {policy['record_channels']!r}
          {mappings}
        }}
        """
    ).replace("'", '"')
    with open(os.path.join(task_dir, "task.pb.txt"), "w", encoding="utf-8") as f:
        f.write(content)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--task-dir", required=True)
    parser.add_argument("--record-path", action="append", default=[])
    parser.add_argument("--perception-launch", default=DEFAULT_LAUNCH,
                        help="Perception launch or DAG; default: current lidar launch")
    parser.add_argument(
        "--override-runtime-modules",
        default="PLANNING,CONTROL,PREDICTION",
    )
    parser.add_argument("--map-dir", default="modules/map/data/sunnyvale")
    parser.add_argument(
        "--vehicle-config-path",
        default="modules/common/data/vehicle_param.pb.txt",
    )
    parser.add_argument(
        "--output-record-dir",
        default="data/simulation",
        help="Directory for sim output bags (default: data/simulation)",
    )
    args = parser.parse_args()
    manifest = load_manifest()
    modules = [m.strip() for m in args.override_runtime_modules.split(",")]
    if len(set(modules)) != len(modules) or not modules:
        parser.error("Choose distinct runtime modules")
    try:
        dag_paths_for_modules(modules)
    except KeyError as error:
        parser.error(f"Unsupported CLI runtime module: {error.args[0]}")
    record_topics(modules, manifest)
    os.makedirs(args.task_dir, exist_ok=True)
    os.makedirs(os.path.join(args.task_dir, "output"), exist_ok=True)
    os.makedirs(args.output_record_dir, exist_ok=True)
    prepared_dags = {}
    if "PERCEPTION" in modules:
        from pathlib import Path
        target = Path(args.task_dir).resolve() / "perception.dag"
        manifest = prepare_perception_pipeline(os.getcwd(), args.perception_launch, target, manifest)
        prepared_dags = {"PERCEPTION": str(target)}
    write_task_pb(args.task_dir, args.scenario_id, args.record_path, modules,
                  args.map_dir, args.vehicle_config_path,
                  args.output_record_dir, manifest, prepared_dags)
    with open(os.path.join(args.task_dir, "simulation.manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")
    write_scenario_pb(args.task_dir, args.scenario_id, modules, args.map_dir,
                      args.vehicle_config_path, prepared_dags)
    with open(os.path.join(args.task_dir, "runtime.gflags"), "w") as f:
        f.write(f"--map_dir={args.map_dir}\n")
        f.write(f"--vehicle_config_path={args.vehicle_config_path}\n")
    with open(os.path.join(args.task_dir, "modules.dag.list"), "w") as f:
        for p in dag_paths_for_modules(modules, prepared_dags):
            f.write(p + "\n")
    print(f"Generated task_dir: {args.task_dir}")
    print(f"Sim bags will go under: {args.output_record_dir}/<scenario_id>/")


if __name__ == "__main__":
    main()
