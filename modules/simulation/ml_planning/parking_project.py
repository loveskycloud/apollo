"""Generate a Ranger HDMap, 36 parking cases, and reachability audit inputs.

The map contains isolated test cells. Dimensions are metres. Space width is
measured perpendicular to its long axis, including diagonal spaces.
"""
import argparse
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import subprocess
import sys
os.environ['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION']='python'
sys.path.insert(0,'/opt/apollo/neo/python')
from google.protobuf import text_format
from modules.common_msgs.map_msgs.map_pb2 import Map
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

ROOT=Path(__file__).resolve().parents[3]
NAME='ranger_parking_lab_v1'


def generate(probe=None):
    out=ROOT/'modules/simulation/scene_editor/examples/parking_lab'
    map_dir=ROOT/'data/map_data'/NAME
    out.mkdir(parents=True,exist_ok=True);map_dir.mkdir(parents=True,exist_ok=True)
    hdmap=Map();hdmap.header.version=b'1';hdmap.header.district=b'Ranger parking laboratory'
    cases=[]
    for index,(style,width,aisle,entry) in enumerate(itertools.product(
            ['parallel','perpendicular','diagonal45'],[.55,.60,.90],[1.2,2.4],['front','rear'])):
        name=f'{style}_w{round(width*100):02d}_aisle{round(aisle*100):03d}_{entry}'
        ox=1000+(index%6)*15;oy=2000+(index//6)*15
        angle={'parallel':0,'perpendicular':math.pi/2,'diagonal45':math.pi/4}[style]
        length=1.4 if style=='parallel' else 1.1
        c,s=math.cos(angle),math.sin(angle)
        cx=0 if style=='parallel' else length/2*c
        cy=width/2 if style=='parallel' else width/2*c+length/2*s
        slot=Polygon([(ox+cx+u*c-v*s,oy+cy+u*s+v*c)
                      for u,v in [(-length/2,-width/2),(length/2,-width/2),(length/2,width/2),(-length/2,width/2)]])
        road=box(ox-4,oy-aisle,ox+4,oy)
        parts=[road,slot]
        if style=='diagonal45':
            a,b=list(slot.exterior.coords)[0],list(slot.exterior.coords)[3]
            # The diagonal mouth needs its full-width entry apron down to the
            # aisle. A vertical triangle would pinch a physically usable bay.
            lead=width*c/s+.05
            parts.append(Polygon([a,b,(b[0]-lead*c,b[1]-lead*s),(a[0]-lead*c,a[1]-lead*s)]))
        area=unary_union(parts)
        assert area.geom_type=='Polygon' and area.is_valid
        heading=angle+(math.pi if entry=='rear' else 0)
        # HDMap parking heading denotes final vehicle body orientation.
        spot=hdmap.parking_space.add();spot.id.id=name;spot.heading=math.remainder(heading,2*math.pi)
        for x,y in list(slot.exterior.coords)[:-1]:p=spot.polygon.point.add();p.x=x;p.y=y
        lot=hdmap.ad_area.add();lot.id.id=name+'_lot';lot.type=1
        for x,y in list(area.exterior.coords)[:-1]:p=lot.polygon.point.add();p.x=x;p.y=y
        lane=hdmap.lane.add();lane.id.id=name+'_lane';lane.length=8;lane.speed_limit=.5
        lane.type=2;lane.turn=1;lane.direction=1
        def curve(curve,y):
            seg=curve.segment.add();seg.s=0;seg.length=8;seg.heading=0
            seg.start_position.x=ox-4;seg.start_position.y=y
            for x in [ox-4,ox,ox+4]:p=seg.line_segment.point.add();p.x=x;p.y=y
        curve(lane.central_curve,oy-aisle/2)
        curve(lane.left_boundary.curve,oy);curve(lane.right_boundary.curve,oy-aisle)
        for bound in [lane.left_boundary,lane.right_boundary]:
            bound.length=8;bound.boundary_type.add().types.append(1)
        for field in ['left_sample','right_sample','left_road_sample','right_road_sample']:
            for ds in [0,8]:v=getattr(lane,field).add();v.s=ds;v.width=aisle/2
        overlap=hdmap.overlap.add();overlap.id.id=name+'_overlap'
        obj=overlap.object.add();obj.id.id=lane.id.id;obj.lane_overlap_info.start_s=3;obj.lane_overlap_info.end_s=5
        obj=overlap.object.add();obj.id.id=name;obj.parking_space_overlap_info.SetInParent()
        lane.overlap_id.add().id=overlap.id.id;spot.overlap_id.add().id=overlap.id.id
        r=hdmap.road.add();r.id.id=name+'_road';r.type=3
        section=r.section.add();section.id.id='0';section.lane_id.add().id=lane.id.id
        for boundary in [lane.left_boundary,lane.right_boundary]:
            section.boundary.outer_polygon.edge.add().curve.CopyFrom(boundary.curve)
        start=[ox-2,oy-aisle/2,0]
        # Localization is the rear axle, 0.26 m behind the body centre.
        goal=[ox+cx-.26*math.cos(heading),oy+cy-.26*math.sin(heading),math.remainder(heading,2*math.pi)]
        scene={'id':name,'name':name,'mapId':NAME,'duration':90,
               'description':f'Parking lab: {style}, width={width}m, aisle={aisle}m, {entry} entry. Full-body containment required.',
               'ego':{'id':'ego','position':{'x':start[0],'y':start[1],'z':0},'heading':0,
                      'vehicleProfile':'ranger_mini_v3','parkingSpaceId':name,
                      'size':{'x':.72,'y':.5,'z':.66},'activeRouteId':'approach',
                      'routes':[{'id':'approach','waypoints':[{'position':{'x':ox,'y':oy-aisle/2,'z':0}}]}]},
               'agents':[],'triggers':[]}
        text=json.dumps(scene,ensure_ascii=False,indent=2)+'\n';filename=name+'.worldsim.scenario.json'
        (out/filename).write_text(text)
        audit={'kind':'worldsim-evaluation','version':1,'scenario_sha256':hashlib.sha256(text.encode()).hexdigest(),
               'expectation':'park','reason':'Full vehicle inside the specified HDMap space; heading error <=2 degrees; stopped.',
               'parking':{'space_id':name,'style':style,'width_m':width,'length_m':length,'aisle_width_m':aisle,
                          'entry':entry,'goal':goal,'area':list(area.exterior.coords)[:-1],
                          'slot':list(slot.exterior.coords)[:-1]}}
        if probe:
            vertices=list(area.exterior.coords)[:-1]
            data=' '.join(map(str,start+goal+[1 if entry=='front' else -1,len(vertices)]+[v for p in vertices for v in p]+[0]))
            result=subprocess.run([str(probe)],input=data,text=True,capture_output=True,timeout=120)
            witness=[]
            if result.returncode==0:
                witness=[list(map(float,line.split(','))) for line in result.stdout.splitlines()]
                sys.path.insert(0,str(ROOT/'modules/simulation/simulator'))
                from quality_metrics import body
                assert all(area.covers(body(x,y,h,.62,.1,.25)) for x,y,h,g in witness),'Invalid witness body sweep'
                assert all(abs(math.remainder(b[2]-a[2],2*math.pi))<=1.8*math.hypot(b[0]-a[0],b[1]-a[1])+.001
                           for a,b in zip(witness,witness[1:])),'Invalid witness curvature/heading continuity'
                assert slot.covers(body(*goal,.62,.1,.25))
                (out/(name+'.witness.json')).write_text(json.dumps(witness))
            audit['validity']={'status':'VALID' if witness else 'UNPROVEN',
                               'reason':'Independent polygon sweep of kinematic witness' if witness else result.stderr}
        (out/(name+'.evaluation.json')).write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
        cases.append({'file':filename,'parking':audit['parking'],'validity':audit.get('validity',{'status':'UNPROVEN'})})
        print(name,cases[-1]['validity']['status'],flush=True)
    for stem in ['base_map','sim_map']:
        (map_dir/(stem+'.bin')).write_bytes(hdmap.SerializeToString())
        (map_dir/(stem+'.txt')).write_text(text_format.MessageToString(hdmap))
    (out/'parking-lab.suite.json').write_text(json.dumps({'kind':'worldsim-suite','version':1,
        'name':'Ranger 泊车 · 36 组合 · 平行/垂直/45° · 头入/尾入','mapId':NAME,
        'scenarios':[c['file'] for c in cases if c['validity']['status']=='VALID']},ensure_ascii=False,indent=2))
    (out/'manifest.json').write_text(json.dumps({'map':str(map_dir),'cases':cases},ensure_ascii=False,indent=2))
    return cases


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--probe',type=Path)
    args=parser.parse_args();generate(args.probe)
