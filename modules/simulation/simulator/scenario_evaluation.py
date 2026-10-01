"""Versioned, scene-owned expectations, independent of planner success/failure."""
import hashlib
import json
import math
from collections import deque
from pathlib import Path

MODES = {'reach_goal', 'yield_then_proceed', 'safe_stop', 'park', 'parking_hold'}


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
    if value['expectation'] in ('park','parking_hold'):
        parking = value.get('parking', {})
        goal = parking.get('goal', [])
        scene = json.loads(source.read_text())
        if (not parking.get('space_id') or
                parking['space_id'] != scene.get('ego', {}).get('parkingSpaceId') or
                parking.get('entry') not in ('front', 'rear') or len(goal) != 3 or
                any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in goal)):
            raise ValueError('Parking expectation requires matching space ID, entry and finite target pose')
        operation=parking.get('operation','in')
        if operation not in ('in','out','in_out') or operation!=scene['ego'].get('parkingOperation','in'):
            raise ValueError('Parking operation must match the scenario command')
        if operation!='in':
            exit_pose=scene['ego'].get('parkingExit',{})
            position=exit_pose.get('position',{})
            if goal!=[position.get('x'),position.get('y'),exit_pose.get('heading')]:
                raise ValueError('Departure target must match the scenario exit command')
    if value['expectation']=='parking_hold':
        scene=json.loads(source.read_text())
        blockers=[a for a in scene.get('agents',[]) if a.get('type')=='AGENT_TYPE_STATIC' and a.get('speed',0)==0 and a.get('enabled',True)]
        if value['parking'].get('variant')!='occupied' or not blockers or not value['parking'].get('hold_goal'):
            raise ValueError('Parking hold requires an audited occupied space and stop target')
        from quality_metrics import body
        parking=value['parking'];ego=scene['ego'];hold=parking['hold_goal']
        if hold!=[ego['position']['x'],ego['position']['y'],ego.get('heading',0)] or any(not math.isfinite(v) for v in hold):
            raise ValueError('Parking hold target must be the finite initial ego pose')
        target=body(*parking.get('park_goal',parking['goal']),.62,.1,.25)
        permanent=[]
        for actor in blockers:
            if any(w.get('speed',0)!=0 for r in actor.get('routes',[]) for w in r.get('waypoints',[])):continue
            if any(a.get('targetAgentId')==actor['id'] for t in scene.get('triggers',[]) for a in t.get('actions',[])):continue
            size=actor.get('size',{});position=actor['position']
            obstacle=body(position['x'],position['y'],actor.get('heading',0),size.get('x',0)/2,size.get('x',0)/2,size.get('y',0)/2)
            if target.intersection(obstacle).area>1e-6:permanent.append(actor)
        if not permanent:
            raise ValueError('Parking hold requires a permanent blocker overlapping the target vehicle')
    if 'parking_dynamic' in value:
        dynamic=value['parking_dynamic'];scene=json.loads(source.read_text())
        actors=scene.get('agents',[])
        if (value['expectation']!='park' or dynamic.get('version') not in (1,2) or
                not dynamic.get('actor_ids') or len(set(dynamic['actor_ids']))!=len(dynamic['actor_ids']) or
                set(dynamic['actor_ids'])!={a['id'] for a in actors} or
                any(a.get('enabled',True) for a in actors)):
            raise ValueError('Dynamic parking requires declared, initially disabled actors and a parking mission')
        if dynamic.get('version') == 1 and 'actor_end_states' in dynamic:
            raise ValueError('Actor end-state overrides require dynamic parking contract version 2')
        if dynamic.get('version') == 2:
            states = dynamic.get('actor_end_states', {})
            if set(states) != set(dynamic['actor_ids']):
                raise ValueError('Dynamic parking requires an end-state contract for every actor')
            for name, state in states.items():
                if state.get('kind') not in ('cleared', 'stopped_visible'):
                    raise ValueError('Unknown dynamic parking actor end state')
                if state['kind'] == 'stopped_visible':
                    numbers = [state.get('position', {}).get(k) for k in ('x', 'y')]
                    numbers += [state.get(k) for k in ('tolerance_m', 'minimum_stationary_s')]
                    if any(not isinstance(v, (float, int)) or not math.isfinite(v) for v in numbers) or min(numbers[2:]) <= 0:
                        raise ValueError('Retained actor needs a finite stop point and positive evidence thresholds')
                    actions = [a for t in scene.get('triggers', []) for a in t.get('actions', []) if a.get('targetAgentId') == name]
                    if any(a.get('kind') == 'ACTION_DISABLE' for a in actions) or not any(a.get('kind') == 'ACTION_STOP' for a in actions):
                        raise ValueError('Retained actor must stop without being disabled')
        for key in ('minimum_activation_displacement_m','minimum_activation_speed_mps'):
            if not isinstance(dynamic.get(key),(int,float)) or not math.isfinite(dynamic[key]) or dynamic[key]<=0:
                raise ValueError('Dynamic parking requires positive finite motion thresholds')
        starts=[t for t in scene.get('triggers',[]) if t.get('type')=='TRIGGER_TYPE_LOCATION' and
                t.get('targetAgentId')=='ego' and any(a.get('kind')=='ACTION_ENABLE' and
                a.get('targetAgentId')==dynamic['actor_ids'][0] for a in t.get('actions',[]))]
        ego=scene['ego']['position']
        if len(starts)!=1 or math.hypot(starts[0]['center']['x']-ego['x'],starts[0]['center']['y']-ego['y'])<=starts[0].get('radius',2):
            raise ValueError('Dynamic parking must start in an ego location zone away from the initial pose')
    if 'parking_interaction' in value:
        interaction=value['parking_interaction'];scene=json.loads(source.read_text())
        backgrounds=interaction.get('background_actor_ids',[]);crossing=interaction.get('crossing_actor_id')
        expected=backgrounds+([crossing] if crossing else [])
        if (value['expectation']!='park' or interaction.get('version')!=1 or not backgrounds or
                len(set(expected))!=len(expected) or set(expected)!={a['id'] for a in scene.get('agents',[])}):
            raise ValueError('Parking interaction requires an explicit, unique actor inventory')
        for key in ('minimum_moving_fraction','minimum_concurrent_motion_s','baseline_duration_s'):
            v=interaction.get(key)
            if not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0:
                raise ValueError('Parking interaction requires positive finite evidence thresholds')
        if interaction['minimum_moving_fraction']>1:
            raise ValueError('Moving fraction must be at most one')
        limit=interaction.get('maximum_extra_duration_s')
        if (limit is None and not crossing) or (limit is not None and
                (not isinstance(limit,(int,float)) or not math.isfinite(limit) or limit<0)):
            raise ValueError('Unrelated-motion cases require a finite completion-time budget')
        if any(a.get('kind')=='ACTION_DISABLE' and a.get('targetAgentId') in backgrounds
               for t in scene.get('triggers',[]) for a in t.get('actions',[])):
            raise ValueError('Unrelated background actors must remain visible')
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
    if mode in ('park','parking_hold'):
        result.update(status='PASS' if goal_distance<=.03 and abs(end[3])<.02 else 'FAIL',
                      reason='Park within 3 cm of rear-axle target and stop; full body/heading checked independently')
        return result
    if mode != 'safe_stop':
        result.update(status='PASS' if goal_distance <= .4 and abs(end[3])<.05 else 'FAIL',
                      reason='Route destination must be reached within 0.4 m and speed below 0.05 m/s')
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
    if expectation['expectation'] in ('park','parking_hold'):
        target=expectation['parking'].get('hold_goal',expectation['parking']['goal'])
        return evaluate_motion(poses,{'x':target[0],'y':target[1]},expectation)
    route = next(r for r in ego['routes'] if r['id'] == ego['activeRouteId'])
    return evaluate_motion(poses, route['waypoints'][-1]['position'], expectation)
