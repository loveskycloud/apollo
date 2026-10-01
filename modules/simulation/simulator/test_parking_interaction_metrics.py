import copy
import unittest
from parking_interaction_metrics import evaluate


class ParkingInteractionTests(unittest.TestCase):
    def setUp(self):
        self.scene={'agents':[{'id':'bystander'}]}
        self.contract={'background_actor_ids':['bystander'],'crossing_actor_id':None,
            'minimum_moving_fraction':.9,'minimum_concurrent_motion_s':1.,
            'baseline_duration_s':10.,'maximum_extra_duration_s':.5}
        self.snapshots=[((i*.1,i*.02,0,0,.2 if 10<=i<90 else 0),
            [(1,2+i*.003,1,0,.4,.4,.03,0)]) for i in range(101)]

    def test_continuous_motion_without_delay_passes(self):
        result=evaluate(self.snapshots,self.scene,self.contract)
        self.assertEqual(result['status'],'PASS')
        self.assertGreater(result['ego_and_background_moving_s'],7)
        self.assertAlmostEqual(result['extra_duration_s'],0.)

    def test_missing_or_stationary_actor_cannot_pass(self):
        missing=copy.deepcopy(self.snapshots);missing[-1]=(missing[-1][0],[])
        self.assertEqual(evaluate(missing,self.scene,self.contract)['status'],'FAIL')
        stationary=[(p,[(1,2,1,0,.4,.4,0,0)]) for p,_ in self.snapshots]
        self.assertEqual(evaluate(stationary,self.scene,self.contract)['status'],'FAIL')

    def test_stalled_ego_and_unnecessary_wait_fail(self):
        stopped=[((p[0],p[1],p[2],p[3],0),o) for p,o in self.snapshots]
        self.assertEqual(evaluate(stopped,self.scene,self.contract)['status'],'FAIL')
        delayed=[((p[0]*1.2,*p[1:]),o) for p,o in self.snapshots]
        result=evaluate(delayed,self.scene,self.contract)
        self.assertEqual(result['status'],'FAIL');self.assertFalse(result['efficiency_pass'])

    def test_scorecard_keeps_failed_and_missing_cases_in_denominator(self):
        from parking_scorecard import scorecard
        metrics=evaluate(self.snapshots,self.scene,self.contract)
        jobs=[{'id':'measured','status':'failed','parking_contract':{'variant':'trigger_relevance_crowd_present','operation':'in'},
               'analysis':{'quality_metrics':{'runs':[{'parking_interaction':metrics}]}}},
              {'id':'missing','status':'failed','parking_contract':{'variant':'trigger_relevance_crowd_present','operation':'in'}}]
        result=scorecard(jobs)
        self.assertEqual(result['unrelated_motion']['cases'],2)
        self.assertEqual(result['dynamic_traffic']['cases'],0)
        self.assertEqual(result['unrelated_motion']['evaluated_pass'],1)
        self.assertEqual(result['unrelated_motion']['mission_success'],0)
        self.assertEqual(result['rates']['mission_success']['total'],2)


if __name__=='__main__':unittest.main()
