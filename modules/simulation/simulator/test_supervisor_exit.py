#!/usr/bin/env python3
"""Real subprocess test: a killed queue supervisor must not orphan a simulator."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from task_service import ROOT, TaskService

parser = argparse.ArgumentParser()
parser.add_argument("--state-dir", required=True)
args = parser.parse_args()
root = Path(args.state_dir).resolve()
worker = subprocess.Popen([sys.executable, str(ROOT / "modules/simulation/simulator/task_service.py"),
                           "--state-dir", str(root)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
child_pid = None
try:
    config = {"kind":"bag", "source":str(ROOT / "modules/simulation/web_monitor/test-artifacts/simulation-20260912/input.record"),
              "map":str(ROOT / "data/bag/data_with_map/extracted/od_hq_map"),
              "vehicle":str(ROOT / "data/bag/data_with_map/extracted/Jiyu_01/modules/common/data/vehicle_param.pb.txt"),
              "modules":["PREDICTION","PLANNING","CONTROL"], "repeat":2}
    worker.stdin.write(json.dumps({"action":"enqueue", "config":config}) + "\n")
    worker.stdin.flush()
    reply = json.loads(worker.stdout.readline())
    assert reply["status"] == "ok", reply
    for _ in range(200):
        job = json.loads((root / "jobs.json").read_text())[reply["id"]]
        if job.get("process_id"):
            child_pid = job["process_id"]
            time.sleep(.5)
            break
        time.sleep(.05)
    assert child_pid is not None, job
    worker.kill()  # Deliberate crash, not a graceful cancellation.
    worker.wait(timeout=10)
    for _ in range(100):
        stat = Path(f"/proc/{child_pid}/stat")
        if not stat.exists() or stat.read_text().split()[2] == "Z": break
        time.sleep(.05)
    else: raise AssertionError(f"Simulator {child_pid} survived its parent")
    restarted = TaskService(root)
    try:
        assert restarted.jobs[reply["id"]]["stage"] == "interrupted"
        assert json.loads((root / "jobs.json").read_text())[reply["id"]]["stage"] == "interrupted"
    finally:
        restarted.close()
    print("PASS: supervisor crash terminates simulator; restart persists interrupted state")
finally:
    if worker.poll() is None:
        worker.terminate()
        worker.wait(timeout=10)
    worker.stdin.close()
    worker.stdout.close()
