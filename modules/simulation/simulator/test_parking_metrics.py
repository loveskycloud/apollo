import unittest
import hashlib
import json
import tempfile
from pathlib import Path
from shapely.geometry import box
from parking_metrics import evaluate_parking
from parking_scorecard import scorecard


class ParkingMetricsTests(unittest.TestCase):
    def metric(self, samples, operation='in', **changes):
        contract={'operation':operation,'variant':'base','entry':'front','goal':[0,0,0]}
        contract.update(changes)
        return evaluate_parking(samples,box(-.5,-.275,.5,.275),0,(.36,.36,.25),contract,[10,1],[9],[1])

    def test_inside_but_not_straight_is_not_aligned_success(self):
        m=self.metric([(0,-1,0,0,.1),(1,0,.015,0,.1),(2,0,.015,0,0)])
        self.assertTrue(m['parking_success']);self.assertFalse(m['straight_parking'])
        self.assertAlmostEqual(m['lateral_error_m'],.015)

    def test_exit_cannot_pass_while_body_remains_in_bay(self):
        m=self.metric([(0,0,0,0,0),(1,.2,0,0,.1),(2,.4,0,0,0)],'out',goal=[.4,0,0])
        self.assertFalse(m['exit_success']);self.assertIsNone(m['parking_success'])

    def test_combined_mission_requires_stopped_parking_phase(self):
        m=self.metric([(0,-1,0,0,.1),(1,0,0,0,.1),(2,1,0,0,.1),(3,2,0,0,0)],'in_out',goal=[2,0,0])
        self.assertTrue(m['exit_success']);self.assertEqual(m['status'],'FAIL')
        samples=[(0,-1,0,0,.1),(1,0,0,0,0),(2,0,0,0,0),(3,0,0,0,0),(4,1,0,0,.1),(5,2,0,0,0)]
        self.assertEqual(self.metric(samples,'in_out',goal=[2,0,0])['status'],'PASS')

    def test_safe_rejection_is_not_parking_success(self):
        samples=[(i*.1,-2,0,0,0) for i in range(61)]
        m=self.metric(samples,variant='occupied')
        self.assertTrue(m['safe_rejection']);self.assertFalse(m['parking_success'])
        self.assertEqual(m['status'],'PASS')

    def test_brief_alignment_does_not_pass_combined_straight_parking(self):
        samples=[(0,-1,0,0,.1),(1,0,.015,0,0),(2,0,.015,0,0),(3,0,0,0,0),(3.5,0,0,0,0),(4,1,0,0,.1),(5,2,0,0,0)]
        m=self.metric(samples,'in_out',goal=[2,0,0])
        self.assertTrue(m['parking_success']);self.assertFalse(m['straight_parking'])
        self.assertEqual(m['straight_dwell_s'],.5)

    def test_failed_case_stays_in_denominator_and_repeats_must_all_pass(self):
        m=self.metric([(0,-1,0,0,.1),(1,0,0,0,.1),(2,0,0,0,0)])
        def job(i,status,metrics):return {'id':i,'status':status,'repeat':len(metrics),'analysis':{'quality_metrics':{'runs':[{'parking':p} for p in metrics]}}}
        result=scorecard([job('ok','completed',[m,m]),job('collision','failed',[m])])
        self.assertEqual(result['rates']['parking_success'],{'passed':1,'total':2,'rate':.5})
        self.assertEqual(result['rates']['straight_parking']['rate'],.5)
        partial=job('partial','completed',[m]);partial['repeat']=2
        self.assertEqual(scorecard([partial])['rates']['parking_success']['passed'],0)

    def test_occupied_audit_rejects_remote_or_scripted_blocker(self):
        from scenario_evaluation import load_evaluation, evaluation_path
        fixtures=Path(__file__).resolve().parents[1]/'scene_editor/examples/parking_missions_v2'
        original=next(fixtures.glob('*__in__occupied.worldsim.scenario.json'))
        for mutation in ('remote','moving','scripted','wrong_hold'):
            scene=json.loads(original.read_text());audit=json.loads(evaluation_path(original).read_text())
            if mutation=='remote':scene['agents'][0]['position']['x']+=20
            if mutation=='moving':scene['agents'][0]['routes']=[{'waypoints':[{'speed':.1}]}]
            if mutation=='scripted':scene['triggers']=[{'actions':[{'targetAgentId':'blocker'}]}]
            if mutation=='wrong_hold':audit['parking']['hold_goal'][0]+=1
            with tempfile.TemporaryDirectory() as d:
                source=Path(d)/'case.worldsim.scenario.json';source.write_text(json.dumps(scene))
                audit['scenario_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
                evaluation_path(source).write_text(json.dumps(audit))
                with self.assertRaises(ValueError):load_evaluation(source)

    def test_departure_audit_rejects_a_different_exit_target(self):
        from scenario_evaluation import load_evaluation, evaluation_path
        fixtures=Path(__file__).resolve().parents[1]/'scene_editor/examples/parking_missions_v2'
        original=next(fixtures.glob('*__out__base.worldsim.scenario.json'))
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/original.name;source.write_bytes(original.read_bytes())
            audit=json.loads(evaluation_path(original).read_text());audit['parking']['goal'][0]+=1
            evaluation_path(source).write_text(json.dumps(audit))
            with self.assertRaisesRegex(ValueError,'exit command'):load_evaluation(source)


if __name__=='__main__':unittest.main()
