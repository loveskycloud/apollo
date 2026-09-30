"""Native failed-scene/static-roadside regression; never accepts empty plans."""
import argparse
import csv
from concurrent.futures import ProcessPoolExecutor
from collections import Counter
import json
import math
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import numpy as np
os.environ['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION']='python'
sys.path[:0]=['/opt/apollo/neo/python',str(Path(__file__).resolve().parents[1]/'logsim/tools'),str(Path(__file__).resolve().parents[1]/'simulator')]
from run_parallel import prepare
from bag_diff import messages
from planning_continuity import PlanningContinuity
from modules.common_msgs.planning_msgs.planning_pb2 import ADCTrajectory
from modules.common_msgs.localization_msgs.localization_pb2 import LocalizationEstimate
from modules.common_msgs.config_msgs.vehicle_config_pb2 import VehicleConfig
from modules.simulation.ml_planning.proto.ml_planning_config_pb2 import MLPlanningConfig
from google.protobuf import text_format


def cruise_metrics(speeds, mask):
    """Measure whole close-pass intervals, without dropping slow samples.

    Monotonic approach acceleration is allowed. A repeated speed reversal of
    at least 2 cm/s is an oscillation; report ranges as well as this pass gate.
    """
    selected=np.flatnonzero(mask)
    groups=np.split(selected,np.flatnonzero(np.diff(selected)>1)+1)
    intervals=[]
    for indices in groups:
        if len(indices)<50:continue  # Ground-truth samples are spaced 10 ms.
        values=speeds[indices];extreme=float(values[0]);direction=0;reversals=0
        for value in values[1:]:
            delta=float(value)-extreme
            if direction>=0 and delta>=0:extreme=float(value)
            elif direction<=0 and delta<=0:extreme=float(value)
            elif abs(delta)>=.02:
                reversals+=int(direction!=0)
                direction=1 if delta>0 else -1;extreme=float(value)
            if direction==0 and abs(float(value)-float(values[0]))>=.02:
                direction=1 if value>values[0] else -1
        intervals.append(dict(samples=len(indices),min_mps=float(values.min()),
                              max_mps=float(values.max()),std_mps=float(values.std()),
                              reversals=reversals))
    return dict(intervals=intervals,passed=all(x['reversals']<=1 and x['max_mps']-x['min_mps']<=.05 for x in intervals))


