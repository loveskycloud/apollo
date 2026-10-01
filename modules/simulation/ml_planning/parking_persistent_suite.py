"""Parking traffic that stops nearby and remains visible until mission completion."""
import copy
import hashlib
import json
import math
from pathlib import Path

from shapely.geometry import Polygon
from shapely.ops import unary_union
from parking_trigger_suite import ROOT, actor, body, position, sweep

EXAMPLES = ROOT/'modules/simulation/scene_editor/examples'


def design(reference, parking, placement):
    area = Polygon(parking['area'])
    xmin, ymin, xmax, _ = area.bounds
    ymax = ymin + parking['aisle_width_m']
    # Leave the existing bay/road geometry unchanged. The other vehicle stops
    # beside the bay mouth or on the opposite edge of the same aisle.
    y = ymax-.30 if placement == 'bay_side' else ymin+.30
    future = unary_union([body(*p[1:4], .62, .1, .25) for p in reference])
    candidates = []
    for i in range(1, len(reference)-20):
        t, x, ey, yaw, speed = reference[i]
        if t < 2 or t > reference[-1][0]*.5 or abs(speed) < .08:
            continue
        first = next(p for p in reference if math.hypot(p[1]-x, p[2]-ey) <= .06)
        if first[0] < 1 or abs(first[4]) < .05 or math.hypot(x-reference[0][1],ey-reference[0][2]) < .25:
            continue
        stopped = body(x,ey,yaw,.62,.1,.25).buffer(.18)
        for j in range(33):
            endx = xmin+.5+j*(xmax-xmin-1)/32
            parked = body(endx,y,0,.36,.36,.25)
            gap = parked.distance(future)
            if gap < .12 or not area.covers(parked.buffer(.025)):
                continue
            for direction in (-1,1):
                start = (endx-direction*1.0,y)
                # STOP zone precedes the final route waypoint, avoiding the
                # VehicleAgent's 0.30 m automatic waypoint arrival threshold.
                a = actor('retained_vehicle',start,(endx+direction*.5,y))
                traveled = actor('retained_vehicle',start,(endx+direction*.025,y))
                swept = sweep(traveled)
                if not area.covers(sweep(a)) or stopped.intersects(swept):
                    continue
                distance = parked.distance(Polygon(parking['slot']))
                candidates.append((distance, t, a, (x,ey), (endx,y), gap))
        if candidates:
            break
    if not candidates:
        raise ValueError('No safe persistent vehicle layout: '+parking['space_id']+' '+placement)
    _, t, a, center, target, gap = min(candidates,key=lambda c:(c[0],c[1]))
    return a, center, target, t, gap


def generate():
    output = EXAMPLES/'parking_persistent_traffic_v1'
    output.mkdir(exist_ok=True)
    cases=[]
    for ref in sorted((EXAMPLES/'parking_dynamic_triggers_v1').glob('*.reference.json')):
        stem=ref.name.removesuffix('.reference.json')
        source=EXAMPLES/'parking_missions_v2'/(stem+'.worldsim.scenario.json')
        original=json.loads(source.read_text())
        audit_path=source.with_name(stem+'.evaluation.json')
        for placement in ('bay_side','aisle_side'):
            scene=copy.deepcopy(original);audit=json.loads(audit_path.read_text())
            a,center,target,t,gap=design(json.loads(ref.read_text()),audit['parking'],placement)
            name=stem.removesuffix('__base')+'__trigger_persistent_'+placement
            scene.update(id=name,name=name,duration=120,agents=[a],description='泊车途中触发来车，停在车位旁/通道旁并持续保留；主车须在其可见时恢复并完成任务。')
            scene['triggers']=[{'id':'ego-maneuver-zone','type':'TRIGGER_TYPE_LOCATION','targetAgentId':'ego',
                'center':position(*center),'radius':.06,'actions':[{'kind':'ACTION_ENABLE','targetAgentId':a['id']}]},
                {'id':'vehicle-stop-and-remain','type':'TRIGGER_TYPE_LOCATION','targetAgentId':a['id'],
                 'center':position(*target),'radius':.025,'actions':[{'kind':'ACTION_STOP','targetAgentId':a['id']}]}]
            audit['parking']['variant']='trigger_persistent_'+placement
            audit['parking_dynamic']={'version':2,'pattern':'persistent_'+placement,'actor_ids':[a['id']],
                'minimum_activation_displacement_m':.15,'minimum_activation_speed_mps':.05,'reference_trigger_s':t,
                'actor_end_states':{a['id']:{'kind':'stopped_visible','position':position(*target),
                    'tolerance_m':.04,'minimum_stationary_s':2}},
                'expectation':'Actor moves, stops nearby and remains visible through completion; ego resumes and completes original mission'}
            audit['reason']='Non-blocking vehicle remains beside the bay or aisle; disappearance cannot pass this case'
            audit['validity']={'status':'VALID','stationary_body_gap_to_baseline_m':gap,
                'reason':'Unchanged successful baseline; stopped vehicle clears entire baseline body sweep by >=0.12 m before 0.025 m stop-zone tolerance; approach sweep inside area and outside ego 0.18 m stopping buffer.'}
            encoded=json.dumps(scene,ensure_ascii=False,indent=2)+'\n'
            audit['scenario_sha256']=hashlib.sha256(encoded.encode()).hexdigest()
            (output/(name+'.worldsim.scenario.json')).write_text(encoded)
            (output/(name+'.evaluation.json')).write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
            p=audit['parking'];cases.append({'file':name+'.worldsim.scenario.json','placement':placement,
                'operation':p['operation'],'style':p['style'],'entry':p['entry']})
    assert len(cases)==24
    groups={'all':cases}
    for key in ('placement','operation'):
        for v in sorted({c[key] for c in cases}):groups[v]=[c for c in cases if c[key]==v]
    groups['smoke']=[next(c for c in cases if c['style']==style and c['operation']==op and c['placement']==placement)
        for style,op,placement in [('parallel','in','bay_side'),('perpendicular','out','aisle_side'),('diagonal45','in','bay_side')]]
    for name,members in groups.items():
        (output/(name+'.suite.json')).write_text(json.dumps({'kind':'worldsim-suite','version':1,
            'name':'泊车旁车停留不清场 V1 · '+name,'mapId':'ranger_parking_lab_v1','scenarios':[c['file'] for c in members]},ensure_ascii=False,indent=2)+'\n')
    (output/'manifest.json').write_text(json.dumps({'version':1,'cases':cases},ensure_ascii=False,indent=2)+'\n')
    print('Generated',len(cases),'persistent traffic cases')


if __name__=='__main__':generate()
