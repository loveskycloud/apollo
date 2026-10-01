"""CEM policy search using rewards from real WorldSim episodes.

CEM optimizes the exported PPO actor's lateral gain and speed bias. Each reward
comes from a fresh native simulator process plus independent record evaluation;
there is no surrogate rollout in this stage. The selected weights stay reviewable.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import multiprocessing
from pathlib import Path
import numpy as np
from fixtures_1haolou import create, MAP, VEHICLE
from run_parallel import prepare, execute

HERE=Path(__file__).resolve().parent
TRAIN_SCENES=['straight_clear','turn_clear','turn_obstacle','pedestrian_left','crossing_vehicle','moving_lead','user_overtake']

def sample(item):
    return execute(item,120)

def reward(result):
    if not result['passed']:return -1000.
    m=result['metrics']
    return (10 + 4*min(.5,m['min_clearance_m'] if m['min_clearance_m'] is not None else .5)
            - .015*m['shield_frames'] - m['goal_error_m']
            - 10*(m['clear_straight_rms_l_m'] or 0) - .005*m['near_miss_samples'])

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    p.add_argument('--weights',type=Path,default=HERE/'models/v2/unified.weights')
    p.add_argument('--rounds',type=int,default=3);p.add_argument('--population',type=int,default=8);p.add_argument('--workers',type=int,default=4)
    a=p.parse_args()
    if not 1 <= a.workers <= 32 or a.rounds < 1 or a.population < 2:
        p.error('workers must be 1..32; rounds >= 1; population >= 2')
    a.out=a.out.resolve();a.out.mkdir(parents=True,exist_ok=False)
    source=a.weights.resolve();tokens=source.read_text().split()
    if tokens[:5] != ['MLP_V2','16','64','64','2'] or len(tokens) != 5383:
        raise ValueError('Expected a unified 16-64-64-2 actor')
    base=np.array([float(v) for v in tokens[5:]])
    (a.out/'base.weights').write_text(source.read_text())
    rng=np.random.default_rng(29);mean=np.zeros(2);scale=np.array([.10,.25]);history=[];best=None
    scenes=create();counter=0
    with ProcessPoolExecutor(max_workers=a.workers,mp_context=multiprocessing.get_context('spawn')) as pool:
        for iteration in range(a.rounds):
            candidates=np.vstack([mean, np.clip(rng.normal(mean,scale,(a.population-1,2)),[-.3,-1],[.3,1])])
            metadata=[]
            for j,theta in enumerate(candidates):
                candidate=a.out/f'round-{iteration:02d}'/f'actor-{j:02d}';candidate.mkdir(parents=True)
                values=base.copy();values[-130:-66]*=np.exp(theta[0]);values[-2]*=np.exp(theta[0]);values[-1]+=theta[1]
                weights=candidate/'actor.weights';weights.write_text('MLP_V2\n16 64 64 2\n'+' '.join(format(v,'.12g') for v in values)+'\n')
                for name in TRAIN_SCENES:
                    scene=json.loads((scenes/f'{name}.worldsim.scenario.json').read_text())
                    run=prepare(candidate/name,name,0,(MAP,VEHICLE),weights,scene)
                    run.update(evaluator='1haolou',candidate=j);metadata.append(run)
            results=list(pool.map(sample,metadata));counter+=len(results)
            scores=np.array([np.mean([reward(r) for r in results if r['candidate']==j]) for j in range(len(candidates))])
            elite=np.argsort(scores)[-2:];chosen=int(np.argmax(scores))
            row={'iteration':iteration,'scores':scores.tolist(),'parameters':candidates.tolist(),'selected':chosen,
                 'episodes':len(results),'successful_episodes':sum(r['passed'] for r in results)}
            history.append(row)
            (a.out/f'round-{iteration:02d}'/'results.json').write_text(json.dumps(results,indent=2))
            if best is None or scores[chosen]>best['reward']:
                best={'reward':float(scores[chosen]),'parameters':candidates[chosen].tolist(),
                      'weights':str(a.out/f'round-{iteration:02d}'/f'actor-{chosen:02d}'/'actor.weights')}
            mean=np.array(best['parameters']);scale=np.maximum(.04,np.std(candidates[elite],axis=0))
            print(json.dumps(row),flush=True)
    selected=Path(best['weights']);(a.out/'selected.weights').write_bytes(selected.read_bytes())
    report={'algorithm':'PPO actor + CEM policy search on native WorldSim rewards','base_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'native_episodes':counter,'training_scenarios':TRAIN_SCENES,'best':best,'history':history,
            'selected_sha256':hashlib.sha256(selected.read_bytes()).hexdigest(),
            'deployment':'Not automatic: run complete scenario regression before installing selected.weights'}
    (a.out/'training.json').write_text(json.dumps(report,indent=2));print(json.dumps(best),flush=True)

if __name__=='__main__':main()
