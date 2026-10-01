"""Reproducible parking missions and reviewed expectations; no scene-ID planner logic."""
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from shapely.geometry import Polygon
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'modules/simulation/simulator'))
from quality_metrics import body


def generate(probe):
    source=ROOT/'modules/simulation/scene_editor/examples/parking_lab'
    out=source.parent/'parking_missions_v2';out.mkdir(exist_ok=True)
    manifest=json.loads((source/'manifest.json').read_text());cases=[]
    for base in manifest['cases']:
        original=json.loads((source/base['file']).read_text());original_audit=json.loads((source/base['file'].replace('.worldsim.scenario.json','.evaluation.json')).read_text())
        arrival=json.loads((source/base['file'].replace('.worldsim.scenario.json','.witness.json')).read_text())
        park=original_audit['parking'];start=arrival[0][:3];goal=park['goal'];ox=start[0]+2;cy=start[1]
        exit_pose=[ox+2,cy,0]
        # Reversing a valid bicycle path gives an independent departure witness.
        departure=[]
        for i in range(len(arrival)-1,-1,-1):
            departure.append(arrival[i][:3]+[-arrival[min(i+1,len(arrival)-1)][3]])
        departure += [[start[0]+i*.01,cy,0,1] for i in range(1,401)]
        variants=[('in','base'),('out','base'),('in_out','base')]
        if park['aisle_width_m']==1.2 and park['width_m'] in (.55,.90):
            variants += [('in','pose_offset')]
            if park['entry']=='rear':variants += [('in','static_end'),('in','occupied'),('in','moving_aisle')]
        for operation,variant in variants:
            scene=copy.deepcopy(original);audit=copy.deepcopy(original_audit)
            name=original['id']+'__'+operation+'__'+variant
            scene.update(id=name,name=name,duration=120)
            ego=scene['ego'];ego['parkingOperation']=operation
            p=copy.deepcopy(park);p.update(operation=operation,variant=variant,park_goal=goal,goal=exit_pose if operation!='in' else goal)
            if operation!='in':ego['parkingExit']={'position':{'x':exit_pose[0],'y':exit_pose[1],'z':0},'heading':0}
            if operation=='out':
                ego['position']={'x':goal[0],'y':goal[1],'z':0};ego['heading']=goal[2]
                ego['routes'][0]['waypoints']=[{'position':{'x':exit_pose[0],'y':exit_pose[1],'z':0}}]
            witness=copy.deepcopy(arrival if operation=='in' else departure if operation=='out' else arrival+departure)
            if variant=='pose_offset':
                ego['position']['y']+=.08 if park['entry']=='front' else -.08
                ego['heading']=math.radians(5 if park['entry']=='front' else -5)
                vertices=park['area'];first=[ego['position']['x'],ego['position']['y'],ego['heading']]
                data=first+goal+[1 if park['entry']=='front' else -1,len(vertices)]+[v for q in vertices for v in q]+[0]
                result=subprocess.run([probe],input=' '.join(map(str,data)),text=True,capture_output=True,timeout=120)
                if result.returncode:raise RuntimeError(name+': no witness '+result.stderr)
                witness=[list(map(float,line.split(','))) for line in result.stdout.splitlines()]
            if variant in ('static_end','occupied'):
                x,y=(ox+3.5,cy) if variant=='static_end' else (Polygon(park['slot']).centroid.x,Polygon(park['slot']).centroid.y)
                scene['agents']=[{'id':'blocker','type':'AGENT_TYPE_STATIC','position':{'x':x,'y':y,'z':0},'heading':0,'size':{'x':.35,'y':.3,'z':.6}}]
            if variant=='moving_aisle':
                y=cy+.45
                scene['agents']=[{'id':'aisle_car','type':'AGENT_TYPE_VEHICLE','position':{'x':ox+3,'y':y,'z':0},'heading':math.pi,'speed':.5,'size':{'x':.5,'y':.2,'z':.6},'activeRouteId':'leave','routes':[{'id':'leave','waypoints':[{'position':{'x':ox+3,'y':y,'z':0},'speed':.5},{'position':{'x':ox-4.5,'y':y,'z':0},'speed':.5}]}]}]
            area=Polygon(park['area']);assert all(area.covers(body(*q[:3],.62,.1,.25)) for q in witness),name
            assert all(abs(math.remainder(b[2]-a[2],2*math.pi))<=1.8*math.hypot(b[0]-a[0],b[1]-a[1])+.001 for a,b in zip(witness,witness[1:])),name
            if variant=='static_end':
                obstacle=body(ox+3.5,cy,0,.175,.175,.15)
                assert all(body(*q[:3],.62,.1,.25).distance(obstacle)>.008 for q in witness),name
            if variant=='moving_aisle':
                # Ego waits at its real starting pose until the actor clears.
                waiting=body(ego['position']['x'],ego['position']['y'],ego['heading'],.62,.1,.25)
                assert all(waiting.distance(body(ox+3-i*.01,cy+.45,math.pi,.25,.25,.1))>.008 for i in range(751)),name
            audit['parking']=p
            audit['expectation']='parking_hold' if variant=='occupied' else 'park'
            audit['reason']='Occupied target: hold safely at start; do not count as parking success' if variant=='occupied' else 'Execute reviewed parking mission, including full-body alignment / departure as applicable'
            if variant=='occupied':p['hold_goal']=[ego['position']['x'],ego['position']['y'],ego['heading']];scene['duration']=12
            text=json.dumps(scene,ensure_ascii=False,indent=2)+'\n'
            audit['scenario_sha256']=hashlib.sha256(text.encode()).hexdigest()
            audit['validity']={'status':'VALID','reason':'Occupied goal requires safe rejection' if variant=='occupied' else 'Independently swept kinematic witness; original vehicle and area unchanged'}
            audit['parking']['witness_length_m']=sum(math.hypot(b[0]-a[0],b[1]-a[1]) for a,b in zip(witness,witness[1:])) if variant!='occupied' else None
            (out/(name+'.worldsim.scenario.json')).write_text(text)
            (out/(name+'.evaluation.json')).write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
            if variant!='occupied':
                (out/(name+'.witness.json')).write_text(json.dumps(witness,separators=(',',':'))+'\n')
            cases.append({'file':name+'.worldsim.scenario.json','operation':operation,'variant':variant,'style':p['style'],'width_m':p['width_m'],'aisle_width_m':p['aisle_width_m'],'entry':p['entry']})
    for category,predicate in [('all',lambda c:True),('in',lambda c:c['operation']=='in'),('out',lambda c:c['operation']=='out'),('in_out',lambda c:c['operation']=='in_out'),('robustness',lambda c:c['variant']!='base')]:
        (out/(category+'.suite.json')).write_text(json.dumps({'kind':'worldsim-suite','version':1,'name':'泊车任务 V2 · '+category,'mapId':'ranger_parking_lab_v1','scenarios':[c['file'] for c in cases if predicate(c)]},ensure_ascii=False,indent=2)+'\n')
    (out/'manifest.json').write_text(json.dumps({'version':2,'cases':cases},ensure_ascii=False,indent=2)+'\n')
    print('Generated',len(cases),'audited missions')


if __name__=='__main__':generate(sys.argv[1])
