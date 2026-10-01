#!/usr/bin/env python3
"""Read-only regression for the reported task; outputs evidence, never a new run."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from task_service import ROOT
from bag_diff import compare, difference_page, messages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', default='d3ade1846813436c')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    directory = ROOT / 'data/simulation/jobs' / args.job
    records = [next((directory/f'run-{i}').glob('simulation.record.*')) for i in (1, 2)]
    original = (directory/'analysis.json').read_bytes()
    saved = json.loads(original)['comparisons'][0]
    result = compare(*records, algorithm=True)
    for key in ('result', 'different_messages', 'raw_different_messages', 'left_sha256', 'right_sha256'):
        assert result[key] == saved[key], (key, result[key], saved[key])
    first = difference_page(*records, include_messages=True)
    last = difference_page(*records, offset=result['different_messages']-1)
    assert first['diffs'][0]['stream_index'] == saved['diffs'][0]['index'] == 863
    assert not last['has_more'] and last['diffs'][0]['stream_index'] == 19808
    (args.out/'first-difference.json').write_text(json.dumps(first['diffs'], indent=2))
    (args.out/'last-difference.json').write_text(json.dumps(last['diffs'], indent=2))
    (args.out/'comparison.json').write_text(json.dumps(result, indent=2))
    sys.path.insert(0, '/opt/apollo/neo/python')
    from google.protobuf.json_format import MessageToDict
    from modules.common_msgs.planning_msgs.planning_pb2 import ADCTrajectory
    from modules.common_msgs.localization_msgs.localization_pb2 import LocalizationEstimate
    evidence = []
    for run, record in enumerate(records, 1):
        plans, poses = {}, {}
        stream = messages(record)
        try:
            for topic, stamp, payload in stream:
                if stamp > 9710000000:
                    break
                if topic == '/apollo/planning' and stamp in (9600000000, 9700000000):
                    m = ADCTrajectory.FromString(payload)
                    plans[stamp] = {
                        'publish_time': str(stamp),
                        'input_pose_z': m.debug.planning_data.adc_position.pose.position.z,
                        'init_point_z': m.debug.planning_data.init_point.path_point.z,
                        'first_trajectory_point': MessageToDict(m.trajectory_point[0], preserving_proto_field_name=True),
                        'first_has_z': m.trajectory_point[0].path_point.HasField('z'),
                        'paths': [{'name': path.name, 'first_point': MessageToDict(path.path_point[0], preserving_proto_field_name=True)}
                                  for path in m.debug.planning_data.path if path.path_point]}
                if topic == '/apollo/localization/pose' and stamp in (9700000000, 9710000000):
                    m = LocalizationEstimate.FromString(payload)
                    poses[stamp] = m.pose.position.z
        finally:
            stream.close()
        assert poses == {9700000000: 0.4, 9710000000: 0.0}
        assert plans[9700000000]['input_pose_z'] == plans[9700000000]['init_point_z'] == 0.4
        assert not plans[9600000000]['first_has_z']
        assert plans[9700000000]['first_has_z']
        assert plans[9700000000]['first_trajectory_point']['path_point']['z'] == 0
        evidence.append({'run': run, 'planning': plans, 'pose_z': poses})
    (args.out/'z-evidence.json').write_text(json.dumps(evidence, indent=2))
    assert (directory/'analysis.json').read_bytes() == original
    print(json.dumps({'result': 'PASS', 'different_messages': result['different_messages'],
                      'raw_hashes_match_original': True, 'analysis_sha256': hashlib.sha256(original).hexdigest(),
                      'first_difference': first['diffs'][0]['stream_index'],
                      'first_field_count': len(first['diffs'][0]['field_changes']),
                      'last_difference': last['diffs'][0]['stream_index'],
                      'z_transition': {'publish_time': '9710000000', 'before': 0.4, 'after': 0.0}}))


if __name__ == '__main__':
    main()
