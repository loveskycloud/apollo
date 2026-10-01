#!/usr/bin/env python3
"""Make a map-matched test scenario from real bag localization + routing command.

This is test data generation, not a runtime fallback or synthetic algorithm output.
"""
import argparse
import json
from pathlib import Path
from task_service import messages
from modules.common_msgs.localization_msgs.localization_pb2 import LocalizationEstimate
from modules.common_msgs.planning_msgs.planning_command_pb2 import PlanningCommand

parser = argparse.ArgumentParser()
parser.add_argument("--bag", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
pose = command = None
for channel, stamp, data in messages(args.bag):
    if channel == "/apollo/localization/pose" and pose is None:
        pose = LocalizationEstimate.FromString(data).pose
    if channel in ("/apollo/planning/command", "/apollo/planning_command_history") and command is None:
        command = PlanningCommand.FromString(data)
assert pose is not None and command is not None
waypoints = command.lane_follow_command.routing_request.waypoint
assert len(waypoints) >= 2, "Real routing command has no complete route"
end = waypoints[-1]
scenario = {"id": "od-hq-real-route-test", "name": "OD HQ real route / 3 s", "duration": 3,
    "ego": {"id": "ego", "position": {"x": pose.position.x, "y": pose.position.y, "z": pose.position.z},
            "heading": pose.heading, "activeRouteId": "real-route",
            "routes": [{"id": "real-route", "waypoints": [
                {"position": {"x": end.pose.x, "y": end.pose.y},
                 **({"heading": end.heading} if end.HasField("heading") else {})}]}]},
    "agents": [{"id": "debug-static", "type": "AGENT_TYPE_STATIC", "enabled": False,
                "position": {"x": pose.position.x + 20, "y": pose.position.y + 20, "z": pose.position.z},
                "size": {"x": 1, "y": 1, "z": 2}}],
    "triggers": [{"id": "appear", "type": "TRIGGER_TYPE_TIME", "time": 1,
                  "actions": [{"kind": "ACTION_ENABLE", "targetAgentId": "debug-static"}]},
                 {"id": "disappear", "type": "TRIGGER_TYPE_TIME", "time": 2,
                  "actions": [{"kind": "ACTION_DISABLE", "targetAgentId": "debug-static"}]}]}
Path(args.output).write_text(json.dumps(scenario, indent=2))
print(args.output)
