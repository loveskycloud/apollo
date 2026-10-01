"""Versioned, independent closed-loop measurements (not training rewards).

World truth contact remains the native 10 ms collision check. Geometry/TTC and
comfort below use recorded observations at 100 ms; absence is NOT_EVALUATED.
"""
import csv
import json
import math
from pathlib import Path
import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

VERSION = 'closed-loop-v1'


def body(x, y, yaw, front, back, half):
    c, s = math.cos(yaw), math.sin(yaw)
    return Polygon([(x+u*c-v*s, y+u*s+v*c)
                    for u, v in [(front, half), (front, -half), (-back, -half), (-back, half)]])


def read_map(path):
    from modules.common_msgs.map_msgs.map_pb2 import Map
    from google.protobuf import text_format
    result = Map()
    path = Path(path)
    if (path/'base_map.bin').is_file():
        result.ParseFromString((path/'base_map.bin').read_bytes())
    else:
        text_format.Parse((path/'base_map.txt').read_text(), result)
    return result


def drivable_area(hdmap):
    pieces = []
    excluded = []
    for lane in hdmap.lane:
        boundaries = []
        for boundary in (lane.left_boundary, lane.right_boundary):
            boundaries.append([(p.x, p.y) for s in boundary.curve.segment for p in s.line_segment.point])
        if min(map(len, boundaries)) >= 2:
            p = Polygon(boundaries[0]+list(reversed(boundaries[1])))
            if not p.is_valid:
                raise ValueError('Invalid lane boundary polygon: '+lane.id.id)
            if p.area > 0: pieces.append(p)
    for lot in hdmap.ad_area:
        if lot.type not in (1, 2): continue
        p = Polygon([(v.x, v.y) for v in lot.polygon.point])
        if not p.is_valid: raise ValueError('Invalid parking lot polygon: '+lot.id.id)
        (pieces if lot.type == 1 else excluded).append(p)
    if not pieces: raise ValueError('Map has no independently evaluable drivable polygons')
    return unary_union(pieces).difference(unary_union(excluded))


def motion_metrics(samples):
    """Samples: seconds, x, y, heading, signed longitudinal speed."""
    if len(samples) < 3: return {'status': 'NOT_EVALUATED', 'reason': 'Insufficient poses'}
    a = np.asarray(samples, dtype=float)
    dt = np.diff(a[:, 0])
    if not np.isfinite(a).all() or (dt <= 0).any(): raise ValueError('Invalid pose timestamps/values')
    speed = a[:, 4]
    acc = np.diff(speed)/dt
    jerk = np.diff(acc)/((dt[1:]+dt[:-1])/2)
    yaw_rate = np.diff(np.unwrap(a[:, 3]))/dt
    lateral_acc = yaw_rate*(speed[:-1]+speed[1:])/2
    start = None; stops = []
    for i, row in enumerate(a):
        if abs(row[4]) < .02:
            if start is None: start = i
        elif start is not None:
            stops.append(float(a[i-1, 0]-a[start, 0])); start = None
    if start is not None: stops.append(float(a[-1, 0]-a[start, 0]))
    distance = float(np.linalg.norm(np.diff(a[:, 1:3], axis=0), axis=1).sum())
    stats = lambda v: {'p95': float(np.percentile(abs(v), 95)), 'max': float(max(abs(v)))}
    return {'status': 'MEASURED', 'elapsed_s': float(a[-1, 0]-a[0, 0]),
            'distance_travelled_m': distance, 'sample_period_s': float(np.median(dt)),
            'longitudinal_acceleration_mps2': stats(acc), 'lateral_acceleration_mps2': stats(lateral_acc),
            'jerk_mps3': stats(jerk), 'yaw_rate_radps': stats(yaw_rate),
            'max_speed_mps': float(max(abs(speed))), 'final_speed_mps': float(abs(speed[-1])),
            'longest_stop_s': max(stops, default=0), 'stops_over_10s': sum(s >= 10 for s in stops),
            'reverse_distance_m': float(sum(np.linalg.norm(a[i+1, 1:3]-a[i, 1:3])
                                          for i in range(len(dt)) if speed[i] < -.01))}


