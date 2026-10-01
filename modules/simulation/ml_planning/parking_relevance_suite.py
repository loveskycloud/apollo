"""Persistent unrelated motion, plus a real crossing among moving bystanders."""
import copy
import hashlib
import json
import math
from pathlib import Path
from shapely.geometry import Polygon
from shapely.ops import unary_union
from parking_trigger_suite import ROOT, actor, body, position, sweep, design

EXAMPLES=ROOT/'modules/simulation/scene_editor/examples'


def crowd(reference):
    swept=unary_union([body(*p[1:4],.62,.1,.25) for p in reference])
    xmin,ymin,xmax,ymax=swept.bounds;cx=(xmin+xmax)/2;cy=(ymin+ymax)/2
    positions=[(xmin-.60,ymin-.60),(cx,ymin-.60),(xmax+.60,ymin-.60),
               (xmax+.60,cy),(xmax+.60,ymax+.60),(cx,ymax+.60),
               (xmin-.60,ymax+.60),(xmin-.60,cy)]
    actors=[]
    for i,(x,y) in enumerate(positions):
        a=actor('bystander_'+str(i),(x-.08,y),(x+.08,y),True)
        a.update(speed=.03,enabled=True)
        # 16 cm back-and-forth steps; all motion remains inside this envelope.
        points=[(x+(-.08 if j%2==0 else .08),y) for j in range(41)]
        a['routes'][0].update(pathType='bezier',waypoints=[{'position':position(*p),
            'handleIn':position(*p),'handleOut':position(*p),'speed':.03} for p in points])
        envelope=unary_union([body(*p,0,.2,.2,.2) for p in ((x-.08,y),(x+.08,y))]).convex_hull
        assert envelope.distance(swept)>.25
        actors.append(a)
    return actors


def passing_vehicle(reference,parking):
    swept=unary_union([body(*p[1:4],.62,.1,.25) for p in reference])
    area=Polygon(parking['area']);xmin,ymin,xmax,_=area.bounds
    # A separated parallel travel lane along the aisle. If the original sweep
    # uses that edge, use a parallel strip outside the ego driveable polygon.
    # This does not enlarge the ego's road boundary or grant it extra space.
    for y in (ymin+.30,ymin-.75):
        a=actor('unrelated_vehicle',(xmin+.4,y),(xmax-.4,y));a['speed']=.12
        for w in a['routes'][0]['waypoints']:w['speed']=.12
        if sweep(a).distance(swept)>.20:return a
    raise ValueError('Cannot place separated passing traffic')


def timed_crossing(reference,parking,center):
    area=Polygon(parking['area']);_,ymin,_,_=area.bounds;ymax=ymin+parking['aisle_width_m']
    activation=next(p for p in reference if math.hypot(p[1]-center[0],p[2]-center[1])<=.06)
    stopping=body(*activation[1:4],.62,.1,.25).buffer(.18)
    for target in reference:
        if target[0]<activation[0]+1.5 or target[0]>reference[-1][0]*.75 or abs(target[4])<.08:continue
        x=target[1]+.26*math.cos(target[3]);y=target[2]+.26*math.sin(target[3])
        for start,end in ((ymin+.21,ymax-.21),(ymax-.21,ymin+.21)):
            speed=abs(y-start)/(target[0]-activation[0])
            if not min(start,end)<y<max(start,end) or not .12<=speed<=.8:continue
            a=actor('pedestrian',(x,start),(x,end),True)
            swept=sweep(a)
            if not area.covers(swept) or stopping.intersects(swept):continue
            a['speed']=speed
            for w in a['routes'][0]['waypoints']:w['speed']=speed
            return a,{'baseline_contact_time_s':target[0],'trigger_time_s':activation[0],
                      'reason':'Without response, constant-speed pedestrian center reaches baseline ego body center at this timestamp'}
    raise ValueError('No genuine time-aligned crossing for '+parking['space_id'])


