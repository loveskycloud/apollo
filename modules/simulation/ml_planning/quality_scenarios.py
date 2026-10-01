"""Deterministic interaction perturbations; audit geometry before native tests."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
os.environ['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION']='python'
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=['/opt/apollo/neo/python',str(ROOT/'modules/simulation/simulator')]
from quality_metrics import body, read_map, drivable_area
from task_service import preflight_world


def generate():
    source=ROOT/'modules/simulation/scene_editor/examples/phase1_interaction'
    output=source.parent/'quality_regression_v1';output.mkdir(exist_ok=True)
    area=drivable_area(read_map(ROOT/'data/map_data/1haolou_202608241047qh'))
    names=[];manifest=[]
    for path in sorted(source.glob('*.worldsim.scenario.json')):
        base=json.loads(path.read_text())
        for variant,scale,shift in [('slow',.85,-.3),('fast',1.15,.3)]:
            scene=copy.deepcopy(base);name=path.name.removesuffix('.worldsim.scenario.json')+'_'+variant
            scene['id']=name;scene['name']='Quality regression · '+name
            scene['ego']['vehicleProfile']='ranger_mini_v3';scene['duration']=120
            ego=scene['ego'];h=ego['heading'];size=ego['size'];pose=ego['position']
            footprint=body(pose['x'],pose['y'],h,.62,.1,.25)
            assert area.covers(footprint)
            for actor in scene['agents']:
                # Translate actor and its entire route along the ego road;
                # keep lateral space/physical dimensions unchanged.
                positions=[actor['position']]+[w['position'] for r in actor.get('routes',[]) for w in r['waypoints']]
                for p in positions:p['x']+=shift*math.cos(h);p['y']+=shift*math.sin(h)
                actor['speed']=actor.get('speed',0)*scale
                for route in actor.get('routes',[]):
                    for w in route['waypoints']:
                        if 'speed' in w:w['speed']*=scale
                p=actor['position'];s=actor['size']
                assert not footprint.intersects(body(p['x'],p['y'],actor.get('heading',0),s['x']/2,s['x']/2,s['y']/2))
            text=json.dumps(scene,ensure_ascii=False,indent=2)+'\n';file=output/(name+'.worldsim.scenario.json');file.write_text(text)
            preflight_world(file)
            audit={'kind':'worldsim-evaluation','version':1,'scenario_sha256':hashlib.sha256(text.encode()).hexdigest(),
                   'expectation':'yield_then_proceed' if any(a.get('speed',0)>0 for a in scene['agents']) else 'reach_goal',
                   'reason':'Perturbed audited straight-road interaction: all obstacles must be passed without collision and the goal reached.',
                   'validity':{'status':'VALID','reason':'Strict protobuf, ego inside drivable road, no initial contact; original lateral passage retained'},
                   'audit':{'source':str(path),'speed_scale':scale,'longitudinal_shift_m':shift,
                            'scope':'Necessary scene validity; successful execution remains a separate model test'}}
            file.with_name(name+'.evaluation.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
            names.append(file.name);manifest.append(audit)
    (output/'quality-regression.suite.json').write_text(json.dumps({'kind':'worldsim-suite','version':1,
        'name':'质量指标复测 · 会车/nudge/组合 · 24 扰动场景','mapId':'1haolou_202608241047qh','scenarios':names},ensure_ascii=False,indent=2))
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print('Generated',len(names),'audited fixtures')


if __name__=='__main__':generate()
