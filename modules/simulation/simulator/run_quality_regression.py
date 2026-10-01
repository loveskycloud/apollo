"""Run bounded CPU-only ML planning regression through the real task service."""
import argparse
import json
from pathlib import Path
import time
from task_service import TaskService, ROOT, TERMINAL


def main():
    p=argparse.ArgumentParser();p.add_argument('--state-dir',type=Path,required=True)
    p.add_argument('--source',type=Path);p.add_argument('--suite',type=Path)
    p.add_argument('--repeat',type=int,default=1);p.add_argument('--workers',type=int,default=2)
    a=p.parse_args()
    if bool(a.source)==bool(a.suite):p.error('Choose one source or suite')
    service=TaskService(a.state_dir,workers=a.workers)
    try:
        config={'kind':'world','vehicle':str(ROOT/'profiles/ranger_mini_v3'),
                'model':'perfect_planning','modules':['ML_PLANNING','ROUTING'],
                'repeat':a.repeat,'step_ms':10,'seed':1}
        if a.suite:
            config.update(suite=str(a.suite.resolve()),concurrency=a.workers)
            request={'action':'enqueue_suite','config':config}
        else:
            config['source']=str(a.source.resolve());request={'action':'enqueue','config':config}
        result=service.request(request);ids=result['ids'];last={}
        while True:
            jobs=[j for j in service.request({'action':'list'})['jobs'] if j['id'] in ids]
            for j in jobs:
                if last.get(j['id'])!=j['stage']:
                    print(json.dumps({'id':j['id'],'scene':Path(j['config']['source']).stem,'stage':j['stage'],
                                      'error':j.get('error')},ensure_ascii=False),flush=True);last[j['id']]=j['stage']
            if all(j['stage'] in TERMINAL for j in jobs):break
            time.sleep(.5)
        summary=[{'id':j['id'],'scene':j['config']['source'],'repeat':j['config']['repeat'],'status':j['stage'],'error':j.get('error'),
                  'parking_contract':j['config'].get('evaluation',{}).get('parking'),
                  'analysis':j.get('analysis'),'effective':j.get('effective_configuration')} for j in jobs]
        (a.state_dir/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        from parking_scorecard import scorecard
        parking=scorecard(summary)
        if parking['case_count']:
            (a.state_dir/'parking-scorecard.json').write_text(json.dumps(parking,ensure_ascii=False,indent=2))
        return 0 if all(j['stage']=='completed' for j in jobs) else 1
    finally:service.close()


if __name__=='__main__':raise SystemExit(main())
