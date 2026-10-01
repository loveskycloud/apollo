"""Independent evidence that persistent unrelated motion does not stall parking."""
import math


def evaluate(snapshots, scene, contract):
    declared={a['id']:i+1 for i,a in enumerate(scene['agents'])}
    backgrounds=contract['background_actor_ids'];observed=[]
    for name in backgrounds:
        oid=declared[name]
        first=next((i for i,(_,obs) in enumerate(snapshots) if any(o[0]==oid for o in obs)),None)
        tail=snapshots[first:] if first is not None else []
        present=[next((o for o in obs if o[0]==oid),None) for _,obs in tail]
        moving=sum(o is not None and math.hypot(o[6],o[7])>.01 for o in present)
        fraction=moving/len(tail) if tail else 0.
        observed.append({'actor_id':name,'continuously_visible_to_end':bool(tail) and all(o is not None for o in present),
                         'moving_fraction':fraction,'moving_at_end':bool(present) and present[-1] is not None and math.hypot(present[-1][6],present[-1][7])>.01})
    ids={declared[name] for name in backgrounds};concurrent=0.
    for (a,_),(b,obs) in zip(snapshots,snapshots[1:]):
        if abs(a[4])>.02 and abs(b[4])>.02 and ids<={o[0] for o in obs if math.hypot(o[6],o[7])>.01}:
            concurrent+=min(.11,b[0]-a[0])
    duration=snapshots[-1][0][0]-snapshots[0][0][0] if snapshots else None
    extra=duration-contract['baseline_duration_s'] if duration is not None else None
    limit=contract['maximum_extra_duration_s']
    efficient=extra is not None and (limit is None or extra<=limit+1e-6)
    crossing=None
    if contract.get('crossing_actor_id'):
        from parking_trigger_metrics import evaluate as trigger_evaluate
        crossing=trigger_evaluate(snapshots,scene,{'actor_ids':[contract['crossing_actor_id']],
            'minimum_activation_displacement_m':.15,'minimum_activation_speed_mps':.05})
    background_ok=bool(observed) and all(a['continuously_visible_to_end'] and a['moving_at_end'] and
        a['moving_fraction']>=contract['minimum_moving_fraction'] for a in observed)
    crossing_ok=crossing is None or (crossing['status']=='PASS' and crossing['yield_response'].get('stopped_after_activation') is True)
    passed=background_ok and concurrent>=contract['minimum_concurrent_motion_s'] and efficient and crossing_ok
    return {'version':'parking-interaction-v1','status':'PASS' if passed else 'FAIL','actors':observed,
            'background_motion_verified':background_ok,'ego_and_background_moving_s':concurrent,
            'duration_s':duration,'baseline_duration_s':contract['baseline_duration_s'],
            'extra_duration_s':extra,'efficiency_pass':efficient,'crossing':crossing,
            'scope':'Persistent observed motion and completion-time comparison; parking outcome and collision gates remain independent'}
