"""Independent physical checks on recorded poses, map widths and perception boxes."""
import bisect
import csv
import json
import math
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'logsim/tools'))
from bag_diff import messages
from google.protobuf.text_format import Parse
from modules.common_msgs.map_msgs.map_pb2 import Map
from modules.common_msgs.localization_msgs.localization_pb2 import LocalizationEstimate
from modules.common_msgs.perception_msgs.perception_obstacle_pb2 import PerceptionObstacles
from modules.common_msgs.planning_msgs.planning_pb2 import ADCTrajectory
from modules.common_msgs.routing_msgs.routing_pb2 import RoutingResponse
from modules.simulation.logsim.proto.simulation_task_pb2 import SimulationTask
from evaluate import box, overlap, clearance


def centering_metrics(rows):
    """Allow 3 m to recover after a bend/hazard, then require lane centering."""
    start_s = None
    offsets = []
    for row in rows:
        clear = float(row['obs5']) < .5 and max(abs(float(row[k])) for k in ('curvature', 'obs10', 'obs11')) < .03
        if not clear:
            start_s = None
            continue
        s = float(row['s'])
        if start_s is None:
            start_s = s
        if s-start_s >= 3 and float(row['speed']) > .2:
            offsets.append(abs(float(row['l'])))
    return {'clear_straight_samples': len(offsets),
            'clear_straight_max_abs_l_m': max(offsets) if offsets else None,
            'clear_straight_rms_l_m': math.sqrt(sum(v*v for v in offsets)/len(offsets)) if offsets else None,
            'centering_passed': not offsets or max(offsets) <= .10}


