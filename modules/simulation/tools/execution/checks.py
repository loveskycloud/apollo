"""Acceptance of actual TaskService outputs; never turn failed analysis into PASS."""
import json
from pathlib import Path
import subprocess


def verify_result(service_module, job, binary_root, env, job_dir):
    if job['stage'] != 'completed' or job.get('execution', {}).get('status') != 'PASS':
        raise RuntimeError('Native task failed: ' + str(job.get('error')))
    if not job.get('outputs') or not all(Path(p).is_file() and Path(p).stat().st_size for p in job['outputs']):
        raise RuntimeError('Simulation produced no nonempty output records')
    manifest = json.loads((job_dir / 'manifest.json').read_text())
    if Path(manifest['runtime_binary']) != binary_root / 'bin/simulator_main':
        raise RuntimeError('Task used another binary executable')
    if not all(Path(p).resolve().is_relative_to(binary_root) for p in manifest['runtime_libraries']):
        raise RuntimeError('Task mixed another binary library tree')
    analysis = job.get('analysis') or {}
    if not analysis.get('topic_message_counts') or analysis.get('missing_module_outputs'):
        raise RuntimeError('Analysis has missing module outputs')
    checks = {'execution': 'PASS', 'selected_binary': 'PASS', 'module_outputs': 'PASS'}
    if job['config']['repeat'] > 1:
        if analysis.get('determinism') != 'PASS':
            raise RuntimeError('Repeated simulation is not deterministic')
        checks['determinism'] = 'PASS'
    else:
        checks['determinism'] = 'NOT_TESTED'
    if job['config']['kind'] == 'world':
        for name in ('collision', 'planning_continuity', 'scenario_expectation', 'quality_metrics'):
            if analysis.get(name, {}).get('status') != 'PASS':
                raise RuntimeError('Simulation analysis failed: ' + name)
            checks[name] = 'PASS'
        if analysis.get('estop_frames'):
            raise RuntimeError('Planning produced estop frames')
        from modules.common_msgs.planning_msgs.planning_pb2 import ADCTrajectory
        from modules.common_msgs.control_msgs.control_cmd_pb2 import ControlCommand
        health = []
        for output in job['outputs']:
            first_plan, plans, controls, initial_wait = None, 0, 0, []
            for channel, stamp, payload in service_module.messages(output):
                if channel == '/apollo/planning':
                    message = ADCTrajectory.FromString(payload)
                    if message.header.status.error_code or not message.trajectory_point or message.decision.main_decision.HasField('not_ready'):
                        raise RuntimeError('Unhealthy Planning at ' + str(stamp))
                    first_plan = stamp if first_plan is None else first_plan
                    plans += 1
                elif channel == '/apollo/control':
                    message = ControlCommand.FromString(payload)
                    if message.header.status.error_code:
                        if (first_plan is None or stamp >= first_plan + 100000000 or
                            message.header.status.error_code != 1002 or
                            message.header.status.msg != 'planning has no trajectory point. planning_seq_num:0'):
                            raise RuntimeError('Unhealthy Control at ' + str(stamp) + ': ' + str(message.header.status))
                        initial_wait.append({'stamp_ns': stamp, 'status': str(message.header.status)})
                    else:
                        controls += 1
            if not plans or ('CONTROL' in job['config']['modules'] and not controls):
                raise RuntimeError('Missing healthy Planning / Control frames')
            health.append(dict(record=output, planning_ok=plans, control_ok=controls,
                               control_initial_wait=initial_wait, control_errors_after_initial_wait=0))
        (job_dir / 'algorithm-health.json').write_text(json.dumps(health, indent=2) + '\n')
        checks['algorithm_health'] = 'PASS'
    if 'ML_PLANNING' in job['config']['modules']:
        if analysis.get('driving_quality', {}).get('status') != 'PASS':
            raise RuntimeError('ML driving quality did not pass')
        command = ['/usr/bin/python3', str(binary_root / 'modules/simulation/ml_planning/verify_policy.py'),
                   '--task-dir', str(job_dir)]
        with (job_dir / 'policy-parity.log').open('w') as output:
            result = subprocess.run(command, env=env, stdout=output, stderr=subprocess.STDOUT)
        parity = json.loads((job_dir / 'policy-parity.json').read_text()) if (job_dir / 'policy-parity.json').is_file() else {}
        if result.returncode or not parity.get('passed'):
            raise RuntimeError('ML actor inference parity failed; see policy-parity.log')
        checks.update(ml_driving_quality='PASS', ml_inference_parity='PASS')
    return checks
