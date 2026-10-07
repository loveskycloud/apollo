"""Versioned, scene-owned expectations, independent of planner success/failure."""
import hashlib
import json
import math
import shlex
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


def pnc_destination_parameters(runtime, vehicle_path):
    """Read the frozen standard-PNC stop contract, not the planner's outcome."""
    from google.protobuf import text_format
    from modules.common_msgs.config_msgs.vehicle_config_pb2 import VehicleConfig
    from modules.planning.traffic_rules.destination.proto.destination_pb2 import DestinationConfig
    runtime = Path(runtime)
    vehicle = text_format.Parse(Path(vehicle_path).read_text(), VehicleConfig()).vehicle_param
    rule_path = runtime / 'modules/planning/traffic_rules/destination/conf/default_conf.pb.txt'
    rule = text_format.Parse(rule_path.read_text(), DestinationConfig())
    # Default in planning_base/gflags/planning_gflags.cc. Flagfiles override it
    # in their actual include order, just as the component's gflags parser does.
    wall_length = .1
    visiting = set()

    def flags(path):
        nonlocal wall_length
        path = Path(path).resolve(strict=True)
        if path in visiting:
            raise ValueError('Recursive PNC flagfile: '+str(path))
        visiting.add(path)
        for line in path.read_text().splitlines():
            words = shlex.split(line, comments=True)
            if not words:
                continue
            key, sep, value = words[0].partition('=')
            if not sep and len(words) == 2:
                value = words[1]
            if key == '--flagfile':
                included = Path(value)
                flags(included if included.is_absolute() else runtime / included)
            elif key == '--virtual_stop_wall_length':
                wall_length = float(value)
        visiting.remove(path)

    flags(runtime / 'modules/planning/planning_component/conf/planning.conf')
    result = {'front_edge_to_center': vehicle.front_edge_to_center,
              'stop_distance': rule.stop_distance, 'virtual_stop_wall_length': wall_length}
    if (not vehicle.HasField('front_edge_to_center') or
            any(not math.isfinite(v) or v < 0 for v in result.values()) or
            result['front_edge_to_center'] <= 0):
        raise ValueError('Invalid frozen PNC destination geometry: '+str(result))
    return result


def pnc_destination_goal(lane, lane_s, parameters):
    """Convert Apollo's destination fence to an independent rear-axle target.

    Destination::MakeDecisions places the wall center before routing_end; the
    lane overload of Frame::CreateStopObstacle includes half the wall length;
    BuildStopDecision subtracts stop_distance again. Stop points refer to the
    vehicle front, whereas localization and the ML goal refer to the rear axle.
    """
    from shapely.geometry import LineString
    line = LineString([(p.x, p.y) for segment in lane.central_curve.segment
                       for p in segment.line_segment.point])
    if not math.isfinite(lane_s) or not 0 <= lane_s <= line.length+1e-5:
        raise ValueError('PNC routing destination is outside its map lane')
    wall_s = max(0, lane_s-parameters['virtual_stop_wall_length']-parameters['stop_distance'])
    stop_s = wall_s-parameters['virtual_stop_wall_length']/2-parameters['stop_distance']
    if stop_s < 0:
        raise ValueError('PNC destination has no room for the configured stop margin')
    stop = line.interpolate(stop_s)
    a, b = line.interpolate(max(0, stop_s-.001)), line.interpolate(min(line.length, stop_s+.001))
    heading = math.atan2(b.y-a.y, b.x-a.x)
    front = parameters['front_edge_to_center']
    return {'x': stop.x-front*math.cos(heading), 'y': stop.y-front*math.sin(heading)}, {
        'x': stop.x, 'y': stop.y}


def evaluate_record(record, scene, expectation, pnc=None, map_dir=None):
    from bag_diff import messages
    from modules.common_msgs.localization_msgs.localization_pb2 import LocalizationEstimate
    from modules.common_msgs.planning_msgs.planning_pb2 import ADCTrajectory
    from modules.common_msgs.planning_msgs.decision_pb2 import STOP_REASON_DESTINATION
    from modules.common_msgs.routing_msgs.routing_pb2 import RoutingResponse
    tail = deque()
    routing = None
    mission_complete = False
    destination_stops = []
    for channel, stamp, payload in messages(record):
        if pnc is not None and channel == '/apollo/raw_routing_response':
            routing = RoutingResponse.FromString(payload)
        if pnc is not None and channel == '/apollo/planning':
            plan = ADCTrajectory.FromString(payload)
            mission_complete = plan.decision.main_decision.HasField('mission_complete')
            for obj in plan.decision.object_decision.decision:
                for decision in obj.object_decision:
                    if decision.HasField('stop') and decision.stop.reason_code == STOP_REASON_DESTINATION:
                        destination_stops.append((decision.stop.stop_point.x, decision.stop.stop_point.y))
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
    goal = route['waypoints'][-1]['position']
    if pnc is None or expectation['expectation'] == 'safe_stop':
        return evaluate_motion(poses, goal, expectation)
    if routing is None or not routing.routing_request.waypoint:
        raise ValueError('PNC destination evaluation requires the recorded routing response')
    end = routing.routing_request.waypoint[-1]
    if not end.HasField('s') or math.hypot(end.pose.x-goal['x'], end.pose.y-goal['y']) > .4:
        raise ValueError('PNC routing destination does not match the requested scene goal')
    from quality_metrics import read_map
    lane = next((lane for lane in read_map(map_dir).lane if lane.id.id == end.id), None)
    if lane is None:
        raise ValueError('PNC destination lane missing from frozen map: '+end.id)
    expected_goal, front_stop = pnc_destination_goal(lane, end.s, pnc)
    result = evaluate_motion(poses, expected_goal, expectation)
    declared_stop_matches = bool(destination_stops) and math.dist(destination_stops[-1],
                                        (front_stop['x'], front_stop['y'])) <= .1
    if not mission_complete or not declared_stop_matches:
        result['status'] = 'FAIL'
    result.update(goal_reference='PNC configured destination stop / rear axle',
                  requested_goal_distance_m=math.hypot(poses[-1][1]-goal['x'], poses[-1][2]-goal['y']) if poses else None,
                  expected_goal=expected_goal, expected_front_stop=front_stop, stop_parameters=pnc,
                  mission_complete=mission_complete, declared_destination_stop_matches=declared_stop_matches,
                  reason='Within 0.4 m of configured PNC stop, speed below 0.05 m/s, destination decision and mission completion required')
    return result
