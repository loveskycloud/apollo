#!/usr/bin/env python3
"""Replay a world's GT inputs as a bag: the shared algorithm schedule must agree."""
import argparse
import json
from pathlib import Path
import time
from task_service import ROOT, TaskService, TERMINAL, messages
from bag_diff import algorithm_payload

parser = argparse.ArgumentParser()
parser.add_argument("--world-record", required=True)
parser.add_argument("--state-dir", required=True)
args = parser.parse_args()
service = TaskService(args.state_dir)
try:
    config = {"kind":"bag", "source":args.world_record,
              "map":str(ROOT / "data/bag/data_with_map/extracted/od_hq_map"),
              "vehicle":str(ROOT / "data/bag/data_with_map/extracted/Jiyu_01/modules/common/data/vehicle_param.pb.txt"),
              "modules":["PREDICTION","PLANNING","CONTROL","ROUTING"], "repeat":1}
    job_id = service.request({"action":"enqueue", "config":config})["id"]
    while True:
        job = next(j for j in service.request({"action":"list"})["jobs"] if j["id"] == job_id)
        if job["stage"] in TERMINAL: break
        time.sleep(.2)
    assert job["stage"] == "completed", job
    selected = {"/apollo/prediction", "/apollo/planning", "/apollo/control"}
    def stream(path):
        return [(c, t, algorithm_payload(c, b)) for c, t, b in messages(path) if c in selected]
    left, right = stream(args.world_record), stream(job["outputs"][0])
    difference = next((i for i, (a,b) in enumerate(zip(left,right)) if a != b), None)
    result = {"result":"PASS" if left == right else "FAIL", "world_count":len(left),
              "logsim_count":len(right), "first_difference":difference,
              "scope":"Exact PREDICTION/PLANNING/CONTROL message order, timestamps and algorithm values",
              "world_record":args.world_record, "logsim_record":job["outputs"][0]}
    (Path(args.state_dir) / "equivalence.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    assert left == right, result
finally:
    service.close()
