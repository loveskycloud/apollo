"""An explicitly unsafe plan must terminate WorldSim, not freeze and succeed."""
import argparse
import json
from pathlib import Path
from fixtures_1haolou import create, MAP, VEHICLE
from run_parallel import prepare, execute

p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
scene=json.loads((create()/'turn_clear.worldsim.scenario.json').read_text())
scene['agents']=[{'id':'blocked-start','type':'AGENT_TYPE_STATIC','position':scene['ego']['position'],
                 'size':{'x':1,'y':1,'z':1}}]
meta=prepare(a.out.resolve(),'expected-estop',0,(MAP,VEHICLE),Path(__file__).resolve().parent/'models/v2/unified.weights',scene)
result=execute(meta,60)
assert result['returncode']!=0,result
log=(a.out/'runtime.log').read_text()
assert 'Planning reported estop: Unified PPO and braking trajectories violate' in log,log[-3000:]
print('PASS: unsafe initial state -> explicit estop -> WorldSim exits unsuccessfully')
