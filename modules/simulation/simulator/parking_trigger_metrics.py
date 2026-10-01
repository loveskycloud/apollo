"""Evidence for motion-triggered parking traffic, including aborted recordings."""
import math


def evaluate(snapshots, scene, contract):
    expected = contract['actor_ids']
    declared = {a['id']: (index+1, a) for index, a in enumerate(scene['agents'])}
    ego = scene['ego']['position']
    observed = []
    dispositions = contract.get('actor_end_states', {})
    ready_times = []
    for name in expected:
        actor_id, actor = declared[name]
        sightings = [(p, o) for p, obstacles in snapshots for o in obstacles if o[0] == actor_id]
        moving = [(p, o) for p, o in sightings if math.hypot(o[6], o[7]) > .01]
        first = moving[0] if moving else None
        cleared = False
        if sightings:
            last_pose, last_actor = sightings[-1]
            end = actor['routes'][0]['waypoints'][-1]['position']
            cleanup=[t.get('radius',.03) for t in scene.get('triggers',[]) if
                     t.get('targetAgentId')==name and t.get('center')==end and
                     any(a.get('kind')=='ACTION_DISABLE' and a.get('targetAgentId')==name for a in t.get('actions',[]))]
            # The final visible frame precedes the cleanup by up to 100 ms.
            tolerance=max(cleanup,default=.03)+.11*math.hypot(last_actor[6],last_actor[7])+.02
            near_exit = math.hypot(last_actor[1]-end['x'], last_actor[2]-end['y']) <= tolerance
            cleared = near_exit and any(p[0] > last_pose[0] and all(o[0] != actor_id for o in obs)
                                       for p, obs in snapshots)
        state = dispositions.get(name, {'kind': 'cleared'})
        retained = False
        stationary_since = None
        if state['kind'] == 'stopped_visible' and snapshots:
            # Require an uninterrupted stationary suffix through the final frame.
            # Missing perception, renewed motion, or a different stop point fails.
            suffix = []
            target = state['position']
            for pose, obstacles in reversed(snapshots):
                found = next((o for o in obstacles if o[0] == actor_id), None)
                if (found is None or math.hypot(found[6], found[7]) > .01 or
                        math.hypot(found[1]-target['x'], found[2]-target['y']) > state['tolerance_m']):
                    break
                suffix.append(pose)
            if suffix:
                stationary_since = suffix[-1][0]
                retained = suffix[0][0]-stationary_since >= state['minimum_stationary_s']-1e-6
        satisfied = cleared if state['kind'] == 'cleared' else retained
        if satisfied:
            ready_times.append(stationary_since if retained else next(p[0] for p, obs in snapshots
                if p[0] > sightings[-1][0][0] and all(o[0] != actor_id for o in obs)))
        observed.append({'actor_id': name, 'perception_id': actor_id,
                         'moving_observed': bool(moving), 'cleared_observed': cleared,
                         'expected_end_state': state['kind'], 'end_state_satisfied': satisfied,
                         'stopped_visible_at_end': retained,
                         'stationary_since_s': stationary_since-snapshots[0][0][0] if stationary_since is not None else None,
                         'first_moving_s': first[0][0]-snapshots[0][0][0] if first else None,
                         'ego_displacement_at_activation_m': math.hypot(first[0][1]-ego['x'], first[0][2]-ego['y']) if first else None,
                         'ego_speed_at_activation_mps': abs(first[0][4]) if first else None})
    initial = observed[0] if observed else {}
    during_motion = (initial.get('moving_observed', False) and
                    initial['ego_displacement_at_activation_m'] >= contract['minimum_activation_displacement_m'] and
                    initial['ego_speed_at_activation_mps'] >= contract['minimum_activation_speed_mps'])
    all_moved = bool(observed) and all(o['moving_observed'] for o in observed)
    all_cleared = bool(observed) and all(o['cleared_observed'] for o in observed)
    all_satisfied = bool(observed) and all(o['end_state_satisfied'] for o in observed)
    persistent = [o for o in observed if o['expected_end_state'] == 'stopped_visible']
    response={'status':'NOT_EVALUATED'}
    if during_motion:
        begin=snapshots[0][0][0]+initial['first_moving_s']
        poses=[p for p,_ in snapshots if p[0]>=begin-1e-6]
        stopped=next((i for i,p in enumerate(poses) if abs(p[4])<.005),None)
        response={'status':'MEASURED','stopped_after_activation':stopped is not None,
                  'time_to_stop_s':poses[stopped][0]-begin if stopped is not None else None,
                  'braking_distance_m':sum(math.hypot(b[1]-a[1],b[2]-a[2]) for a,b in zip(poses[:stopped],poses[1:stopped+1])) if stopped is not None else None,
                  'peak_braking_deceleration_mps2':max([max(0.,abs(a[4])-abs(b[4]))/(b[0]-a[0]) for a,b in zip(poses[:stopped],poses[1:stopped+1]) if b[0]>a[0]],default=0.) if stopped is not None else None,
                  'resume_delay_after_clear_s':None,'resume_delay_after_ready_s':None,
                  'resumed_with_stationary_actors_visible':False,'premature_resume':None}
        if all_satisfied and stopped is not None:
            ready_at = max(ready_times)
            resumed = next((p[0] for p in poses[stopped+1:] if abs(p[4]) > .02), None)
            response['premature_resume'] = any(abs(p[4]) > .02 for p in poses[stopped+1:] if p[0] < ready_at)
            response['resumed_before_actor_end_state'] = response['premature_resume']
            response['premature_resume_scope'] = 'Legacy name: before scripted actor end state, not proof of unsafe motion; use independent collision checks'
            response['resume_delay_after_ready_s'] = resumed-ready_at if resumed is not None else None
            if all_cleared:
                response['resume_delay_after_clear_s'] = response['resume_delay_after_ready_s']
            if persistent and resumed is not None and resumed >= ready_at:
                ids = {o['perception_id'] for o in persistent}
                response['resumed_with_stationary_actors_visible'] = any(p[0] == resumed and
                    ids <= {o[0] for o in obs if math.hypot(o[6], o[7]) <= .01} for p, obs in snapshots)
    # A path-irrelevant actor may never require a stop. Count actual motion
    # while its stationary, continuously-visible suffix is present as well.
    continued=False
    if persistent and all_satisfied:
        ready=max(ready_times)
        continued=any(p[0]>=ready and abs(p[4])>.02 for p,_ in snapshots)
    response['continued_with_stationary_actors_visible']=continued
    return {'version': 'parking-trigger-v2', 'status': 'PASS' if during_motion and all_moved and all_satisfied and (not persistent or continued) else 'FAIL',
            'activation_during_maneuver': during_motion, 'all_actors_moved': all_moved,
            'all_actors_cleared': all_cleared, 'all_expected_end_states_satisfied': all_satisfied,
            'persistent_actors_retained': bool(persistent) and all(o['stopped_visible_at_end'] for o in persistent), 'actors': observed,
            'yield_response':response,
            'scope': 'Trigger effects inferred from stable actor IDs in recorded perception at 100 ms; '+
                     'scenario activation is separate from parking success; aborted runs remain failures'}


def evaluate_record(record, scene, contract):
    from bag_diff import messages
    from modules.common_msgs.localization_msgs.localization_pb2 import LocalizationEstimate
    from modules.common_msgs.perception_msgs.perception_obstacle_pb2 import PerceptionObstacles
    snapshots = []; latest = None
    for channel, stamp, payload in messages(record):
        if channel == '/apollo/localization/pose':
            p = LocalizationEstimate.FromString(payload).pose
            latest = (stamp/1e9, p.position.x, p.position.y, p.heading,
                      math.hypot(p.linear_velocity.x, p.linear_velocity.y))
        elif channel == '/apollo/perception/obstacles' and latest is not None:
            obstacles = PerceptionObstacles.FromString(payload)
            snapshots.append((latest, [(o.id, o.position.x, o.position.y, o.theta, o.length, o.width,
                o.velocity.x, o.velocity.y) for o in obstacles.perception_obstacle]))
    return evaluate(snapshots, scene, contract)