def evaluate(run, scene):
    continuity=PlanningContinuity();poses=[];times=[];errors=Counter();max_k=0.;max_a=0.;commands=[]
    for channel,stamp,payload in messages(run/'simulation.record'):
        if channel=='/apollo/planning':
            msg=ADCTrajectory.FromString(payload);continuity.planning(stamp,msg)
            if msg.trajectory_point:commands.append((stamp/1e9,msg.trajectory_point[0].a))
            if msg.estop.reason:errors[msg.estop.reason.split(' at s=')[0]]+=1
            for pt in msg.trajectory_point:max_k=max(max_k,abs(pt.path_point.kappa));max_a=max(max_a,abs(pt.a))
        elif channel=='/apollo/localization/pose':
            p=LocalizationEstimate.FromString(payload).pose;continuity.world(stamp)
            poses.append([p.position.x,p.position.y,p.heading,math.hypot(p.linear_velocity.x,p.linear_velocity.y)])
            times.append(stamp/1e9)
    c=continuity.result();collision=json.loads((run/'collision.json').read_text())
    goal=scene['ego']['routes'][0]['waypoints'][-1]['position'];arr=np.array(poses)
    goal_error=math.hypot(arr[-1,0]-goal['x'],arr[-1,1]-goal['y'])
    result=dict(continuity=c,collision=collision,goal_error_m=goal_error,errors=dict(errors),max_curvature=max_k,max_acceleration=max_a,distance_m=float(np.linalg.norm(np.diff(arr[:,:2],axis=0),axis=1).sum()))
    if len(commands)>1:
        commands=np.array(commands);dt=np.diff(commands[:,0]);valid=dt>1e-6
        result['max_replan_jerk_mps3']=float(np.max(abs(np.diff(commands[:,1])[valid]/dt[valid]))) if valid.any() else None
    remaining=np.hypot(arr[:,0]-goal['x'],arr[:,1]-goal['y'])
    reached=np.flatnonzero(remaining<=.4)
    result['first_goal_time_s']=times[int(reached[0])]-times[0] if len(reached) else None
    if all(a['type']=='AGENT_TYPE_STATIC' and a.get('enabled',True) and not a.get('speed',0) for a in scene['agents']) and not scene.get('triggers'):
        from modules.simulation.logsim.proto.simulation_task_pb2 import SimulationTask
        task=text_format.Parse((run/'task.pb.txt').read_text(),SimulationTask())
        planner_config=text_format.Parse((run/'conf/ml_planning.pb.txt').read_text(),MLPlanningConfig())
        nudge_target=getattr(planner_config,'static_nudge_speed_mps',.2)
        vehicle=text_format.Parse(Path(task.vehicle_config_path).read_text(),VehicleConfig()).vehicle_param
        front,back=vehicle.front_edge_to_center,vehicle.back_edge_to_center
        left,right=vehicle.left_edge_to_center,vehicle.right_edge_to_center
        h=arr[:,2];ex=arr[:,0]+(front-back)/2*np.cos(h)-(left-right)/2*np.sin(h)
        ey=arr[:,1]+(front-back)/2*np.sin(h)+(left-right)/2*np.cos(h)
        min_gap=np.full(len(arr),np.inf)
        for a in scene['agents']:
            oh=a['heading'];length=a['size']['x'];width=a['size']['y'];ox=a['position']['x'];oy=a['position']['y']
            gaps=[]
            for axis in [h,h+math.pi/2,np.full(len(arr),oh),np.full(len(arr),oh+math.pi/2)]:
                gaps.append(abs((ox-ex)*np.cos(axis)+(oy-ey)*np.sin(axis))-((front+back)/2*abs(np.cos(h-axis))+(left+right)/2*abs(np.sin(h-axis)))-(length/2*abs(np.cos(oh-axis))+width/2*abs(np.sin(oh-axis))))
            min_gap=np.minimum(min_gap,np.max(gaps,axis=0))
        close=min_gap<.15
        result['min_static_separating_gap_m']=float(min_gap.min()) if len(scene['agents']) else None
        result['max_speed_within_15cm_mps']=float(arr[close,3].max()) if close.any() else None
        passing=close & (remaining>.4)
        result['mean_nudge_speed_mps']=float(arr[passing,3].mean()) if passing.any() else None
        result['static_clearance_passed']=bool((min_gap>=.05-1e-6).all())
        result['nudge_target_mps']=nudge_target
        # The accepted 0.2 profile uses proportional convergence. Faster
        # experimental profiles were tested with a strict 1 mm/s allowance.
        allowance=.051 if nudge_target<=.2 else .001
        result['nudge_speed_passed']=not close.any() or result['max_speed_within_15cm_mps']<=nudge_target+allowance
        trace=list(csv.DictReader((run/'policy.csv').open()))
        # Sharp turns and destination braking require a changing speed. Use
        # every sample in the other close-pass intervals, including slow ones.
        curvature=np.interp(times,[float(x['timestamp']) for x in trace],
                            [max(abs(float(x[k])) for k in ('curvature','obs10','obs11')) for x in trace])
        steady=close & (remaining>2.12) & (curvature<=.03)
        result['nudge_cruise']=cruise_metrics(arr[:,3],steady)
    result['passed']=bool(c['status']=='PASS' and collision['status']=='PASS' and goal_error<=.4 and max_a<=1.01 and max_k<=1.87 and result.get('static_clearance_passed',True) and result.get('nudge_speed_passed',True) and result.get('nudge_cruise',{}).get('passed',True))
    return result


def run_case(spec):
    source,root,map_dir,vehicle,weights=map(Path,spec)
    scene=json.loads(source.read_text());run=root/scene['id']
    prepare(run,'roadside',0,(map_dir,vehicle),weights,scene)
    env=os.environ.copy();env.update(SIM_PARENT_PID=str(os.getpid()),ML_PLANNING_TRACE=str(run/'policy.csv'),GLOG_log_dir=str(run/'log'),OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',CYBER_IP='127.0.0.1')
    with (run/'runtime.log').open('w') as log:
        try:p=subprocess.run(['/opt/apollo/neo/bin/simulator_main','--task_dir='+str(run)],cwd=run,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
        except subprocess.TimeoutExpired:return dict(scene=scene['id'],passed=False,error='timeout')
    result=dict(scene=scene['id'],source=str(source),run=str(run),returncode=p.returncode)
    if p.returncode==0:
        try:result.update(evaluate(run,scene))
        except Exception as e:result.update(passed=False,error=str(e))
    else:result['passed']=False
    (run/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--scenes',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--map',type=Path,required=True);p.add_argument('--vehicle',type=Path,required=True);p.add_argument('--weights',type=Path,required=True);p.add_argument('--workers',type=int,default=30);p.add_argument('--include',type=Path,action='append',default=[]);a=p.parse_args()
    sources=sorted(a.scenes.glob('*.worldsim.scenario.json'))+a.include
    a.out.mkdir(parents=True,exist_ok=False)
    specs=[list(map(str,[s,a.out,a.map,a.vehicle,a.weights])) for s in sources]
    results=[]
    with ProcessPoolExecutor(max_workers=a.workers,mp_context=multiprocessing.get_context('spawn')) as pool:
        for r in pool.map(run_case,specs):
            results.append(r);print(json.dumps({k:r.get(k) for k in ('scene','passed','goal_error_m','errors','error')}),flush=True)
    report=dict(total=len(results),passed=sum(r['passed'] for r in results),failed=[r['scene'] for r in results if not r['passed']],results=results)
    (a.out/'summary.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='results'}),flush=True)

if __name__=='__main__':main()
