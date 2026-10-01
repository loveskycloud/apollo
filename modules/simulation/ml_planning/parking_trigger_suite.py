"""Generate motion-triggered parking tests from successful unperturbed runs.

The actors are initially disabled. A small ego location zone starts traffic;
actor endpoint zones remove traffic, so it cannot become a permanent blocker.
No planner implementation or acceptance threshold is changed by this generator.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import sys

from shapely.geometry import Polygon
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'modules/simulation/simulator'))
from quality_metrics import body


def position(x, y):
    return {'x': float(x), 'y': float(y), 'z': 0}


def actor(name, start, end, pedestrian=False):
    speed = .65 if pedestrian else .45
    heading = math.atan2(end[1]-start[1], end[0]-start[0])
    length, width, height = (.4, .4, 1.7) if pedestrian else (.72, .50, .66)
    return {'id': name, 'name': name, 'type': 'AGENT_TYPE_PEDESTRIAN' if pedestrian else 'AGENT_TYPE_VEHICLE',
            'position': position(*start), 'heading': heading, 'speed': speed,
            'size': {'x': length, 'y': width, 'z': height}, 'enabled': False,
            'activeRouteId': name+'-route', 'routes': [{'id': name+'-route', 'pathType': 'polyline',
                'waypoints': [{'position': position(*p), 'speed': speed} for p in (start, end)]}]}


def sweep(agent):
    points = [p['position'] for p in agent['routes'][0]['waypoints']]
    a, b = points
    count = math.ceil(math.hypot(b['x']-a['x'], b['y']-a['y'])/.02)
    size = agent['size']
    return unary_union([body(a['x']+(b['x']-a['x'])*i/count,
                            a['y']+(b['y']-a['y'])*i/count, agent['heading'],
                            size['x']/2, size['x']/2, size['y']/2) for i in range(count+1)])


def design(reference, parking, pattern):
    area = Polygon(parking['area'])
    # This first dynamic set uses the wide aisle, leaving room to yield.
    xmin, ymin, xmax, _ = area.bounds
    cy = ymin+parking['aisle_width_m']/2
    initial = reference[0]
    for i in range(1, len(reference)-10):
        t, x, y, heading, speed = reference[i]
        if t < 2 or t > reference[-1][0]*.65 or abs(speed) < .08:
            continue
        if math.hypot(x-initial[1], y-initial[2]) < .25:
            continue
        # Leave room for a low-speed stop before the actors cross the path.
        stopped = body(x, y, heading, .62, .1, .25).buffer(.18)
        future = unary_union([body(*p[1:4], .62, .1, .25) for p in reference[i+1::5]])
        pedestrian = None
        for p in reference[i+10::5]:
            px = p[1]
            if not xmin+.25 < px < xmax-.25:
                continue
            candidate = actor('pedestrian', (px, ymin+.21), (px, ymin+2.4-.21), True)
            swept = sweep(candidate)
            if area.covers(swept) and not stopped.intersects(swept) and future.intersects(swept):
                pedestrian = candidate
                break
        vehicles = None
        for offset in (.9, -.9, .65, -.65, .35, -.35, 0.):
            a = actor('vehicle_left', (xmin+.40, cy+offset), (xmax-.40, cy+offset))
            swept = sweep(a)
            if area.covers(swept) and not stopped.intersects(swept) and future.intersects(swept):
                vehicles = [a, actor('vehicle_right', (xmax-.40, cy+offset), (xmin+.40, cy+offset))]
                break
        if pattern == 'pedestrian_cross' and pedestrian:
            chosen = [pedestrian]
        elif pattern == 'vehicle_left' and vehicles:
            chosen = [vehicles[0]]
        elif pattern == 'vehicle_right' and vehicles:
            chosen = [vehicles[1]]
        elif pattern == 'pedestrian_then_vehicles' and pedestrian and vehicles:
            chosen = [pedestrian]+vehicles
        else:
            continue
        # The zone must first be encountered during motion, not at t=0 or
        # an earlier pass through the same pose. Native regression checks it.
        first = next(q for q in reference if math.hypot(q[1]-x, q[2]-y) <= .06)
        if first[0] < 1 or abs(first[4]) < .05:
            continue
        return chosen, (x, y), t
    raise ValueError('No separated, relevant trigger geometry: '+parking['space_id']+' '+pattern)


def build_case(scene, audit, reference, pattern):
    scene, audit = copy.deepcopy(scene), copy.deepcopy(audit)
    actors, center, reference_time = design(reference, audit['parking'], pattern)
    name = scene['id'].removesuffix('__base')+'__trigger_'+pattern
    scene.update(id=name, name=name, description='泊车动作开始后进入位置触发区，启动动态交通；清空后仍须完成泊车任务。',
                 duration=120, agents=actors)
    enable = lambda a: {'kind': 'ACTION_ENABLE', 'targetAgentId': a['id']}
    scene['triggers'] = [{'id': 'ego-maneuver-zone', 'name': '泊车途中启动动态交通',
        'type': 'TRIGGER_TYPE_LOCATION', 'targetAgentId': 'ego', 'center': position(*center),
        'radius': .06, 'actions': [enable(actors[0])]}]
    for index, a in enumerate(actors):
        end = a['routes'][0]['waypoints'][-1]['position']
        actions = [{'kind': 'ACTION_DISABLE', 'targetAgentId': a['id']}]
        if index+1 < len(actors):
            actions.append(enable(actors[index+1]))
        scene['triggers'].append({'id': a['id']+'-clear', 'name': '驶离干扰区并清场',
            'type': 'TRIGGER_TYPE_LOCATION', 'targetAgentId': a['id'], 'center': end,
            # VehicleAgent considers the final waypoint reached at 0.30 m.
            # Enter this cleanup zone before it stops short of that waypoint.
            'radius': .03 if a['type']=='AGENT_TYPE_PEDESTRIAN' else .35, 'actions': actions})
    audit['parking']['variant'] = 'trigger_'+pattern
    audit['parking_dynamic'] = {'version': 1, 'pattern': pattern, 'actor_ids': [a['id'] for a in actors],
        'minimum_activation_displacement_m': .15, 'minimum_activation_speed_mps': .05,
        'reference_trigger_s': reference_time,
        'expectation': 'All actors must activate and clear after ego motion; then finish the original parking mission'}
    audit['reason'] = 'Motion-triggered clearing hazards: yield safely and complete the original parking mission'
    audit['validity'] = {'status': 'VALID', 'reason': 'Successful baseline; actor sweeps within driveable area; '+
        'trigger pose has 0.18 m stopping buffer clear of each sequential actor sweep; sweeps intersect future ego path. '+
        'This geometry audit does not certify dynamic control.'}
    encoded = json.dumps(scene, ensure_ascii=False, indent=2)+'\n'
    audit['scenario_sha256'] = hashlib.sha256(encoded.encode()).hexdigest()
    return scene, audit, encoded


def generate(baseline, output):
    output.mkdir(parents=True, exist_ok=True)
    summaries = json.loads((baseline/'summary.json').read_text())
    sys.path.insert(0, '/opt/apollo/neo/python')
    import task_service  # Configure Apollo protobuf and record-reader runtime.
    from bag_diff import messages
    from modules.common_msgs.localization_msgs.localization_pb2 import LocalizationEstimate
    cases = []
    for job in summaries:
        p = job['analysis']['quality_metrics']['runs'][0]['parking']
        if p['variant'] != 'base' or p['operation'] not in ('in', 'out') or p['width_m'] != .9 or p['aisle_width_m'] != 2.4:
            continue
        assert job['status'] == 'completed'
        request = json.loads((baseline/job['id']/'analysis_request.json').read_text())
        source = Path(request['source'])
        scene = json.loads(source.read_text())
        audit = task_service.load_evaluation(source)
        reference = []; next_time = -math.inf
        for channel, stamp, payload in messages(request['outputs'][0]):
            if channel != '/apollo/localization/pose' or stamp/1e9 < next_time-1e-6:
                continue
            pose = LocalizationEstimate.FromString(payload).pose
            t = stamp/1e9; next_time = t+.1
            reference.append([t, pose.position.x, pose.position.y, pose.heading,
                              math.hypot(pose.linear_velocity.x, pose.linear_velocity.y)])
        origin = reference[0][0]
        for pose in reference: pose[0] -= origin
        (output/(scene['id']+'.reference.json')).write_text(json.dumps(reference)+'\n')
        for pattern in ('pedestrian_cross', 'vehicle_left', 'vehicle_right', 'pedestrian_then_vehicles'):
            generated, expected, encoded = build_case(scene, audit, reference, pattern)
            name = generated['id']
            (output/(name+'.worldsim.scenario.json')).write_text(encoded)
            (output/(name+'.evaluation.json')).write_text(json.dumps(expected, ensure_ascii=False, indent=2)+'\n')
            cases.append({'file': name+'.worldsim.scenario.json', 'pattern': pattern,
                          'operation': p['operation'], 'style': p['style'], 'entry': p['expected_entry']})
    assert len(cases) == 48, len(cases)
    groups = {'all': cases}
    for key in ('pattern', 'operation'):
        for value in sorted({c[key] for c in cases}):
            groups[value] = [c for c in cases if c[key] == value]
    groups['smoke'] = [next(c for c in cases if c['pattern']==pattern and c['style']==style and c['operation']==operation)
        for pattern, style, operation in [('pedestrian_cross','parallel','in'),
            ('vehicle_left','perpendicular','out'),('vehicle_right','diagonal45','in'),
            ('pedestrian_then_vehicles','parallel','out')]]
    for name, members in groups.items():
        (output/(name+'.suite.json')).write_text(json.dumps({'kind': 'worldsim-suite', 'version': 1,
            'name': '泊车途中动态触发 V1 · '+name, 'mapId': 'ranger_parking_lab_v1',
            'scenarios': [c['file'] for c in members]}, ensure_ascii=False, indent=2)+'\n')
    (output/'manifest.json').write_text(json.dumps({'version': 1, 'cases': cases}, ensure_ascii=False, indent=2)+'\n')
    print('Generated', len(cases), 'motion-triggered parking scenes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'modules/simulation/scene_editor/examples/parking_dynamic_triggers_v1')
    args = parser.parse_args()
    generate(args.baseline, args.output)
