"""Generate scenarios on the existing, unmodified 1haolou HDMap."""
import json
import math
from pathlib import Path
import sys
sys.path.insert(0, '/opt/apollo/neo/python')
from google.protobuf.text_format import Parse
from modules.common_msgs.map_msgs.map_pb2 import Map

ROOT = Path(__file__).resolve().parents[3]
MAP = ROOT / 'data/map_data/1haolou_202608241047qh'
VEHICLE = ROOT / 'profiles/ranger_mini_v3/modules/common/data/vehicle_param.pb.txt'

def create():
    m = Parse((MAP/'base_map.txt').read_text(), Map())
    lanes = {l.id.id:l for l in m.lane}
    def point(lane_id, s, lateral=0):
        p = [p for seg in lanes[lane_id].central_curve.segment for p in seg.line_segment.point]
        for a,b in zip(p,p[1:]):
            d=math.hypot(b.x-a.x,b.y-a.y)
            if s <= d:
                heading=math.atan2(b.y-a.y,b.x-a.x)
                return {'x':a.x+s/d*(b.x-a.x)-lateral*math.sin(heading),
                        'y':a.y+s/d*(b.y-a.y)+lateral*math.cos(heading),'z':0},heading
            s-=d
        raise ValueError('Point outside lane')
    out=Path(__file__).resolve().parent/'examples/1haolou'
    out.mkdir(exist_ok=True)
    for name, turn, obstacle, offset in [
        ('straight_clear',False,False,0), ('straight_obstacle',False,True,.3),
        ('straight_centered',False,True,0),
        ('turn_clear',True,False,0), ('turn_obstacle',True,True,.3),
        ('turn_obstacle_right',True,True,-.3)]:
        start,heading=point('Lane_45' if turn else 'Lane_9',.5 if turn else 2)
        end,_=point('Lane_47' if turn else 'Lane_9',18 if turn else 27)
        position,oh=point('Lane_47' if turn else 'Lane_9',8 if turn else 15,offset)
        scene={'id':f'1haolou-{name}', 'name':f'1haolou {name.replace("_"," ")}',
            'description':'Unified ML planner test. Obstacle becomes observable after 5 seconds.',
            'duration':60,'mapId':MAP.name,
            'ego':{'id':'ego','position':start,'heading':heading,
                'size':{'x':.72,'y':.5,'z':.66},'activeRouteId':'route',
                'routes':[{'id':'route','waypoints':[{'position':end}]}]},
            'agents':[], 'triggers':[]}
        if obstacle:
            scene['agents']=[{'id':'obstacle','type':'AGENT_TYPE_STATIC','position':position,
                'heading':oh,'size':{'x':.3,'y':.18,'z':.5},'enabled':False}]
            scene['triggers']=[{'id':'appear','type':'TRIGGER_TYPE_TIME','time':5,
                'actions':[{'kind':'ACTION_ENABLE','targetAgentId':'obstacle'}]}]
        (out/f'{name}.worldsim.scenario.json').write_text(json.dumps(scene,indent=2)+'\n')
    # WorldSim-native dynamic actors and triggers; planner only sees perception.
    base=json.loads((out/'turn_clear.worldsim.scenario.json').read_text())
    import copy
    for name, pedestrian, side, distance, speed in [
        ('pedestrian_left',True,1,3.2,.55),
        ('pedestrian_right',True,-1,3.2,.55),
        ('pedestrian_sudden',True,1,2.4,.75),
        ('crossing_vehicle',False,-1,3.5,.4),
    ]:
        scene=copy.deepcopy(base);scene['id']='1haolou-'+name;scene['name']='1haolou '+name.replace('_',' ')
        start,heading=point('Lane_47',8,side*1.3)
        end,_=point('Lane_47',8,-side*1.6)
        scene['agents']=[{'id':'crossing','type':'AGENT_TYPE_PEDESTRIAN' if pedestrian else 'AGENT_TYPE_VEHICLE',
            'position':start,'heading':heading-side*math.pi/2,'speed':speed,'enabled':False,
            'size':{'x':.4 if pedestrian else .6,'y':.4 if pedestrian else .35,'z':1.7 if pedestrian else .7},
            'activeRouteId':'cross','routes':[{'id':'cross','pathType':'polyline','waypoints':[{'position':start},{'position':end}]}]}]
        scene['triggers']=[{'id':'sudden-entry','type':'TRIGGER_TYPE_AGENT_DISTANCE',
            'agentAId':'ego','agentBId':'crossing','distance':distance,'compare':'less',
            'actions':[{'kind':'ACTION_ENABLE','targetAgentId':'crossing'}]}]
        (out/f'{name}.worldsim.scenario.json').write_text(json.dumps(scene,indent=2)+'\n')
    scene=copy.deepcopy(base);scene['id']='1haolou-moving-lead';scene['name']='1haolou moving lead vehicle'
    start,heading=point('Lane_47',4,.22);end,_=point('Lane_47',21,.22)
    scene['agents']=[{'id':'lead','type':'AGENT_TYPE_VEHICLE','position':start,'heading':heading,'speed':.35,
        'size':{'x':.6,'y':.35,'z':.7},'activeRouteId':'lead-route',
        'routes':[{'id':'lead-route','pathType':'polyline','waypoints':[{'position':start},{'position':end}]}]}]
    scene['triggers']=[]
    (out/'moving_lead.worldsim.scenario.json').write_text(json.dumps(scene,indent=2)+'\n')
    scene=json.loads((out/'straight_obstacle.worldsim.scenario.json').read_text())
    scene['id']='1haolou-slalom';scene['name']='1haolou multiple static obstacles'
    scene['agents']=[];scene['triggers']=[]
    for i,(s,l) in enumerate([(12,.3),(20,-.3)]):
        position,heading=point('Lane_9',s,l)
        scene['agents'].append({'id':f'cone-{i}','type':'AGENT_TYPE_STATIC','position':position,'heading':heading,
            'size':{'x':.3,'y':.18,'z':.5}})
    (out/'static_slalom.worldsim.scenario.json').write_text(json.dumps(scene,indent=2)+'\n')
    return out

if __name__=='__main__':
    print(create())
