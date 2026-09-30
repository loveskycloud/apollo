"""Exercise the installed native component through standard Cyber inputs.

Uses one failed-scene record for real map/route/pose fixtures. Runs a synthetic
command sequence in LogSim, not a scenario acceptance or vehicle driving test.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

os.environ['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION'] = 'python'
sys.path.insert(0, '/opt/apollo/neo/python')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"logsim/tools"))
from bag_diff import messages
from cyber.proto.proto_desc_pb2 import ProtoDesc
from google.protobuf import text_format
from google.protobuf.descriptor_pb2 import FileDescriptorProto
from modules.common_msgs.planning_msgs.planning_command_pb2 import PlanningCommand
from modules.common_msgs.planning_msgs.planning_pb2 import ADCTrajectory
from modules.common_msgs.planning_msgs.pad_msg_pb2 import PadMessage
from modules.common_msgs.localization_msgs.localization_pb2 import LocalizationEstimate
from modules.common_msgs.perception_msgs.perception_obstacle_pb2 import PerceptionObstacles
from modules.common_msgs.routing_msgs.routing_pb2 import RoutingResponse
from modules.common_msgs.external_command_msgs.command_status_pb2 import CommandStatus, ERROR, RUNNING, FINISHED
from modules.external_command.proto.temporary_stop_command_pb2 import TemporaryStopCommand
from modules.external_command.proto.reference_line_offset_command_pb2 import ReferenceLineOffsetCommand
from modules.simulation.logsim.proto.simulation_task_pb2 import SimulationTask
from run_parallel import prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture-job', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run = args.out.resolve()
    fixture = args.fixture_job.resolve()
    route = pose = None
    for channel, _, payload in messages(fixture/'run-1/simulation.record'):
        if channel == '/apollo/raw_routing_response' and route is None:
            route = RoutingResponse.FromString(payload)
        if channel == '/apollo/localization/pose' and pose is None:
            pose = LocalizationEstimate.FromString(payload)
        if route is not None and pose is not None:
            break
    assert route is not None and pose is not None, 'Fixture must contain route and localization'
    prepare(run, 'mixed', 0, (fixture/'map', fixture/'vehicle/vehicle_param.pb.txt'),
            Path(__file__).resolve().parent/'models/v4/unified.weights', {'duration': 2})
    command = PlanningCommand(command_id=11, is_motion_command=True, target_speed=.2)
    command.header.timestamp_sec = 100
    command.header.sequence_num = 11
    command.lane_follow_command.CopyFrom(route)

    def motion(number):
        result = PlanningCommand()
        result.CopyFrom(command)
        result.command_id = number
        result.header.sequence_num = number
        return result

    def pack(target, message):
        target.type_url = 'type.googleapis.com/'+message.DESCRIPTOR.full_name
        target.value = message.SerializeToString()

    def temporary(mode):
        result = PlanningCommand(command_id=100+mode)
        pack(result.custom_command, TemporaryStopCommand(command_id=100+mode, mode=mode))
        return result

    invalid = motion(13)
    invalid.target_speed = float('nan')
    parking = motion(15)
    parking.parking_command.parking_spot_id = 'unsupported'
    offset = PlanningCommand(command_id=17)
    pack(offset.custom_command, ReferenceLineOffsetCommand(command_id=17, mode=ReferenceLineOffsetCommand.RIGHT_OFFSET))
    near_goal = motion(21)
    req = near_goal.lane_follow_command.routing_request
    first = type(req.waypoint[0])()
    first.CopyFrom(req.waypoint[0])
    del req.waypoint[:]
    req.waypoint.add().CopyFrom(first)
    req.waypoint.add().CopyFrom(first)
    req.waypoint[-1].s += .2
    bad_route = motion(22)
    bad_route.lane_follow_command.routing_request.waypoint[-1].s = float('nan')
    zero_speed = motion(25)
    zero_speed.target_speed = 0
    events = [
        ('lane_follow', command, 'moving', 11),
        ('retry', command, 'moving', 11),
        ('stop', PadMessage(action=PadMessage.STOP), 'stopped', 11),
        ('resume', PadMessage(action=PadMessage.RESUME_CRUISE), 'moving', 11),
        ('temporary_stop', temporary(TemporaryStopCommand.STOP), 'stopped', 11),
        ('resume_preserves_temporary_stop', PadMessage(action=PadMessage.RESUME_CRUISE), 'stopped', 11),
        ('cancel_temporary_stop', temporary(TemporaryStopCommand.CANCEL), 'moving', 11),
        ('clear', PadMessage(action=PadMessage.CLEAR_PLANNING), 'cleared', 11),
        ('resume_cannot_revive_clear', PadMessage(action=PadMessage.RESUME_CRUISE), 'cleared', 11),
        ('retry_cannot_revive_clear', command, 'cleared', 11),
        ('new_task_after_clear', motion(12), 'moving', 12),
        ('invalid_speed', invalid, 'error', 13),
        ('valid_after_error', motion(14), 'moving', 14),
        ('unsupported_parking', parking, 'error', 15),
        ('valid_after_parking', motion(16), 'moving', 16),
        ('unsupported_offset', offset, 'error', 17),
        ('valid_after_offset', motion(18), 'moving', 18),
        ('empty_command', PlanningCommand(command_id=19), 'error', 19),
        ('valid_after_empty', motion(20), 'moving', 20),
        ('invalid_route_endpoint', bad_route, 'error', 22),
        ('valid_after_route_error', motion(23), 'moving', 23),
        ('unsupported_pad', PadMessage(action=PadMessage.CHANGE_LEFT), 'error', 23),
        ('valid_after_pad_error', motion(24), 'moving', 24),
        ('zero_target_speed', zero_speed, 'stopped', 25),
        ('resume_with_new_speed', motion(26), 'moving', 26),
        ('finish_at_destination', near_goal, 'finished', 21),
        ('retry_finished', near_goal, 'finished_hold', 21),
    ]
    path = run/'commands.record'
    fixture_dir = run/'messages'
    fixture_dir.mkdir()
    manifest = []
    schemas = {}
    types = {'/apollo/planning/command': PlanningCommand, '/apollo/planning/pad': PadMessage,
             '/apollo/localization/pose': LocalizationEstimate, '/apollo/perception/obstacles': PerceptionObstacles}
    for channel, cls in types.items():
        def describe(file):
            tree = ProtoDesc()
            descriptor = FileDescriptorProto()
            file.CopyToProto(descriptor)
            tree.desc = descriptor.SerializeToString()
            for dependency in file.dependencies:
                tree.dependencies.add().CopyFrom(describe(dependency))
            return tree
        schema = fixture_dir/(cls.__name__+'.schema')
        schema.write_bytes(describe(cls.DESCRIPTOR.file).SerializeToString())
        schemas[channel] = (cls.DESCRIPTOR.full_name, schema)

    def write(channel, payload, timestamp):
        target = fixture_dir/(str(len(manifest))+'.pb')
        target.write_bytes(payload)
        name, schema = schemas[channel]
        manifest.append(' '.join([str(timestamp)]+[json.dumps(str(v)) for v in (channel,name,schema,target)]))
    for i, (_, event, _, _) in enumerate(events):
        ns = 100_000_000_000+i*100_000_000
        # Ordering and separate timestamps ensure the command precedes perception.
        channel = '/apollo/planning/pad' if isinstance(event, PadMessage) else '/apollo/planning/command'
        write(channel, event.SerializeToString(), ns)
        pose.header.timestamp_sec = (ns+1_000_000)/1e9
        pose.pose.linear_velocity.x = pose.pose.linear_velocity.y = 0
        write('/apollo/localization/pose', pose.SerializeToString(), ns+1_000_000)
        perception = PerceptionObstacles()
        perception.header.timestamp_sec = (ns+2_000_000)/1e9
        write('/apollo/perception/obstacles', perception.SerializeToString(), ns+2_000_000)
    (run/'messages.txt').write_text('\n'.join(manifest)+'\n')
    subprocess.run(['/opt/apollo/neo/bin/command_fixture_writer', str(run/'messages.txt'), str(path)], check=True)
    task = SimulationTask()
    text_format.Parse((run/'task.pb.txt').read_text(), task)
    task.input_kind = SimulationTask.BAG
    task.ClearField('world_scenario_path')
    task.record_paths.append(str(path))
    del task.runtime_modules[:]
    task.runtime_modules.append('PLANNING')
    del task.dag_paths[:]
    task.dag_paths.append(str(run/'planning.dag'))
    task.channel_policy.Clear()
    task.channel_policy.inject_channels.extend(types)
    outputs = ['/apollo/planning', '/apollo/planning/command_status', '/apollo/planning/reference_line_offset_command_status']
    task.channel_policy.suppress_channels.extend(outputs)
    task.channel_policy.record_channels.extend(list(types)+outputs)
    (run/'task.pb.txt').write_text(text_format.MessageToString(task))
    env = os.environ.copy()
    env.update(SIM_PARENT_PID=str(os.getpid()), GLOG_log_dir=str(run/'log'), OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    with (run/'runtime.log').open('w') as log:
        completed = subprocess.run(['/opt/apollo/neo/bin/simulator_main', '--task_dir='+str(run)],
                                   cwd=run, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=60)
    assert completed.returncode == 0, f'Process exited with {completed.returncode}: {run}/runtime.log'
    plans, statuses, offset_statuses = {}, {}, []
    for channel, _, payload in messages(run/'simulation.record'):
        if channel == '/apollo/planning':
            value = ADCTrajectory.FromString(payload)
            plans[round((value.header.timestamp_sec-100)*10)] = value
        elif channel == '/apollo/planning/command_status':
            value = CommandStatus.FromString(payload)
            statuses[round((value.header.timestamp_sec-100)*10)] = value
        elif channel == '/apollo/planning/reference_line_offset_command_status':
            offset_statuses.append(CommandStatus.FromString(payload))
    results = []
    for i, (name, _, expectation, command_id) in enumerate(events):
        assert i in plans and i in statuses, (name, list(plans), list(statuses))
        plan, status = plans[i], statuses[i]
        assert status.command_id == command_id, (name, status)
        if expectation == 'error':
            assert status.status == ERROR and plan.estop.is_estop, (name, status, plan)
        else:
            assert len(plan.trajectory_point) == 81 and not plan.estop.is_estop, (name, plan)
            speeds = [p.v for p in plan.trajectory_point]
            assert max(speeds) <= .2+1e-9, (name, max(speeds))
            if expectation in ('stopped', 'cleared', 'finished_hold'):
                assert max(speeds) == 0, (name, max(speeds))
            if expectation == 'moving':
                assert max(speeds) > .01, (name, max(speeds))
            expected_status = ERROR if expectation=='cleared' else FINISHED if expectation.startswith('finished') else RUNNING
            assert status.status == expected_status, (name, status)
            assert plan.routing_header.sequence_num == command_id, (name, plan.routing_header)
        results.append({'case': name, 'status': 'PASS'})
    assert any(s.command_id==17 and s.status==ERROR for s in offset_statuses)
    (run/'verification.json').write_text(json.dumps(results, indent=2))
    print(f'PASS: {len(results)} command lifecycle checks; errors recovered in the same process; no raw Routing input')


if __name__ == '__main__':
    main()