def evaluate(record, scenario_path, trace_path):
    scene=json.loads(Path(scenario_path).read_text())
    trace=list(csv.DictReader(Path(trace_path).open()))
    trace_time=np.array([float(r['timestamp']) for r in trace])
    trace_l=np.array([float(r['l']) for r in trace])
    # Independent polyline road containment, not the planner's spline/shield.
    task=Parse((Path(record).parent/'task.pb.txt').read_text(),SimulationTask())
    map_dir=Path(task.map_dir)
    m=Map()
    if (map_dir/'base_map.bin').is_file():
        m.ParseFromString((map_dir/'base_map.bin').read_bytes())
    else:
        Parse((map_dir/'base_map.txt').read_text(),m)
    segments=[]
    for lane in m.lane:
        points=[(p.x,p.y) for seg in lane.central_curve.segment for p in seg.line_segment.point]
        pos=0.
        for a,b in zip(points,points[1:]):
            length=math.dist(a,b)
            widths=[]
            for samples in (lane.left_sample,lane.right_sample):
                widths.append(float(np.interp(pos+length/2,[s.s for s in samples],[s.width for s in samples])))
            segments.append((*a,b[0]-a[0],b[1]-a[1],*widths))
            pos+=length
    seg=np.array(segments); origin=seg[0,:2].copy();seg[:,:2]-=origin
    poses=[];obstacles=[];obstacle_frames=[];collisions=0;min_clearance=float('inf');estop=0;plans=0;max_k=0;max_a=0;route=[]
    for channel,stamp,data in messages(record):
        if channel=='/apollo/raw_routing_response':
            r=RoutingResponse.FromString(data);route=[s.id for rd in r.road for p in rd.passage for s in p.segment]
        elif channel=='/apollo/perception/obstacles':
            obstacle_frames.append((stamp,{o.id:o for o in PerceptionObstacles.FromString(data).perception_obstacle}))
        elif channel=='/apollo/planning':
            p=ADCTrajectory.FromString(data);plans+=1;estop+=p.estop.is_estop
            for t in p.trajectory_point:max_k=max(max_k,abs(t.path_point.kappa));max_a=max(max_a,abs(t.a))
        elif channel=='/apollo/localization/pose':
            p=LocalizationEstimate.FromString(data).pose;x,y,h=p.position.x,p.position.y,p.heading
            poses.append((stamp,x,y,h,math.hypot(p.linear_velocity.x,p.linear_velocity.y)))
    if not poses:raise ValueError('No ego poses recorded')
    # Align 100 Hz ego poses with 10 Hz perception using the neighboring actual
    # samples. Holding a moving box at its previous position creates false
    # collisions; constant-velocity extrapolation is used only after the last sample.
    stamps=[t for t,_ in obstacle_frames]
    near_miss_samples=0;early_return_samples=0;return_gaps=[];min_clearance_time=None
    for stamp,x,y,h,_ in poses:
        index=bisect.bisect_right(stamps,stamp)-1
        if index<0: continue
        ot,objects=obstacle_frames[index]
        nt,next_objects=obstacle_frames[min(index+1,len(obstacle_frames)-1)]
        ego=box(x+.26*math.cos(h),y+.26*math.sin(h),h,.72,.5)
        for oid,o in objects.items():
            if nt>ot and oid in next_objects:
                other=next_objects[oid];f=(stamp-ot)/(nt-ot)
                ox=o.position.x+f*(other.position.x-o.position.x)
                oy=o.position.y+f*(other.position.y-o.position.y)
            else:
                dt=(stamp-ot)/1e9;ox=o.position.x+dt*o.velocity.x;oy=o.position.y+dt*o.velocity.y
            poly=box(ox,oy,o.theta,o.length,o.width)
            hit=overlap(ego,poly);gap=clearance(ego,poly)
            collisions+=hit
            near_miss_samples+=not hit and gap < .25
            if gap < min_clearance:
                min_clearance=gap;min_clearance_time=stamp/1e9
            # Independent body-edge check during a same-direction vehicle pass.
            lateral=float(np.interp(stamp/1e9,trace_time,trace_l))
            prior=float(np.interp(stamp/1e9-.1,trace_time,trace_l))
            dh=math.remainder(h-o.theta,2*math.pi)
            along=(x+.26*math.cos(h)-ox)*math.cos(o.theta)+(y+.26*math.sin(h)-oy)*math.sin(o.theta)
            extent=.36*abs(math.cos(dh))+.25*abs(math.sin(dh))
            rear_gap=along-extent-o.length/2
            if o.type==5 and abs(dh)<.3 and along>0 and abs(lateral)>.1 and abs(prior)-abs(lateral)>.003:
                return_gaps.append(rear_gap)
                early_return_samples+=rear_gap < .35
    arr=np.array(poses);corners=[]
    for _,x,y,h,_ in poses:corners.extend(box(x+.26*math.cos(h),y+.26*math.sin(h),h,.72,.5))
    points=np.array(corners)-origin;offroad=0;max_excess=-float('inf')
    for batch in np.array_split(points,max(1,len(points)//256)):
        delta=batch[:,None,:]-seg[None,:,:2];d=seg[:,2:4]
        fraction=np.clip((delta*d).sum(-1)/(d*d).sum(-1),0,1)
        error=delta-fraction[...,None]*d
        best=(error*error).sum(-1).argmin(-1)
        signed=(delta[:,:,1]*d[:,0]-delta[:,:,0]*d[:,1])/np.linalg.norm(d,axis=-1)
        lat=signed[np.arange(len(batch)),best]
        excess=np.where(lat>=0,lat-seg[best,4],-lat-seg[best,5])
        max_excess=max(max_excess,float(excess.max()));offroad+=int((excess>.015).sum())
    ego_scene=scene['ego'];active=ego_scene.get('activeRouteId',ego_scene.get('active_route_id'))
    end=next(r for r in ego_scene['routes'] if r['id']==active)['waypoints'][-1]['position']
    goal=math.hypot(arr[-1,1]-end['x'],arr[-1,2]-end['y'])
    turn=float(np.abs(np.diff(np.unwrap(arr[:,3]))).sum())
    distance=float(np.linalg.norm(np.diff(arr[:,1:3],axis=0),axis=1).sum())
    shield=sum(int(r['shield']) for r in trace)
    centering=centering_metrics(trace)
    passed=collisions==0 and offroad==0 and estop==0 and goal<.4 and distance>20 and plans>=590 and max_a<=1.01 and max_k<=1.87
    passed=passed and centering['centering_passed'] and early_return_samples==0
    if 'turn' in scene['id']:passed=passed and turn>1.2 and 'Lane_48' in route
    result=dict(passed=bool(passed),record=str(record),goal_error_m=goal,distance_m=distance,heading_change_rad=turn,
        collisions=collisions,offroad_corners=offroad,max_boundary_excess_m=max_excess,estop_frames=estop,
        planning_frames=plans,max_curvature=max_k,max_acceleration=max_a,shield_frames=shield,
        min_clearance_m=min_clearance if math.isfinite(min_clearance) else None,
        near_miss_threshold_m=.25,near_miss_samples=int(near_miss_samples),min_clearance_time_s=min_clearance_time,
        early_return_samples=int(early_return_samples),return_min_rear_gap_m=min(return_gaps) if return_gaps else None,
        route_lanes=route,**centering)
    Path(record).parent.joinpath('metrics.json').write_text(json.dumps(result,indent=2))
    return result
