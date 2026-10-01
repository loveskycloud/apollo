"""Run all map scenarios with exactly the same actor, in isolated processes."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing
from pathlib import Path
from run_parallel import prepare, execute
from fixtures_1haolou import create, MAP, VEHICLE

HERE=Path(__file__).resolve().parent

def run(item):
    # The independent map evaluator is selected by test data, never by the actor.
    return execute(item,120)

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--workers',type=int,default=4)
    p.add_argument('--weights',type=Path,default=HERE/'models/v2/unified.weights',help='Development-only checkpoint evaluation; UI always uses the deployed unified actor')
    a=p.parse_args()
    if not 1 <= a.workers <= 32:
        p.error('workers must be 1..32')
    a.out=a.out.resolve();a.out.mkdir(parents=True,exist_ok=False)
    jobs=[]
    for source in sorted(create().glob('*.json')):
        scene=json.loads(source.read_text());name=source.name.split('.')[0]
        meta=prepare(a.out/name,name,0,(MAP,VEHICLE),a.weights.resolve(),scene)
        meta.update(evaluator='1haolou',map=str(MAP),vehicle=str(VEHICLE))
        jobs.append(meta)
    with ProcessPoolExecutor(max_workers=a.workers,mp_context=multiprocessing.get_context('spawn')) as pool:
        results=list(pool.map(run,jobs))
    (a.out/'summary.json').write_text(json.dumps(results,indent=2))
    for r in results: print(r['kind'], r['returncode'],r.get('metrics',r.get('evaluation_error')),flush=True)
    return 0 if all(r['passed'] for r in results) else 1

if __name__=='__main__':raise SystemExit(main())
