"""Suite-level parking scorecard with explicit case denominators and missing data."""
import json
from pathlib import Path
import numpy as np


def scorecard(jobs):
    cases=[];search=[];steady=[];durations=[];changes=[];lateral=[];heading=[]
    path_ratios=[];jerk=[];gaps=[];boundary=[];first_motion=[]
    yielding={k:[] for k in ('time_to_stop_s','braking_distance_m','peak_braking_deceleration_mps2','resume_delay_after_clear_s','resume_delay_after_ready_s')}
    extra_duration=[];concurrent_motion=[]
    for job in jobs:
        analysis=job.get('analysis') or {};runs=[m.get('parking') for m in analysis.get('quality_metrics',{}).get('runs',[])]
        runs=[m for m in runs if m]
        source=job.get('scene') or job.get('config',{}).get('source','')
        if runs:meta=runs[0]
        else:
            meta=job.get('parking_contract') or job.get('config',{}).get('evaluation',{}).get('parking')
            if not meta:
                p=Path(source).with_name(Path(source).name.replace('.worldsim.scenario.json','.evaluation.json')) if source else None
                if not p or not p.is_file():continue
                meta=json.loads(p.read_text()).get('parking')
            if not meta:continue
        operation=meta.get('operation','in');variant=meta.get('variant','base');status=job.get('status',job.get('stage'))
        expected_runs=job.get('repeat',job.get('config',{}).get('repeat',1))
        done=status=='completed' and len(runs)==expected_runs and bool(runs) and all(r.get('status')=='PASS' for r in runs)
        complete=status in ('completed','failed','cancelled','interrupted')
        road=[r.get('road',{}).get('status') for r in analysis.get('quality_metrics',{}).get('runs',[])]
        dynamic=[r['parking_dynamic'] for r in analysis.get('quality_metrics',{}).get('runs',[]) if 'parking_dynamic' in r]
        if not dynamic and analysis.get('parking_dynamic'):dynamic=[analysis['parking_dynamic']]
        interactions=[r['parking_interaction'] for r in analysis.get('quality_metrics',{}).get('runs',[]) if 'parking_interaction' in r]
        for r in interactions:
            if r.get('extra_duration_s') is not None:extra_duration.append(r['extra_duration_s'])
            concurrent_motion.append(r['ego_and_background_moving_s'])
        for r in dynamic:
            for key,values in yielding.items():
                value=r.get('yield_response',{}).get(key)
                if value is not None:values.append(value)
        success=lambda key:done and bool(runs) and all(r.get(key) is True for r in runs)
        cases.append({'id':job['id'],'scene':source,'operation':operation,'variant':variant,'style':meta.get('style'),
                      'width_m':meta.get('width_m'),'aisle_width_m':meta.get('aisle_width_m'),'entry':meta.get('expected_entry',meta.get('entry')),
                      'status':status,'complete':complete,'mission_success':done,
                      'expected_runs':expected_runs,'measured_runs':len(runs),
                      'interaction_case':variant.startswith('trigger_relevance_'),
                      'interaction_pass':len(interactions)==expected_runs and all(r.get('status')=='PASS' for r in interactions),
                      'background_motion_verified':len(interactions)==expected_runs and all(r.get('background_motion_verified') is True for r in interactions),
                      'continued_with_retained':len(dynamic)==expected_runs and all(r.get('yield_response',{}).get('continued_with_stationary_actors_visible') is True for r in dynamic),
                      'dynamic_case':variant.startswith('trigger_') and not variant.startswith('trigger_relevance_'),
                      'dynamic_evaluated':bool(dynamic) and all(r.get('status') in ('PASS','FAIL') for r in dynamic),
                      'dynamic_activation':len(dynamic)==expected_runs and all(r.get('activation_during_maneuver') is True for r in dynamic),
                      'dynamic_cleared':len(dynamic)==expected_runs and all(r.get('all_actors_cleared') is True for r in dynamic),
                      'persistent_retained':len(dynamic)==expected_runs and all(r.get('persistent_actors_retained') is True for r in dynamic),
                      'resumed_with_retained':len(dynamic)==expected_runs and all(r.get('yield_response',{}).get('resumed_with_stationary_actors_visible') is True for r in dynamic),
                      'yield_then_resume':len(dynamic)==expected_runs and all(r.get('yield_response',{}).get('stopped_after_activation') is True and
                          r.get('yield_response',{}).get('resume_delay_after_ready_s',r.get('yield_response',{}).get('resume_delay_after_clear_s')) is not None for r in dynamic),
                      'parking_eligible':operation in ('in','in_out') and variant!='occupied',
                      'exit_eligible':operation in ('out','in_out'),
                      'parking_success':success('parking_success'),'straight':success('straight_parking'),
                      'exit_success':success('exit_success'),'safe_rejection':success('safe_rejection'),
                      'collision_status':analysis.get('collision',{}).get('status','NOT_EVALUATED'),
                      'collision_count':analysis.get('collision',{}).get('collision_count'),
                      'road_status':'FAIL' if 'FAIL' in road else 'PASS' if road and all(v=='PASS' for v in road) else 'NOT_EVALUATED',
                      'error':job.get('error')})
        for r in analysis.get('quality_metrics',{}).get('runs',[]):
            for dest,value in [(jerk,r.get('motion',{}).get('jerk_mps3',{}).get('max')),
                               (gaps,r.get('interaction',{}).get('minimum_body_gap_m')),
                               (boundary,r.get('road',{}).get('minimum_boundary_distance_m'))]:
                if value is not None:dest.append(value)
        for m in runs:
            if m.get('variant')=='occupied':continue
            # Per-run summaries are retained; aggregate distributions below
            # explicitly summarize per-run maxima / case measures, not pooled frames.
            for target,key in [(search,'search_ms'),(steady,'steady_planning_ms')]:
                value=m.get(key,{}).get('max')
                if value is not None:target.append(value)
            durations.append(m['maneuver_duration_s']);changes.append(m['gear_changes'])
            if m.get('distance_over_witness') is not None:path_ratios.append(m['distance_over_witness'])
            if m.get('time_to_first_motion_s') is not None:first_motion.append(m['time_to_first_motion_s'])
            if operation!='out':lateral.append(abs(m['lateral_error_m']));heading.append(m['heading_error_deg'])
    def rates(items):
        def rate(predicate,key):
            eligible=[c for c in items if predicate(c)];n=len(eligible);passed=sum(c[key] for c in eligible)
            return {'passed':passed,'total':n,'rate':passed/n if n else None}
        return {'mission_success':rate(lambda c:c['variant']!='occupied','mission_success'),
                'parking_success':rate(lambda c:c['parking_eligible'],'parking_success'),
                'straight_parking':rate(lambda c:c['parking_eligible'],'straight'),
                'straight_among_successful_parking':rate(lambda c:c['parking_eligible'] and c['parking_success'],'straight'),
                'exit_success':rate(lambda c:c['exit_eligible'],'exit_success'),
                'safe_rejection':rate(lambda c:c['variant']=='occupied','safe_rejection')}
    stats=lambda a:{'count':len(a),'min':min(a) if a else None,'p50':float(np.percentile(a,50)) if a else None,'p95':float(np.percentile(a,95)) if a else None,'max':max(a) if a else None}
    return {'version':'parking-v2','status':'COMPLETE' if cases and all(c['complete'] for c in cases) else 'IN_PROGRESS',
            'case_count':len(cases),'measured_run_count':len(durations),'rates':rates(cases),
            'unrelated_motion':{'cases':sum(c['interaction_case'] for c in cases),
                                'evaluated_pass':sum(c['interaction_case'] and c['interaction_pass'] for c in cases),
                                'background_motion_verified':sum(c['interaction_case'] and c['background_motion_verified'] for c in cases),
                                'mission_success':sum(c['interaction_case'] and c['mission_success'] for c in cases)},
            'dynamic_traffic':{'cases':sum(c['dynamic_case'] for c in cases),
                               'evaluated':sum(c['dynamic_case'] and c['dynamic_evaluated'] for c in cases),
                               'activated_during_maneuver':sum(c['dynamic_case'] and c['dynamic_activation'] for c in cases),
                               'all_actors_cleared':sum(c['dynamic_case'] and c['dynamic_cleared'] for c in cases),
                               'continued_with_stationary_actors_visible':sum(c['dynamic_case'] and c['continued_with_retained'] for c in cases),
                               'persistent_actors_retained':sum(c['dynamic_case'] and c['persistent_retained'] for c in cases),
                               'resumed_with_stationary_actors_visible':sum(c['dynamic_case'] and c['resumed_with_retained'] for c in cases),
                               'yield_then_resume':sum(c['dynamic_case'] and c['yield_then_resume'] for c in cases),
                               'mission_success':sum(c['dynamic_case'] and c['mission_success'] for c in cases)},
            'by_operation':{v:rates([c for c in cases if c['operation']==v]) for v in sorted({c['operation'] for c in cases})},
            'by_style':{v:rates([c for c in cases if c['style']==v]) for v in sorted({c['style'] for c in cases if c['style']})},
            'by_variant':{v:rates([c for c in cases if c['variant']==v]) for v in sorted({c['variant'] for c in cases})},
            'by_width_m':{str(v):rates([c for c in cases if c['width_m']==v]) for v in sorted({c['width_m'] for c in cases if c['width_m'] is not None})},
            'by_aisle_width_m':{str(v):rates([c for c in cases if c['aisle_width_m']==v]) for v in sorted({c['aisle_width_m'] for c in cases if c['aisle_width_m'] is not None})},
            'by_entry':{v:rates([c for c in cases if c['entry']==v]) for v in sorted({c['entry'] for c in cases if c['entry']})},
            'safety':{'collision_evaluated_cases':sum(c['collision_status']!='NOT_EVALUATED' for c in cases),
                      'collision_cases':sum(c['collision_status']=='FAIL' for c in cases),
                      'collision_count':sum(c['collision_count'] or 0 for c in cases),
                      'road_evaluated_cases':sum(c['road_status']!='NOT_EVALUATED' for c in cases),
                      'road_violation_cases':sum(c['road_status']=='FAIL' for c in cases)},
            'distributions':{'extra_duration_vs_baseline_s':stats(extra_duration),'ego_and_background_moving_s':stats(concurrent_motion),'per_run_max_search_ms':stats(search),'per_run_max_steady_planning_ms':stats(steady),
                             **{k:stats(v) for k,v in yielding.items()},
                             'maneuver_duration_s':stats(durations),'gear_changes':stats(changes),'abs_lateral_error_m':stats(lateral),'heading_error_deg':stats(heading),
                             'distance_over_witness':stats(path_ratios),'time_to_first_motion_s':stats(first_motion),
                             'per_run_max_jerk_mps3':stats(jerk),'minimum_obstacle_gap_m':stats(gaps),'minimum_boundary_distance_m':stats(boundary)},
            'denominator':'Case-level: all requested repeats must pass; failed/cancelled/missing-analysis cases remain failures. Occupied-space safe rejection is separate. Pending cases are provisional.',
            'cases':cases}
