"""Reproducible static roadside encroachment fixtures on the captured HDMap.

Width audit is a necessary geometric check, not proof of full-path feasibility.
Native regression remains required for every generated scene.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from modules.common_msgs.map_msgs.map_pb2 import Map


def generate(map_dir, out, count=100):
    m=Map();m.ParseFromString((map_dir/'base_map.bin').read_bytes())
    candidates=[]
    for lane in m.lane:
        pts=np.array([(p.x,p.y) for seg in lane.central_curve.segment for p in seg.line_segment.point])
        lengths=np.linalg.norm(np.diff(pts,axis=0),axis=1)
        if not len(lengths) or min(lengths)<1e-7:continue
        arc=np.r_[0,np.cumsum(lengths)]
        if arc[-1]<5:continue
        headings=np.unwrap(np.arctan2(np.diff(pts[:,1]),np.diff(pts[:,0])))
        # Keep this curriculum to slight encroachment on straight/gentle roads.
        # Existing failed bend scenarios are regressed separately.
        if np.ptp(headings)>.6:continue
        candidates.append((lane,pts,arc,headings))
    out.mkdir(parents=True,exist_ok=False)
    scenarios=[];audits=[]
    for index in range(count):
        lane,pts,arc,headings=candidates[index%len(candidates)]
        variant=index//len(candidates)
        def pose(s,l=0):
            i=min(len(headings)-1,max(0,int(np.searchsorted(arc,s)-1)))
            t=(s-arc[i])/(arc[i+1]-arc[i]);p=pts[i]+t*(pts[i+1]-pts[i]);h=float(headings[i])
            return {'position':{'x':float(p[0]-l*math.sin(h)),'y':float(p[1]+l*math.cos(h)),'z':0},'heading':h}
        def width(s,samples):return float(np.interp(s,[v.s for v in samples],[v.width for v in samples]))
        start=.85+(max(0,arc[-1]-14))*((variant%3)/3)
        end=min(float(arc[-1]-.85),start+12)
        side=1 if variant%2==0 else -1
        obstacle_s=[start+(end-start)*(.46+.01*variant)]
        if end-start>8 and variant%3==2:obstacle_s=[start+2.5,end-2.5]
        actors=[];passages=[]
        for j,s in enumerate(obstacle_s):
            left,right=width(s,lane.left_sample),width(s,lane.right_sample)
            sign=side*(-1 if j%2 else 1)
            w=.10+.013*variant;length=.23+.045*variant
            intrusion=.08+.018*variant
            remaining=left+right-intrusion
            assert remaining>=.5+.05+.02,'Narrow permanently blocked fixture'
            lateral=sign*((left if sign>0 else right)-intrusion+w/2)
            actors.append(dict(id=f'static-{j}',name=f'路边静态障碍 {j}',type='AGENT_TYPE_STATIC',**pose(s,lateral),size={'x':length,'y':w,'z':.6},speed=0,enabled=True))
            passages.append(dict(actor_id=f'static-{j}',intrusion_m=intrusion,remaining_width_m=remaining,minimum_required_m=.57))
        name=f'roadside_{index:03d}_{lane.id.id}'
        scene=dict(id=name,name=f'静态低速 nudge {index:03d} · {lane.id.id}',mapId='beijing_zongyuan_1haolou_car_20260930',duration=90,description='实际 0.5m 车宽，静态障碍略微侵入道路；要求到达终点、持续有效轨迹、零碰撞。',ego=dict(id='ego',**pose(start),size={'x':.72,'y':.5,'z':.66},activeRouteId='route',routes=[{'id':'route','waypoints':[{'position':pose(end)['position']}]}]),agents=actors,triggers=[])
        text=json.dumps(scene,ensure_ascii=False,indent=2)+'\n';file=name+'.worldsim.scenario.json';(out/file).write_text(text)
        evaluation=dict(kind='worldsim-evaluation',version=1,scenario_sha256=hashlib.sha256(text.encode()).hexdigest(),expectation='reach_goal',reason='静态路边障碍保留可通行宽度，必须无碰撞通过，停车不计成功。',audit={'passages':passages,'vehicle_width_m':.5,'static_obstacle_clearance_m':.05,'scope':'Necessary passage width check; native swept-path validation required.'})
        (out/(name+'.evaluation.json')).write_text(json.dumps(evaluation,ensure_ascii=False,indent=2)+'\n')
        scenarios.append(file);audits.append({'id':name,'lane':lane.id.id,'passages':passages})
    manifest=dict(kind='worldsim-suite',version=1,name=f'北京总院 · 静态路边侵入 · {count} 场景',mapId='beijing_zongyuan_1haolou_car_20260930',scenarios=scenarios)
    (out/'static-nudge.suite.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    (out/'generation-audit.json').write_text(json.dumps({'lanes':len(candidates),'scenes':audits,'map_sha256':hashlib.sha256((map_dir/'base_map.bin').read_bytes()).hexdigest()},indent=2)+'\n')
    print(json.dumps({'scenarios':count,'lanes':len(candidates),'suite':str(out/'static-nudge.suite.json')}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--map',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--count',type=int,default=100);a=p.parse_args();generate(a.map,a.out,a.count)
