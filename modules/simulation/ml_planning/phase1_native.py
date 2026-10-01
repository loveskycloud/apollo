"""Frozen real-map perception-only WorldSim suite and paired model validation."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import csv
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import sys
import numpy as np

HERE=Path(__file__).resolve().parent
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"]="python"
sys.path[:0]=["/opt/apollo/neo/python",str(HERE.parent/"simulator")]
from google.protobuf.text_format import Parse
from modules.common_msgs.map_msgs.map_pb2 import Map
from fixtures_1haolou import MAP, VEHICLE
from run_parallel import prepare, execute


def quality_metrics(rows):
    """Measured localization state at perception ticks, NOT actor target changes."""
    if len(rows)<3:
        raise ValueError("Insufficient quality trace")
    t=np.array([float(r["timestamp"]) for r in rows])
    v=np.array([float(r["speed"]) for r in rows])
    dt=np.diff(t)
    if not np.isfinite(t).all() or not np.isfinite(v).all() or (dt<=0).any():
        raise ValueError("Invalid trace timestamps/speeds")
    a=np.diff(v)/dt
    jerk=np.diff(a)/((dt[1:]+dt[:-1])/2)
    moving=(v[1:]+v[:-1])>.1
    jm=moving[1:]|moving[:-1]
    lateral=np.array([float(r["l"]) for r in rows])
    return dict(sample_period_median_s=float(np.median(dt)),
                rms_acceleration_mps2=float(np.sqrt(np.mean(a[moving]**2))) if moving.any() else None,
                p95_abs_jerk_mps3=float(np.percentile(abs(jerk[jm]),95)) if jm.any() else None,
                max_abs_jerk_mps3=float(max(abs(jerk))),
                max_lateral_step_m=float(max(abs(np.diff(lateral)))),
                safety_intervention_fraction=float(np.mean([int(r["shield"])!=0 for r in rows])),
                final_speed_mps=float(v[-1]))


def generate(out):
    out.mkdir(parents=True,exist_ok=False)
    m=Parse((MAP/"base_map.txt").read_text(),Map())
    lane=next(l for l in m.lane if l.id.id=="Lane_9")
    points=[p for seg in lane.central_curve.segment for p in seg.line_segment.point]
    def point(s,l=0):
        for a,b in zip(points,points[1:]):
            d=math.hypot(b.x-a.x,b.y-a.y)
            if s<=d:
                h=math.atan2(b.y-a.y,b.x-a.x)
                return dict(x=a.x+s/d*(b.x-a.x)-l*math.sin(h),y=a.y+s/d*(b.y-a.y)+l*math.cos(h),z=0),h
            s-=d
        raise ValueError("Route exceeds lane")
    definitions=[("clear",[]),
                 ("nudge_left",[("static",12,.32,0)]),
                 ("nudge_right",[("static",12,-.32,0)]),
                 ("nudge_alternating",[("static",10,.32,0),("static",17,-.32,0)]),
                 ("meeting_slow",[("meeting",17,.30,.3)]),
                 ("meeting_fast",[("meeting",20,.30,.6)]),
                 ("meeting_offset",[("meeting",18,.46,.45)]),
                 ("meeting_nudge_right",[("static",12,-.32,0),("meeting",20,.40,.45)]),
                 ("meeting_nudge_left",[("static",12,.32,0),("meeting",20,.40,.45)]),
                 ("successive_meeting",[("meeting",16,.38,.4),("meeting",23,.38,.4)]),
                 ("lead",[("lead",8,.20,.35)]),
                 ("meeting_nudge_late",[("static",17,-.32,0),("meeting",25,.35,.6)])]
    paths=[]
    for name,actors in definitions:
        start,heading=point(2);goal,_=point(27)
        scene=dict(id="phase1-"+name,name="Phase 1 "+name,duration=90,mapId=MAP.name,
                   ego=dict(id="ego",position=start,heading=heading,size=dict(x=.72,y=.5,z=.66),
                            activeRouteId="route",routes=[dict(id="route",waypoints=[dict(position=goal)])]),
                   agents=[],triggers=[])
        for i,(kind,s,l,speed) in enumerate(actors):
            position,h=point(s,l)
            agent=dict(id=f"actor-{i}",type="AGENT_TYPE_STATIC" if kind=="static" else "AGENT_TYPE_VEHICLE",
                       position=position,heading=h+(math.pi if kind=="meeting" else 0),speed=speed,
                       size=dict(x=.5 if kind=="static" else .8,y=.22 if kind=="static" else .35,z=.7))
            if kind!="static":
                end,_=point(.5 if kind=="meeting" else 38,l)
                agent.update(activeRouteId="actor-route",routes=[dict(id="actor-route",pathType="polyline",
                             waypoints=[dict(position=position,speed=speed),dict(position=end,speed=speed)])])
            scene["agents"].append(agent)
        path=out/(name+".worldsim.scenario.json")
        path.write_text(json.dumps(scene,indent=2)+"\n");paths.append(path)
    (out/"manifest.json").write_text(json.dumps(dict(map=str(MAP),
        map_sha256=hashlib.sha256((MAP/"base_map.txt").read_bytes()).hexdigest(),
        scope="Lane_9 / 12 fixed cases / perfect_planning / nonreactive actors",
        files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}),indent=2)+"\n")
    (out/"phase1-interaction.suite.json").write_text(json.dumps(dict(
        kind="worldsim-suite",version=1,name="第一期 · 感知结果 · 会车与 nudge · 12 场景",
        mapId=MAP.name,scenarios=[p.name for p in paths]),ensure_ascii=False,indent=2)+"\n")
    return paths


def run(spec):
    source,root,weights=map(Path,spec)
    scene=json.loads(source.read_text());directory=root/source.stem
    item=prepare(directory,"phase1",0,(MAP,VEHICLE),weights,scene)
    item["evaluator"]="1haolou"
    result=execute(item,240)
    if result["returncode"]==0:
        try:
            from validate_roadside import evaluate
            # Native collision report and strict no-empty/no-gap continuity.
            result["native"]=evaluate(directory,scene)
            rows=list(csv.DictReader((directory/"policy.csv").open()))
            result["quality"]=quality_metrics(rows)
            from driving_quality import analyze_trace
            result["web_driving_quality"]=analyze_trace(directory/"policy.csv")
            result["passed"] &= (result["native"]["passed"] and result["quality"]["final_speed_mps"]<.05
                                 and result["web_driving_quality"]["status"]=="PASS")
        except Exception as exc:
            result.update(passed=False,quality_error=str(exc))
    (directory/"result.json").write_text(json.dumps(result,indent=2)+"\n")
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scenes",type=Path,required=True)
    p.add_argument("--generate",action="store_true")
    p.add_argument("--weights",type=Path)
    p.add_argument("--out",type=Path)
    p.add_argument("--workers",type=int,default=3)
    p.add_argument("--only",action="append",help="Exact scenario stem; repeat for focused regression")
    a=p.parse_args()
    if a.workers<1 or a.workers>8:
        p.error("workers must be 1..8")
    if a.generate:
        generate(a.scenes)
    if a.weights is None:
        if not a.generate:p.error("weights required for evaluation")
        return
    if a.out is None:p.error("out required for evaluation")
    sources=sorted(a.scenes.glob("*.worldsim.scenario.json"))
    if a.only:
        sources=[s for s in sources if s.name.removesuffix(".worldsim.scenario.json") in a.only]
        if len(sources)!=len(set(a.only)):p.error("Unknown --only scenario")
    if not sources:p.error("No scenario files")
    a.out.mkdir(parents=True,exist_ok=False)
    artifacts={"weights":a.weights.resolve(),"component":Path("/opt/apollo/neo/lib/modules/simulation/ml_planning/libml_planning.so"),
               "simulator":Path("/opt/apollo/neo/bin/simulator_main"),"map":MAP/"base_map.txt","vehicle":VEHICLE}
    artifacts.update({name:HERE/name for name in ("planner.h","component.cc","phase1_native.py","evaluate_1haolou.py","validate_roadside.py")})
    (a.out/"provenance.json").write_text(json.dumps({
        "scope":"perception-only / perfect_planning / nonreactive actors",
        "artifacts":{name:dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for name,path in artifacts.items()},
        "scenarios":{s.name:hashlib.sha256(s.read_bytes()).hexdigest() for s in sources}},indent=2)+"\n")
    specs=[list(map(str,[s.resolve(),a.out.resolve(),a.weights.resolve()])) for s in sources]
    results=[]
    with ProcessPoolExecutor(max_workers=a.workers,mp_context=multiprocessing.get_context("spawn")) as pool:
        for r in pool.map(run,specs):
            results.append(r)
            print(json.dumps(dict(scene=Path(r["directory"]).name,passed=r["passed"],
                                  error=r.get("evaluation_error",r.get("quality_error")),quality=r.get("quality"))),flush=True)
    (a.out/"summary.json").write_text(json.dumps(results,indent=2)+"\n")
    return 0 if all(r["passed"] for r in results) else 1


if __name__=="__main__":
    raise SystemExit(main())
