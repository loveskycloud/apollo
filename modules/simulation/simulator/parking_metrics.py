"""Independent mission outcomes; aggregate denominators never exclude failures."""
import math
import numpy as np
from shapely.geometry import Polygon

VERSION='parking-v2'
LIMITS={'heading_deg':2.,'straight_heading_deg':1.,'straight_lateral_m':.01,
        'straight_longitudinal_m':.03,'target_distance_m':.03,'stop_speed_mps':.02,'exit_clearance_m':.1}


def evaluate_parking(samples, polygon, heading, vehicle, contract, latencies, searches, steady):
    from quality_metrics import body
    f,b,h=vehicle
    operation=contract.get('operation','in');variant=contract.get('variant','base')
    a=np.asarray(samples,dtype=float);last=a[-1];moving=[p for p in samples if abs(p[4])>.01]
    gears=[1 if p[4]>0 else -1 for p in moving];changes=sum(x!=y for x,y in zip(gears,gears[1:]))
    c,s=math.cos(heading),math.sin(heading)
    parked=[];straight=[];details=[]
    for p in a:
        footprint=body(p[1],p[2],p[3],f,b,h)
        dx=footprint.centroid.x-polygon.centroid.x;dy=footprint.centroid.y-polygon.centroid.y
        longitudinal=dx*c+dy*s;lateral=-dx*s+dy*c
        angle=abs(math.degrees(math.remainder(p[3]-heading,2*math.pi)))
        inside=polygon.buffer(1e-6).covers(footprint)
        stopped=abs(p[4])<LIMITS['stop_speed_mps']
        parked.append(inside and stopped and angle<=LIMITS['heading_deg'])
        straight.append(parked[-1] and angle<=LIMITS['straight_heading_deg'] and abs(lateral)<=LIMITS['straight_lateral_m'] and abs(longitudinal)<=LIMITS['straight_longitudinal_m'])
        details.append((angle,longitudinal,lateral,inside,footprint.distance(polygon.boundary)))
    indices=[i for i,p in enumerate(parked) if p]
    selected=len(a)-1
    parking_success=bool(indices) if operation=='in_out' else bool(parked[-1]) if operation=='in' else None
    def longest_hold(mask):
        best=(0.,None);run_start=None
        for i,ok in enumerate(mask+[False]):
            if ok and run_start is None:run_start=i
            if not ok and run_start is not None:
                duration=float(a[i-1,0]-a[run_start,0])
                if duration>=best[0]:best=(duration,(run_start+i-1)//2)
                run_start=None
        return best
    dwell,park_sample=longest_hold(parked)
    straight_dwell,straight_sample=longest_hold(straight)
    straight_success=bool(straight_dwell>=1.) if operation=='in_out' else bool(straight[-1]) if operation=='in' else None
    if operation=='in_out':
        selected=straight_sample if straight_success else park_sample if park_sample is not None else selected
    final_body=body(last[1],last[2],last[3],f,b,h)
    goal=contract.get('goal',contract.get('park_goal'))
    goal_distance=math.hypot(last[1]-goal[0],last[2]-goal[1])
    final_heading=abs(math.degrees(math.remainder(last[3]-goal[2],2*math.pi)))
    exit_gap=final_body.distance(polygon)
    exit_success=(goal_distance<=LIMITS['target_distance_m'] and final_heading<=LIMITS['heading_deg'] and
                  abs(last[4])<LIMITS['stop_speed_mps'] and exit_gap>=LIMITS['exit_clearance_m']) if operation!='in' else None
    entry_index=indices[0] if indices else None
    entry_gears=[p[4] for p in samples[:entry_index+1] if abs(p[4])>.01] if entry_index is not None else []
    entry=('front' if entry_gears[-1]>0 else 'rear') if entry_gears else None
    if parking_success and contract.get('entry') and entry!=contract['entry']:
        parking_success=False
    outcome=(parking_success if operation=='in' else exit_success if operation=='out' else parking_success and exit_success and dwell>=1.)
    tail=a[a[:,0]>=last[0]-5.000001]
    held=(tail[-1,0]-tail[0,0]>=4.99 and np.max(np.abs(tail[:,4]))<.02 and
          np.max(np.linalg.norm(tail[:,1:3]-a[0,1:3],axis=1))<=.03)
    if variant=='occupied':outcome=bool(held);parking_success=False;straight_success=False
    angle,longitudinal,lateral,inside,clearance=details[selected]
    distance=float(np.linalg.norm(np.diff(a[:,1:3],axis=0),axis=1).sum())
    stats=lambda values: {'count':len(values),'p50':float(np.percentile(values,50)) if values else None,
                         'p95':float(np.percentile(values,95)) if values else None,
                         'p99':float(np.percentile(values,99)) if values else None,'max':max(values) if values else None}
    return {'version':VERSION,'status':'PASS' if outcome else 'FAIL','operation':operation,'variant':variant,
            'style':contract.get('style'),'width_m':contract.get('width_m'),'aisle_width_m':contract.get('aisle_width_m'),
            'parking_success':parking_success,'straight_parking':straight_success,'exit_success':exit_success,
            'safe_rejection':bool(held) if variant=='occupied' else None,'entry':entry,'expected_entry':contract.get('entry'),
            'body_inside':inside,'heading_error_deg':angle,'longitudinal_error_m':longitudinal,'lateral_error_m':lateral,
            'boundary_clearance_m':clearance,'side_clearance_imbalance_m':2*abs(lateral),
            'final_target_distance_m':goal_distance,'final_heading_error_deg':final_heading,'exit_body_clearance_m':exit_gap,
            'parked_dwell_s':dwell,'straight_dwell_s':straight_dwell,
            'precision_sample':'midpoint_of_longest_aligned_hold' if operation=='in_out' and straight_success else 'midpoint_of_longest_parked_hold' if operation=='in_out' and park_sample is not None else 'terminal_pose',
            'first_parked_s':float(a[entry_index,0]-a[0,0]) if entry_index is not None and operation!='out' else None,
            'maneuver_duration_s':float(last[0]-a[0,0]),'time_to_first_motion_s':moving[0][0]-a[0,0] if moving else None,
            'gear_changes':changes,'distance_m':distance,
            'distance_over_witness':distance/contract['witness_length_m'] if contract.get('witness_length_m') else None,
            'planning_ms':stats(latencies),'search_ms':stats(searches),
            'steady_planning_ms':stats(steady),
            'limits':LIMITS,'scope':'Recorded full-body geometry; thresholds for this Ranger fixture, not a certified parking standard'}