def evaluate(record, scene, map_dir, vehicle_path, trace_path, expectation=None):
    from bag_diff import messages
    from google.protobuf import text_format
    from modules.common_msgs.config_msgs.vehicle_config_pb2 import VehicleConfig
    from modules.common_msgs.localization_msgs.localization_pb2 import LocalizationEstimate
    from modules.common_msgs.perception_msgs.perception_obstacle_pb2 import PerceptionObstacles
    from modules.common_msgs.planning_msgs.planning_pb2 import ADCTrajectory
    vehicle = text_format.Parse(Path(vehicle_path).read_text(), VehicleConfig()).vehicle_param
    hdmap = read_map(map_dir)
    area = drivable_area(hdmap)
    samples=[]; snapshots=[]; latest=None; plans=[]; searches=[]; steady=[]; next_t=-math.inf
    for channel, stamp, payload in messages(record):
        t = stamp/1e9
        if channel == '/apollo/localization/pose':
            p = LocalizationEstimate.FromString(payload).pose
            latest=(t, p.position.x, p.position.y, p.heading,
                    p.linear_velocity.x*math.cos(p.heading)+p.linear_velocity.y*math.sin(p.heading))
            if t >= next_t-1e-6:
                samples.append(latest); next_t=t+.1
        elif channel == '/apollo/perception/obstacles' and latest is not None:
            obs=PerceptionObstacles.FromString(payload)
            snapshots.append((latest, [(o.id,o.position.x,o.position.y,o.theta,o.length,o.width,
                                       o.velocity.x,o.velocity.y,o.type) for o in obs.perception_obstacle]))
        elif channel == '/apollo/planning':
            p=ADCTrajectory.FromString(payload)
            if p.latency_stats.HasField('total_time_ms'):
                plans.append(p.latency_stats.total_time_ms)
                search=[v.time_ms for v in p.latency_stats.task_stats if v.name=='parking_search']
                searches.extend(search)
                if not search:steady.append(p.latency_stats.total_time_ms)
    if latest is not None and (not samples or latest[0]>samples[-1][0]+1e-6): samples.append(latest)
    motion=motion_metrics(samples)
    if motion['status'] != 'MEASURED':
        return {'version':VERSION,'status':'NOT_EVALUATED','motion':motion}
    f,b,h=vehicle.front_edge_to_center,vehicle.back_edge_to_center,vehicle.width/2
    kinematic_failures=0;max_curvature=0
    for a,p in zip(samples,samples[1:]):
        distance=math.hypot(p[1]-a[1],p[2]-a[2]);turn=abs(math.remainder(p[3]-a[3],2*math.pi))
        if turn>distance/vehicle.min_turn_radius*1.05+.002:kinematic_failures+=1
        if distance>.001:max_curvature=max(max_curvature,turn/distance)
    outside=[]; min_boundary=math.inf
    for p in samples:
        footprint=body(p[1],p[2],p[3],f,b,h)
        if not area.buffer(1e-6).covers(footprint): outside.append(p[0])
        min_boundary=min(min_boundary,footprint.distance(area.boundary))
    trace=[]
    if Path(trace_path).is_file():
        with Path(trace_path).open() as stream: trace=list(csv.DictReader(stream))
    trace_time=[float(r['timestamp']) for r in trace];trace_l=[float(r['l']) for r in trace]
    early_return=0;return_gaps=[]
    minimum=math.inf; min_ttc=math.inf; exposures=0; recovery=[]; threatened=False; cleared_at=None;near_miss_frames=0
    oncoming=0; oncoming_min=math.inf; obstacle_frames=0
    for p, obs in snapshots:
        ego=body(p[1],p[2],p[3],f,b,h); local_gap=math.inf
        for oid,x,y,yaw,length,width,vx,vy,kind in obs:
            other=body(x,y,yaw,length/2,length/2,width/2)
            gap=ego.distance(other); minimum=min(minimum,gap); local_gap=min(local_gap,gap)
            near_miss_frames+=int(0<gap<.25)
            if trace and kind==5:
                lateral=float(np.interp(p[0],trace_time,trace_l));prior=float(np.interp(p[0]-.1,trace_time,trace_l))
                dh=math.remainder(p[3]-yaw,2*math.pi)
                along=(ego.centroid.x-x)*math.cos(yaw)+(ego.centroid.y-y)*math.sin(yaw)
                extent=(f+b)/2*abs(math.cos(dh))+h*abs(math.sin(dh))
                if abs(dh)<.3 and along>0 and abs(lateral)>.1 and abs(prior)-abs(lateral)>.003:
                    rear_gap=along-extent-length/2;return_gaps.append(rear_gap);early_return+=int(rear_gap<.35)
            if gap<1.5: obstacle_frames+=1
            opposite=vx*math.cos(p[3])+vy*math.sin(p[3])<-.05 and math.cos(yaw-p[3])<-.7
            if opposite and gap<5:
                oncoming+=1; oncoming_min=min(oncoming_min,gap)
            # Constant velocity, constant heading OBB TTC, 0.1 s / 3 s horizon.
            # Screen distant actors before constructing projected footprints.
            relative=math.hypot(vx-p[4]*math.cos(p[3]),vy-p[4]*math.sin(p[3]))
            if gap>relative*3+.01: continue
            for k in range(31):
                dt=k*.1
                e=body(p[1]+p[4]*math.cos(p[3])*dt,p[2]+p[4]*math.sin(p[3])*dt,p[3],f,b,h)
                o=body(x+vx*dt,y+vy*dt,yaw,length/2,length/2,width/2)
                if e.intersects(o): min_ttc=min(min_ttc,dt); exposures+=int(dt<1); break
        if local_gap<1.5:
            threatened=True; cleared_at=None
        elif threatened and cleared_at is None: cleared_at=p[0]
        if cleared_at is not None and abs(p[4])>.1:
            recovery.append(p[0]-cleared_at); threatened=False; cleared_at=None
    remaining_recovery=(snapshots[-1][0][0]-cleared_at if cleared_at is not None else None)
    route_progress={'status':'NOT_EVALUATED'}
    if trace and 'remaining' in trace[0]:
        initial=float(trace[0]['remaining']); final=float(trace[-1]['remaining'])
        route_progress={'status':'MEASURED','remaining_m':final,
                        'fraction':max(0,min(1,1-final/initial)) if initial>0 else None}
    finite=lambda x: x if math.isfinite(x) else None
    result={'version':VERSION,'status':'FAIL' if outside or kinematic_failures else 'PASS',
            'scope':'Recorded executed poses; geometry/TTC sampled at 100 ms; perfect_planning is not Control validation',
            'motion':motion,'route_progress':route_progress,
            'passing_return':{'status':'MEASURED' if return_gaps else 'NOT_EVALUATED',
                              'early_return_frames':early_return,'minimum_rear_clearance_m':min(return_gaps) if return_gaps else None,
                              'required_rear_clearance_m':.35,'scope':'Same-heading vehicle passes with lateral return; not applicable to every static nudge'},
            'kinematics':{'status':'FAIL' if kinematic_failures else 'PASS','violating_intervals':kinematic_failures,
                          'maximum_measured_curvature_per_m':max_curvature,'maximum_vehicle_curvature_per_m':1/vehicle.min_turn_radius},
            'road':{'status':'FAIL' if outside else 'PASS','outside_frames':len(outside),
                    'sample_count':len(samples),'first_outside_s':outside[0] if outside else None,
                    'minimum_boundary_distance_m':finite(min_boundary)},
            'interaction':{'status':'MEASURED' if math.isfinite(minimum) else 'NOT_EVALUATED',
                           'minimum_body_gap_m':finite(minimum),'min_ttc_3s':finite(min_ttc),
                           'gap_below_025m_pair_frames':near_miss_frames,
                           'ttc_below_1s_pair_frames':exposures,'near_obstacle_pair_frames':obstacle_frames,
                           'oncoming_pair_frames':oncoming,'oncoming_min_gap_m':finite(oncoming_min),
                           'observed_recovery_delay_s':recovery,'unrecovered_after_clear_s':remaining_recovery,
                           'clear_definition':'All observed actors at least 1.5 m from ego body; diagnostic, not right-of-way proof'},
            'policy':{'status':'MEASURED' if trace else 'NOT_EVALUATED',
                      'safety_intervention_fraction':sum(int(r['shield'])!=0 for r in trace)/len(trace) if trace else None},
            'runtime':{'status':'MEASURED' if plans else 'NOT_EVALUATED',
                       'startup_planning_latency_ms':plans[0] if plans else None,
                       'planning_latency_ms_p95':float(np.percentile(plans,95)) if plans else None,
                       'planning_latency_ms_p99':float(np.percentile(plans,99)) if plans else None,
                       'over_100ms_frames':sum(v>100 for v in plans)}}
    parking=scene['ego'].get('parkingSpaceId')
    if parking:
        from parking_metrics import evaluate_parking
        spot=next(s for s in hdmap.parking_space if s.id.id==parking)
        contract=dict((expectation or {}).get('parking',{}))
        if 'goal' not in contract:
            center=Polygon([(p.x,p.y) for p in spot.polygon.point]).centroid
            contract['goal']=[center.x-(f-b)/2*math.cos(spot.heading),center.y-(f-b)/2*math.sin(spot.heading),spot.heading]
        result['parking']=evaluate_parking(samples,Polygon([(p.x,p.y) for p in spot.polygon.point]),
                                          spot.heading,(f,b,h),contract,plans,searches,steady)
        if result['parking']['status']!='PASS':result['status']='FAIL'
        dynamic=(expectation or {}).get('parking_dynamic')
        if dynamic:
            from parking_trigger_metrics import evaluate as evaluate_triggers
            result['parking_dynamic']=evaluate_triggers(snapshots,scene,dynamic)
            if result['parking_dynamic']['status']!='PASS':result['status']='FAIL'
        interaction=(expectation or {}).get('parking_interaction')
        if interaction:
            from parking_interaction_metrics import evaluate as evaluate_interaction
            result['parking_interaction']=evaluate_interaction(snapshots,scene,interaction)
            if result['parking_interaction']['status']!='PASS':result['status']='FAIL'
    return result
