"""Validate a real queue job's early mission completion and final progress.

Run inside the Apollo container: python3 test_mission_completion.py --job <dir>
The input job must reach its single-route destination before its duration limit.
"""
import argparse
import json
import math
import os
from pathlib import Path
import sys


def verify_job(directory):
    os.environ['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION'] = 'python'
    sys.path.insert(0, '/opt/apollo/neo/python')
    from task_service import messages
    from modules.common_msgs.chassis_msgs.chassis_pb2 import Chassis
    from modules.common_msgs.localization_msgs.localization_pb2 import LocalizationEstimate
    from modules.common_msgs.planning_msgs.planning_pb2 import ADCTrajectory

    results = []
    for run in sorted(directory.glob('run-*')):
        scene = json.loads((run / 'scenario.timeout.json').read_text())
        assert len(scene['ego']['routes']) == 1, 'Use a single-route regression fixture'
        goal = scene['ego']['routes'][0]['waypoints'][-1]['position']
        first_stamp = final_stamp = mission_stamp = None
        pose = chassis = None
        plans = 0
        for channel, stamp, payload in messages(run / 'simulation.record'):
            first_stamp = stamp if first_stamp is None else min(first_stamp, stamp)
            final_stamp = stamp if final_stamp is None else max(final_stamp, stamp)
            if channel == '/apollo/localization/pose':
                pose = LocalizationEstimate.FromString(payload).pose
            elif channel == '/apollo/canbus/chassis':
                chassis = Chassis.FromString(payload)
            elif channel == '/apollo/planning':
                plan = ADCTrajectory.FromString(payload)
                plans += 1
                if plan.decision.main_decision.HasField('mission_complete'):
                    assert pose is not None
                    assert math.hypot(pose.position.x-goal['x'], pose.position.y-goal['y']) <= .4
                    assert math.hypot(pose.linear_velocity.x, pose.linear_velocity.y) < .05
                    assert not plan.estop.is_estop and plan.trajectory_point
                    mission_stamp = stamp if mission_stamp is None else mission_stamp
        assert mission_stamp is not None and plans > 1, 'No native mission_complete'
        assert chassis.parking_brake and chassis.speed_mps == 0, 'Must finish stopped and parked'
        elapsed = (final_stamp-first_stamp)/1e9
        assert elapsed < scene['duration']-1, (elapsed, scene['duration'])
        assert 0 <= final_stamp-mission_stamp <= 20_000_000, 'Continued ticking after arrival'
        progress = json.loads((run / 'progress.json').read_text())
        assert progress['percent'] == 100
        assert abs(progress['sim_time_s']-final_stamp/1e9) < 1e-6
        collision = json.loads((run / 'collision.json').read_text())
        assert collision['complete'] and collision['status'] == 'PASS'
        results.append({'run': run.name, 'duration_limit_s': scene['duration'],
                        'elapsed_s': elapsed, 'progress': progress['percent'], 'status': 'PASS'})
    assert results, 'No run outputs'
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify_job(args.job), indent=2))
