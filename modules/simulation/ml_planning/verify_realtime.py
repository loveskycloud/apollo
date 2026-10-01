"""Causal check: same weights and route, identical plans before obstacle reveal."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'logsim/tools'))
from bag_diff import messages


def main():
    p=argparse.ArgumentParser();p.add_argument('suite',type=Path);a=p.parse_args()
    results=json.loads((a.suite/'summary.json').read_text());by_name={r['kind']:r for r in results}
    hashes={hashlib.sha256((Path(r['directory'])/'policy.weights').read_bytes()).hexdigest() for r in results}
    assert len(hashes)==1,hashes
    def plans(name):
        run=Path(by_name[name]['directory']);record=next(run.glob('simulation.record*'))
        return {t:b for c,t,b in messages(record) if c=='/apollo/planning'}
    evidence=[]
    names=['turn_obstacle','turn_obstacle_right','pedestrian_left','pedestrian_right','pedestrian_sudden','crossing_vehicle','straight_obstacle','straight_centered']
    if 'user_overtake' in by_name and 'user_multibend' in by_name:
        names.append('user_overtake')
    for name in names:
        baseline=plans('user_multibend' if name=='user_overtake' else ('straight_clear' if name.startswith('straight') else 'turn_clear'))
        actual=plans(name);rows=list(csv.DictReader((Path(by_name[name]['directory'])/'policy.csv').open()))
        first=min(float(r['timestamp']) for r in rows if int(r['obstacles'])>0);ns=round(first*1e9)
        prefix=[t for t in actual if t<ns]
        assert len(prefix)>=20 and all(actual[t]==baseline[t] for t in prefix),(name,first)
        changed=[t for t in actual if t>=ns and actual[t]!=baseline[t]]
        assert changed,(name,'No response to perception')
        evidence.append({'scenario':name,'first_obstacle_time_s':first,'identical_prefix_plans':len(prefix),
                         'first_changed_plan_s':min(changed)/1e9})
    report={'passed':True,'one_actor_sha256':hashes.pop(),'comparisons':evidence}
    (a.suite/'realtime-evidence.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))

if __name__=='__main__':main()
