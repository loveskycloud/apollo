import unittest
from parking_trigger_metrics import evaluate


class ParkingTriggerTests(unittest.TestCase):
    def setUp(self):
        self.scene={'ego':{'position':{'x':0,'y':0}},'agents':[{'id':'ped',
            'routes':[{'waypoints':[{'position':{'x':2,'y':0}},{'position':{'x':2,'y':1}}]}]}]}
        self.contract={'actor_ids':['ped'],'minimum_activation_displacement_m':.15,
                       'minimum_activation_speed_mps':.05}

    def test_no_activation_cannot_pass_even_if_parking_finishes(self):
        result=evaluate([((0,0,0,0,0),[]),((10,2,0,0,0),[])],self.scene,self.contract)
        self.assertFalse(result['activation_during_maneuver'])
        self.assertEqual(result['status'],'FAIL')

    def test_initial_or_stopped_activation_does_not_count_as_mid_maneuver(self):
        for x,speed in [(0,.2),(.4,0)]:
            snapshots=[((0,0,0,0,0),[]),((1,x,0,0,speed),[(1,2,0,0,.4,.4,0,.5)])]
            self.assertFalse(evaluate(snapshots,self.scene,self.contract)['activation_during_maneuver'])

    def test_aborted_record_keeps_trigger_evidence_but_does_not_pass(self):
        snapshots=[((0,0,0,0,0),[]),((2,.3,0,0,.2),[(1,2,0,0,.4,.4,0,.5)])]
        result=evaluate(snapshots,self.scene,self.contract)
        self.assertTrue(result['activation_during_maneuver'])
        self.assertFalse(result['all_actors_cleared']);self.assertEqual(result['status'],'FAIL')

    def test_actor_must_move_then_clear_at_its_exit(self):
        snapshots=[((0,0,0,0,0),[]),((2,.3,0,0,.2),[(1,2,0,0,.4,.4,0,.5)]),
                   ((4,.4,0,0,0),[(1,2,.96,0,.4,.4,0,.5)]),((4.1,.4,0,0,0),[])]
        self.assertEqual(evaluate(snapshots,self.scene,self.contract)['status'],'PASS')
        snapshots[2][1][0]=(1,2,.4,0,.4,.4,0,.5)
        self.assertFalse(evaluate(snapshots,self.scene,self.contract)['all_actors_cleared'])

    def test_trigger_success_does_not_turn_failed_parking_into_success(self):
        from parking_scorecard import scorecard
        job={'id':'failed','status':'failed','parking_contract':{'variant':'trigger_pedestrian_cross','operation':'in'},
             'analysis':{'parking_dynamic':{'activation_during_maneuver':True,'all_actors_cleared':True}}}
        s=scorecard([job])
        self.assertEqual(s['dynamic_traffic']['activated_during_maneuver'],1)
        self.assertEqual(s['dynamic_traffic']['mission_success'],0)
        self.assertEqual(s['rates']['parking_success']['total'],1)
        self.assertEqual(s['rates']['parking_success']['passed'],0)

    def test_yield_response_measures_real_braking_and_resume(self):
        snapshots=[((0,0,0,0,0),[])]
        for i in range(6):
            t=i*.1
            snapshots.append(((2+t,.3+.2*t-.2*t*t,0,0,max(0.,.2-.4*t)),[(1,2,t,0,.4,.4,0,.5)]))
        snapshots += [((4,.35,0,0,0),[(1,2,.96,0,.4,.4,0,.5)]),
                      ((4.1,.35,0,0,0),[]),((4.9,.352,0,0,.04),[])]
        response=evaluate(snapshots,self.scene,self.contract)['yield_response']
        self.assertAlmostEqual(response['time_to_stop_s'],.5)
        self.assertAlmostEqual(response['braking_distance_m'],.05)
        self.assertAlmostEqual(response['peak_braking_deceleration_mps2'],.4)
        self.assertAlmostEqual(response['resume_delay_after_clear_s'],.8)
        self.assertFalse(response['premature_resume'])
        snapshots[2]=((2.1,.3,0,0,0),snapshots[2][1])
        self.assertAlmostEqual(evaluate(snapshots,self.scene,self.contract)['yield_response']['peak_braking_deceleration_mps2'],2.)

    def persistent_fixture(self):
        self.contract.update(version=2, actor_end_states={'ped':{'kind':'stopped_visible',
            'position':{'x':2,'y':1},'tolerance_m':.04,'minimum_stationary_s':2}})
        obstacle=(1,2,1,0,.4,.4,0,0)
        return [((0,0,0,0,0),[]),((2,.3,0,0,.2),[(1,2,0,0,.4,.4,0,.5)]),
                ((2.5,.35,0,0,0),[(1,2,.25,0,.4,.4,0,.5)]),
                ((4,.35,0,0,0),[obstacle]),((4.9,.4,0,0,.1),[obstacle]),
                ((7,1,0,0,0),[obstacle])]

    def test_resume_with_stopped_visible_actor_without_clearing(self):
        r=evaluate(self.persistent_fixture(),self.scene,self.contract)
        self.assertEqual(r['status'],'PASS')
        self.assertFalse(r['all_actors_cleared'])
        self.assertTrue(r['persistent_actors_retained'])
        self.assertTrue(r['yield_response']['resumed_with_stationary_actors_visible'])
        self.assertAlmostEqual(r['yield_response']['resume_delay_after_ready_s'],.9)
        self.assertIsNone(r['yield_response']['resume_delay_after_clear_s'])

    def test_retained_actor_disappearing_moving_or_stopping_elsewhere_fails(self):
        for final in ([],[(1,2,1,0,.4,.4,0,.1)],[(1,3,1,0,.4,.4,0,0)]):
            snapshots=self.persistent_fixture();snapshots[-1]=(snapshots[-1][0],final)
            self.assertEqual(evaluate(snapshots,self.scene,self.contract)['status'],'FAIL')

    def test_stopped_actor_without_ego_resume_cannot_pass(self):
        snapshots=self.persistent_fixture();snapshots[-2]=((4.9,.35,0,0,0),snapshots[-2][1])
        self.assertEqual(evaluate(snapshots,self.scene,self.contract)['status'],'FAIL')

    def test_brief_stop_or_perception_gap_cannot_count_as_persistent(self):
        snapshots=self.persistent_fixture();snapshots[-2]=(snapshots[-2][0],[])
        self.assertEqual(evaluate(snapshots,self.scene,self.contract)['status'],'FAIL')
        snapshots=self.persistent_fixture()[:-1]
        self.assertEqual(evaluate(snapshots,self.scene,self.contract)['status'],'FAIL')

    def test_old_clear_contract_still_rejects_retained_actor(self):
        snapshots=self.persistent_fixture();self.contract.pop('actor_end_states')
        self.assertEqual(evaluate(snapshots,self.scene,self.contract)['status'],'FAIL')

    def test_scorecard_counts_each_retained_case_and_uses_ready_delay(self):
        from parking_scorecard import scorecard
        metrics=evaluate(self.persistent_fixture(),self.scene,self.contract)
        def job(i, dynamic):
            return {'id':str(i),'status':'failed','parking_contract':{'variant':'trigger_persistent_bay_side','operation':'in'},
                    'analysis':{'parking_dynamic':dynamic}}
        other={'status':'FAIL','persistent_actors_retained':False}
        result=scorecard([job(1,metrics),job(2,metrics),job(3,other)])
        self.assertEqual(result['dynamic_traffic']['persistent_actors_retained'],2)
        self.assertEqual(result['dynamic_traffic']['resumed_with_stationary_actors_visible'],2)
        self.assertEqual(result['dynamic_traffic']['yield_then_resume'],2)
        self.assertEqual(result['dynamic_traffic']['all_actors_cleared'],0)
        self.assertEqual(result['dynamic_traffic']['mission_success'],0)

    def test_uninterrupted_parking_with_retained_unrelated_actor_passes(self):
        snapshots=self.persistent_fixture()
        snapshots=[((p[0],p[1],p[2],p[3],.2 if 0<p[0]<7 else 0),obs) for p,obs in snapshots]
        result=evaluate(snapshots,self.scene,self.contract)
        self.assertEqual(result['status'],'PASS')
        self.assertTrue(result['yield_response']['continued_with_stationary_actors_visible'])


if __name__=='__main__':unittest.main()
