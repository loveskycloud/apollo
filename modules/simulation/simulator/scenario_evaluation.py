"""Versioned, scene-owned expectations, independent of planner success/failure."""
import hashlib
import json
import math
from collections import deque
from pathlib import Path

MODES = {'reach_goal', 'yield_then_proceed', 'safe_stop'}


def evaluation_path(source):
    return Path(source).with_name(Path(source).name.replace('.worldsim.scenario.json', '') + '.evaluation.json')


def load_evaluation(source):
    source = Path(source)
    path = evaluation_path(source)
    if not path.exists():
        return {'expectation': 'reach_goal', 'reason': 'Legacy scene: reach the route destination'}
    value = json.loads(path.read_text())
    if value.get('kind') != 'worldsim-evaluation' or value.get('version') != 1 or value.get('expectation') not in MODES:
        raise ValueError(f'Invalid scene evaluation: {path}')
    if value.get('scenario_sha256') != hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError(f'Scene changed after its expectation was audited: {path}; regenerate the scene audit')
    if not isinstance(value.get('reason'), str) or not value['reason'].strip():
        raise ValueError('A scene expectation requires its reasoning')
    if value['expectation'] == 'safe_stop':
        scene = json.loads(source.read_text())
        actors = {a['id']: a for a in scene.get('agents', [])}
        blockers = value.get('blocked_by', [])
        if not blockers or any(a not in actors or actors[a]['type'] != 'AGENT_TYPE_STATIC' or
                               not actors[a].get('enabled', True) for a in blockers):
            raise ValueError('Safe-stop expectation requires an enabled static road blocker')
        for actor_id in blockers:
            actor = actors[actor_id]
            if actor.get('speed', 0) != 0 or any(w.get('speed', 0) != 0 for r in actor.get('routes', []) for w in r.get('waypoints', [])):
                raise ValueError('A moving obstacle cannot define permanent road blockage')
            if any(a.get('targetAgentId') == actor_id for t in scene.get('triggers', []) for a in t.get('actions', [])):
                raise ValueError('A scripted changing obstacle requires a clearing-hazard expectation')
        positions = value.get('stop_path', [])
        if len(positions) < 2 or any(not all(isinstance(p.get(k), (int, float)) and math.isfinite(p[k])
                                                  for k in ('x', 'y')) for p in positions):
            raise ValueError('Safe-stop expectation requires an audited approach stopping region')
    return value


def evaluate_motion(poses, goal, expectation):
    """poses: (seconds, x, y, measured_speed); evaluate the terminal 5 s."""
    mode = expectation['expectation']
    if not poses:
        return {'status': 'FAIL', 'reason': 'No localization poses', 'expectation': mode}
    end = poses[-1]
    goal_distance = math.hypot(end[1]-goal['x'], end[2]-goal['y'])
    result = {'expectation': mode, 'goal_distance_m': goal_distance}
    if mode != 'safe_stop':
        result.update(status='PASS' if goal_distance <= .4 else 'FAIL',
                      reason='Route destination must be reached within 0.4 m')
        return result
    tail = [p for p in poses if p[0] >= end[0]-5-1e-6]
    duration = tail[-1][0]-tail[0][0]
    max_speed = max(p[3] for p in tail)
    displacement = max(math.hypot(p[1]-end[1], p[2]-end[2]) for p in tail)
    path = expectation['stop_path']
    zone_distance = max(min(math.hypot(p[1]-q['x'], p[2]-q['y']) for q in path) for p in tail)
    passed = duration >= 4.99 and max_speed <= .05 and displacement <= .10 and zone_distance <= .4
    result.update(status='PASS' if passed else 'FAIL', reason='Stop inside the audited approach region for at least 5 s',
                  stopped_duration_s=duration, max_terminal_speed_mps=max_speed,
                  terminal_displacement_m=displacement, stop_region_distance_m=zone_distance,
                  blocked_by=expectation['blocked_by'])
    return result


def evaluate_record(record, scene, expectation):
    from bag_diff import messages
    from modules.common_msgs.localization_msgs.localization_pb2 import LocalizationEstimate
    tail = deque()
    for channel, stamp, payload in messages(record):
        if channel != '/apollo/localization/pose':
            continue
        seconds = stamp/1e9
        tail.append((seconds, payload))
        while tail and tail[0][0] < seconds-5.1:
            tail.popleft()
    poses = []
    for seconds, payload in tail:
        p = LocalizationEstimate.FromString(payload).pose
        poses.append((seconds, p.position.x, p.position.y,
                      math.hypot(p.linear_velocity.x, p.linear_velocity.y)))
    ego = scene['ego']
    route = next(r for r in ego['routes'] if r['id'] == ego['activeRouteId'])
    return evaluate_motion(poses, route['waypoints'][-1]['position'], expectation)