def generate():
    output=EXAMPLES/'parking_relevance_v1';output.mkdir(exist_ok=True)
    cases=[]
    for ref in sorted((EXAMPLES/'parking_dynamic_triggers_v1').glob('*.reference.json')):
        reference=json.loads(ref.read_text());stem=ref.name.removesuffix('.reference.json')
        source=EXAMPLES/'parking_missions_v2'/(stem+'.worldsim.scenario.json')
        for pattern in ('crowd_present','vehicle_passing','crowd_and_vehicle','crowd_and_crossing'):
            scene=json.loads(source.read_text());audit=json.loads(source.with_name(stem+'.evaluation.json').read_text())
            p=audit['parking'];actors=[];hazard=None
            if pattern!='vehicle_passing':actors+=crowd(reference)
            if pattern in ('vehicle_passing','crowd_and_vehicle'):actors.append(passing_vehicle(reference,p))
            backgrounds=[a['id'] for a in actors]
            chosen,center,t=design(reference,p,'pedestrian_cross')
            name=stem.removesuffix('__base')+'__trigger_relevance_'+pattern
            triggers=[]
            if pattern in ('vehicle_passing','crowd_and_vehicle'):
                for a in actors:a['enabled']=False
                triggers.append({'id':'activate-unrelated-motion','type':'TRIGGER_TYPE_LOCATION','targetAgentId':'ego',
                    'center':position(*center),'radius':.06,'actions':[{'kind':'ACTION_ENABLE','targetAgentId':a['id']} for a in actors]})
            if pattern=='crowd_and_crossing':
                hazard,counterfactual=timed_crossing(reference,p,center);actors.append(hazard)
                triggers += [{'id':'activate-crossing','type':'TRIGGER_TYPE_LOCATION','targetAgentId':'ego',
                    'center':position(*center),'radius':.06,'actions':[{'kind':'ACTION_ENABLE','targetAgentId':hazard['id']}]},
                    {'id':'crossing-clear','type':'TRIGGER_TYPE_LOCATION','targetAgentId':hazard['id'],
                     'center':hazard['routes'][0]['waypoints'][-1]['position'],'radius':.03,
                     'actions':[{'kind':'ACTION_DISABLE','targetAgentId':hazard['id']}]}]
            scene.update(id=name,name=name,agents=actors,triggers=triggers,duration=120,
                description='持续轻微移动的围观行人/无关行驶车辆不应阻断泊车；混合真实横穿时仍须安全让行。')
            p['variant']='trigger_relevance_'+pattern
            audit['parking_interaction']={'version':1,'background_actor_ids':backgrounds,
                'crossing_actor_id':hazard['id'] if hazard else None,'minimum_moving_fraction':.90,
                'minimum_concurrent_motion_s':1.,'baseline_duration_s':reference[-1][0],
                'maximum_extra_duration_s':None if hazard else .5}
            if hazard:audit['parking_interaction']['counterfactual']=counterfactual
            audit['reason']='Persistent unrelated motion must not stall parking; real crossing remains a safety test'
            audit['validity']={'status':'VALID','reason':'Unchanged successful ego path and area; background swept bodies clear full baseline body corridor by >0.20 m; crossing uses audited separated activation.'}
            encoded=json.dumps(scene,ensure_ascii=False,indent=2)+'\n';audit['scenario_sha256']=hashlib.sha256(encoded.encode()).hexdigest()
            (output/(name+'.worldsim.scenario.json')).write_text(encoded)
            (output/(name+'.evaluation.json')).write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
            cases.append({'file':name+'.worldsim.scenario.json','pattern':pattern,'operation':p['operation'],'style':p['style'],'entry':p['entry']})
    assert len(cases)==48
    groups={'all':cases}
    for pattern in sorted({c['pattern'] for c in cases}):groups[pattern]=[c for c in cases if c['pattern']==pattern]
    groups['smoke']=[next(c for c in cases if c['pattern']==pattern and c['style']==style and c['operation']==operation)
        for pattern,style,operation in [('crowd_present','parallel','in'),('vehicle_passing','perpendicular','out'),
            ('crowd_and_vehicle','diagonal45','in'),('crowd_and_crossing','diagonal45','out')]]
    for name,members in groups.items():
        (output/(name+'.suite.json')).write_text(json.dumps({'kind':'worldsim-suite','version':1,'name':'泊车无关运动与围观 V1 · '+name,
            'mapId':'ranger_parking_lab_v1','scenarios':[c['file'] for c in members]},ensure_ascii=False,indent=2)+'\n')
    (output/'manifest.json').write_text(json.dumps({'version':1,'cases':cases},ensure_ascii=False,indent=2)+'\n')
    print('Generated',len(cases),'relevance cases')


if __name__=='__main__':generate()
