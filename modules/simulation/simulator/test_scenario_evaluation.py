import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from scenario_evaluation import evaluation_path, evaluate_motion, load_evaluation


class ScenarioEvaluationTests(unittest.TestCase):
    def contract(self):
        return {'expectation': 'safe_stop', 'blocked_by': ['barrier'],
                'stop_path': [{'x': x, 'y': 0} for x in (2, 2.2, 2.4, 2.6)]}

    def poses(self, x=2.2, speed=0):
        return [(i*.1, x, 0, speed) for i in range(61)]

    def test_blocked_road_accepts_stable_safe_stop_without_arrival(self):
        result=evaluate_motion(self.poses(), {'x': 20, 'y': 0}, self.contract())
        self.assertEqual(result['status'], 'PASS')
        self.assertGreater(result['goal_distance_m'], 10)

    def test_parking_at_start_or_creeping_does_not_pass(self):
        for poses in (self.poses(x=0), self.poses(speed=.2), self.poses()[-20:]):
            self.assertEqual(evaluate_motion(poses, {'x': 20, 'y': 0}, self.contract())['status'], 'FAIL')

    def test_clearable_hazard_still_requires_arrival(self):
        for mode in ('reach_goal','yield_then_proceed'):
            self.assertEqual(evaluate_motion(self.poses(), {'x':20,'y':0}, {'expectation':mode})['status'],'FAIL')
            self.assertEqual(evaluate_motion(self.poses(x=19.8), {'x':20,'y':0}, {'expectation':mode})['status'],'PASS')
            self.assertEqual(evaluate_motion(self.poses(x=19.8,speed=.2), {'x':20,'y':0}, {'expectation':mode})['status'],'FAIL')

    def test_parking_requires_precision_and_a_stop(self):
        for x, speed, expected in [(2.01,0,'PASS'),(2.04,0,'FAIL'),(2.01,.1,'FAIL')]:
            self.assertEqual(evaluate_motion(self.poses(x=x,speed=speed), {'x':2,'y':0},
                                            {'expectation':'park'})['status'], expected)

    def test_parking_audit_must_match_command(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'case.worldsim.scenario.json'
            source.write_text(json.dumps({'ego':{'parkingSpaceId':'bay'}}))
            value=dict(kind='worldsim-evaluation',version=1,reason='Audited parking',expectation='park',
                       scenario_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                       parking={'space_id':'bay','entry':'rear','goal':[0,0,0]})
            evaluation_path(source).write_text(json.dumps(value))
            self.assertEqual(load_evaluation(source)['expectation'],'park')
            value['parking']['space_id']='wrong'
            evaluation_path(source).write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'matching space ID'):load_evaluation(source)

    def test_reviewed_narrow_bend_expects_a_stop(self):
        source=Path(__file__).resolve().parents[1]/'scene_editor/examples/beijing_zongyuan_1haolou/coverage_Lane_60_reverse_mixed.worldsim.scenario.json'
        value=load_evaluation(source)
        self.assertEqual(value['expectation'],'safe_stop')
        passage=value['audit']['passages'][0]
        self.assertLess(passage['available_passage_m'],passage['required_passage_m'])
        self.assertGreater(len(value['stop_path']),2)

    def test_changed_scene_invalidates_its_reviewed_expectation(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'case.worldsim.scenario.json'
            source.write_text(json.dumps({'agents':[{'id':'barrier','type':'AGENT_TYPE_STATIC','enabled':True}]}))
            value=dict(self.contract(),kind='worldsim-evaluation',version=1,reason='Permanent narrow-road blockage',
                       scenario_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
            evaluation_path(source).write_text(json.dumps(value))
            self.assertEqual(load_evaluation(source)['expectation'],'safe_stop')
            source.write_text('{"agents":[]}')
            with self.assertRaisesRegex(ValueError,'changed after'):
                load_evaluation(source)

    def test_invalid_scene_is_not_enqueued_and_proven_passage_stays_active(self):
        from task_service import validate
        root=Path(__file__).resolve().parents[1]/'scene_editor/examples/beijing_zongyuan_1haolou'
        blocked=root/'coverage_Lane_60_reverse_mixed.worldsim.scenario.json'
        with self.assertRaisesRegex(ValueError, 'Invalid scenario'):
            validate({'kind':'world','source':str(blocked)})
        manifest=json.loads((root/'beijing_zongyuan_1haolou.suite.json').read_text())
        self.assertNotIn(blocked.name,manifest['scenarios'])
        proven=root/'coverage_Lane_65_static_right.worldsim.scenario.json'
        self.assertIn(proven.name,manifest['scenarios'])
        self.assertEqual(load_evaluation(proven)['expectation'],'reach_goal')

    def test_persistent_actor_contract_rejects_cleanup_and_invalid_targets(self):
        root=Path(__file__).resolve().parents[1]/'scene_editor/examples/parking_persistent_traffic_v1'
        original=next(root.glob('*.worldsim.scenario.json'))
        contract=load_evaluation(original)
        self.assertEqual(contract['parking_dynamic']['version'],2)
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/original.name
            scene=json.loads(original.read_text())
            scene['triggers'][-1]['actions'].append({'kind':'ACTION_DISABLE','targetAgentId':'retained_vehicle'})
            source.write_text(json.dumps(scene))
            contract['scenario_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
            evaluation_path(source).write_text(json.dumps(contract))
            with self.assertRaisesRegex(ValueError,'without being disabled'):load_evaluation(source)
            source.write_bytes(original.read_bytes())
            contract=load_evaluation(original)
            contract['parking_dynamic']['actor_end_states']['retained_vehicle']['position']['x']=float('nan')
            evaluation_path(source).write_text(json.dumps(contract))
            with self.assertRaisesRegex(ValueError,'finite stop point'):load_evaluation(source)
            contract=load_evaluation(original);contract['parking_dynamic']['actor_end_states']={}
            evaluation_path(source).write_text(json.dumps(contract))
            with self.assertRaisesRegex(ValueError,'every actor'):load_evaluation(source)

    def test_unrelated_motion_requires_retained_actors_and_efficiency_budget(self):
        root=Path(__file__).resolve().parents[1]/'scene_editor/examples/parking_relevance_v1'
        original=next(root.glob('*__trigger_relevance_crowd_present.worldsim.scenario.json'))
        value=load_evaluation(original)
        self.assertEqual(len(value['parking_interaction']['background_actor_ids']),8)
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/original.name;source.write_bytes(original.read_bytes())
            value['parking_interaction']['maximum_extra_duration_s']=None
            evaluation_path(source).write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'completion-time budget'):load_evaluation(source)
            value=load_evaluation(original);scene=json.loads(source.read_text())
            scene['triggers']=[{'actions':[{'kind':'ACTION_DISABLE','targetAgentId':'bystander_0'}]}]
            source.write_text(json.dumps(scene));value['scenario_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
            evaluation_path(source).write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'remain visible'):load_evaluation(source)


if __name__ == '__main__':
    unittest.main()
