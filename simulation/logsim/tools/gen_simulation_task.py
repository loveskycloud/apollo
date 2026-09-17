#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate SimulationTask task_dir for LogSim."""

import argparse
import os
import textwrap


DEFAULT_INJECT = [
    "/apollo/perception/obstacles",
    "/apollo/localization/pose",
    "/apollo/canbus/chassis",
]

DEFAULT_SUPPRESS = [
    "/apollo/prediction",
    "/apollo/planning",
    "/apollo/control",
]

DEFAULT_RECORD = [
    "/apollo/prediction",
    "/apollo/planning",
    "/apollo/control",
]


def dag_paths_for_modules(modules):
    paths = []
    if "PREDICTION" in modules:
        paths.append("modules/prediction/dag/prediction.dag")
    if "PLANNING" in modules:
        paths.append("modules/planning/planning_component/dag/planning.dag")
    if "CONTROL" in modules:
        paths.append("modules/control/control_component/dag/control.dag")
    return paths


def write_scenario_pb(task_dir, scenario_id, modules, map_dir, vehicle):
    enabled_lines = "\n".join(f'  enabled_modules: {m}' for m in modules)
    content = textwrap.dedent(
        f"""\
        name: "{scenario_id}"
        scenario_id: "{scenario_id}"
        mode: LOGSIM
        map_dir: "{map_dir}"
        vehicle: "{vehicle}"
        {enabled_lines}
        """
    )
    # Fix indentation of enabled_modules block
    lines = [
        f'name: "{scenario_id}"',
        f'scenario_id: "{scenario_id}"',
        "mode: LOGSIM",
        f'map_dir: "{map_dir}"',
        f'vehicle: "{vehicle}"',
    ]
    for m in modules:
        lines.append(f"enabled_modules: {m}")
    with open(os.path.join(task_dir, "scenario.pb.txt"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def write_task_pb(task_dir, scenario_id, record_paths, modules, map_dir,
                  vehicle_config_path, output_record_dir="data/simulation"):
    dags = dag_paths_for_modules(modules)
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
          inject_channels: {DEFAULT_INJECT!r}
          suppress_channels: {DEFAULT_SUPPRESS!r}
          record_channels: {DEFAULT_RECORD!r}
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
    os.makedirs(args.task_dir, exist_ok=True)
    os.makedirs(os.path.join(args.task_dir, "output"), exist_ok=True)
    os.makedirs(args.output_record_dir, exist_ok=True)
    modules = [m.strip() for m in args.override_runtime_modules.split(",")]
    write_task_pb(args.task_dir, args.scenario_id, args.record_path, modules,
                  args.map_dir, args.vehicle_config_path,
                  args.output_record_dir)
    write_scenario_pb(args.task_dir, args.scenario_id, modules, args.map_dir,
                      args.vehicle_config_path)
    with open(os.path.join(args.task_dir, "runtime.gflags"), "w") as f:
        f.write(f"--map_dir={args.map_dir}\n")
        f.write(f"--vehicle_config_path={args.vehicle_config_path}\n")
    with open(os.path.join(args.task_dir, "modules.dag.list"), "w") as f:
        for p in dag_paths_for_modules(modules):
            f.write(p + "\n")
    print(f"Generated task_dir: {args.task_dir}")
    print(f"Sim bags will go under: {args.output_record_dir}/<scenario_id>/")


if __name__ == "__main__":
    main()
