#!/usr/bin/env python3
"""Re-run an existing task's configuration with its current source profile."""
import argparse
import json
from pathlib import Path
import time

from task_service import TaskService, TERMINAL

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--jobs', required=True)
parser.add_argument('--job-id', required=True)
parser.add_argument('--state-dir', required=True)
parser.add_argument('--source', help='Explicit alternative validation scenario; original task is unchanged')
parser.add_argument('--map', help='Explicit matching validation map')
args = parser.parse_args()
template = json.loads(Path(args.jobs).read_text())[args.job_id]
for name in ('source', 'map'):
    if getattr(args, name): template['config'][name] = getattr(args, name)
service = TaskService(args.state_dir)
try:
    job_id = service.request({'action': 'enqueue', 'config': template['config']})['id']
    last = None
    while True:
        job = next(j for j in service.request({'action': 'list'})['jobs'] if j['id'] == job_id)
        state = (job['stage'], job.get('run'))
        if state != last:
            print(job_id, *state, flush=True)
            last = state
        if job['stage'] in TERMINAL:
            print(json.dumps(job, indent=2), flush=True)
            assert job['stage'] == 'completed', job.get('error')
            break
        time.sleep(.5)
finally:
    service.close()
