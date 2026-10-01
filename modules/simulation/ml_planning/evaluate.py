"""Independent checks on actual WorldSim Cyber records (not training rewards)."""
import csv
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "simulation/logsim/tools"))
from bag_diff import messages
from modules.common_msgs.localization_msgs.localization_pb2 import LocalizationEstimate
from modules.common_msgs.perception_msgs.perception_obstacle_pb2 import PerceptionObstacles
from modules.common_msgs.planning_msgs.planning_pb2 import ADCTrajectory


def box(x, y, yaw, length, width):
    c, s = math.cos(yaw), math.sin(yaw)
    return [(x + u * c - v * s, y + u * s + v * c)
            for u, v in ((length/2, width/2), (-length/2, width/2),
                         (-length/2, -width/2), (length/2, -width/2))]


def overlap(a, b):
    # Separating axis test, independently of the planner's AABB shield.
    for poly in (a, b):
        for p, q in zip(poly, poly[1:] + poly[:1]):
            nx, ny = -(q[1] - p[1]), q[0] - p[0]
            pa, pb = ([x * nx + y * ny for x, y in polygon] for polygon in (a, b))
            if max(pa) < min(pb) or max(pb) < min(pa):
                return False
    return True


def clearance(a, b):
    if overlap(a, b):
        return 0.
    distances = []
    for points, edges in ((a, b), (b, a)):
        for x, y in points:
            for p, q in zip(edges, edges[1:] + edges[:1]):
                dx, dy = q[0] - p[0], q[1] - p[1]
                t = max(0, min(1, ((x-p[0])*dx + (y-p[1])*dy) / (dx*dx + dy*dy)))
                distances.append(math.hypot(x-p[0]-t*dx, y-p[1]-t*dy))
    return min(distances)


def evaluate(record, scenario_path, trace_path):
    scene = json.loads(Path(scenario_path).read_text())
    poses, obstacles = [], []
    counts = {}
    invalid_plans = 0
    min_clearance = float("inf")
    collisions, boundary_violations = 0, 0
    max_accel, max_curvature = 0., 0.
    for channel, stamp, data in messages(record):
        counts[channel] = counts.get(channel, 0) + 1
        if channel == "/apollo/perception/obstacles":
            obstacles = list(PerceptionObstacles.FromString(data).perception_obstacle)
        elif channel == "/apollo/planning":
            plan = ADCTrajectory.FromString(data)
            bad = plan.estop.is_estop or plan.header.module_name != "ml_planning_ppo" or len(plan.trajectory_point) < 2
            previous_time = -1
            for p in plan.trajectory_point:
                fields = [p.path_point.x, p.path_point.y, p.path_point.theta, p.path_point.kappa,
                          p.v, p.a, p.relative_time]
                bad |= not all(math.isfinite(v) for v in fields) or p.relative_time <= previous_time or p.v < 0
                previous_time = p.relative_time
                max_accel, max_curvature = max(max_accel, abs(p.a)), max(max_curvature, abs(p.path_point.kappa))
            invalid_plans += bool(bad)
        elif channel == "/apollo/localization/pose":
            msg = LocalizationEstimate.FromString(data)
            pose = msg.pose
            x, y, yaw = pose.position.x, pose.position.y, pose.heading
            poses.append((stamp / 1e9, x, y, yaw))
            ego = box(x, y, yaw, 2, 1.2)
            boundary_violations += any(p[1] < 1996 or p[1] > 2004 for p in ego)
            for o in obstacles:
                other = box(o.position.x, o.position.y, o.theta, o.length, o.width)
                d = clearance(ego, other)
                min_clearance = min(min_clearance, d)
                collisions += overlap(ego, other)
    if not poses:
        return {"passed": False, "error": "No recorded ego poses"}
    rows = list(csv.DictReader(Path(trace_path).open()))
    shield_count = sum(int(row["shield"]) for row in rows)
    end = scene["ego"]["routes"][0]["waypoints"][-1]["position"]
    max_lateral = max(abs(p[2] - 2000) for p in poses)
    progress = poses[-1][1] - poses[0][1]
    goal_error = math.hypot(poses[-1][1] - end["x"], poses[-1][2] - end["y"])
    passed_obstacle = all(poses[-1][1] > a["position"]["x"] + 3 for a in scene["agents"])
    passed = (collisions == 0 and boundary_violations == 0 and invalid_plans == 0
              and goal_error < 1 and progress > 50 and passed_obstacle
              and max_accel <= 2.01 and max_curvature <= .251
              and counts.get("/apollo/planning", 0) >= 290
              and (bool(scene["agents"]) or max_lateral < .5))
    result = {"passed": passed, "scope": "30 s / straight synthetic 8 m corridor / perfect_planning",
              "record": str(record), "pose_count": len(poses), "planning_count": counts.get("/apollo/planning", 0),
              "progress_m": progress, "goal_error_m": goal_error, "max_lateral_m": max_lateral,
              "min_obstacle_clearance_m": min_clearance if math.isfinite(min_clearance) else None,
              "collisions": collisions, "boundary_violations": boundary_violations,
              "invalid_plans": invalid_plans, "max_abs_acceleration": max_accel,
              "max_abs_curvature": max_curvature, "shield_frames": shield_count,
              "passed_obstacle": passed_obstacle, "channels": counts}
    # Native SVG artifact: x longitudinal position, y lateral offset.
    points = " ".join(f"{30+(x-1010)*14:.2f},{140-(y-2000)*24:.2f}" for _, x, y, _ in poses)
    boxes = "".join(f'<rect x="{30+(a["position"]["x"]-1010-.5)*14}" y="{140-(a["position"]["y"]-2000+.5)*24}" width="14" height="24" fill="#d95f45"/>' for a in scene["agents"])
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="860" height="300" viewBox="0 0 860 300">
<rect width="860" height="300" fill="#fafafa"/>
<text x="30" y="25" font-family="sans-serif" font-size="17">WorldSim PPO: {scene['id']} — {'PASS' if passed else 'FAIL'}</text>
<rect x="25" y="44" width="810" height="192" fill="#eef2f5" stroke="#637381"/>
<path d="M25 140H835" stroke="#a0aab4" stroke-dasharray="6 6"/>
{boxes}<polyline points="{points}" fill="none" stroke="#187bcd" stroke-width="3"/>
<text x="30" y="270" font-family="sans-serif" font-size="14">Longitudinal: 0–55 m | Lateral: ±4 m | Blue: recorded ego pose | Red: obstacle</text>
</svg>'''
    Path(record).parent.joinpath("trajectory.svg").write_text(svg)
    return result
