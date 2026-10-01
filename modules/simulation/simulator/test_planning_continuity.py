import unittest
from types import SimpleNamespace as N
from planning_continuity import PlanningContinuity


def message(empty=False, estop=False):
    return N(trajectory_point=[] if empty else [N(relative_time=t, path_point=N(x=0.,y=0.,theta=0.,kappa=0.),v=0.,a=0.) for t in (0.,.1,8.)],
             estop=N(is_estop=estop,reason='constraint'), header=N(status=N(error_code=0)),
             decision=N(main_decision=N(HasField=lambda _:False)))


class ContinuityTests(unittest.TestCase):
    def check(self, stamps, bad=None):
        c=PlanningContinuity()
        c.world(1_000_000_000)
        for i,t in enumerate(stamps):
            c.planning(round(t*1e9),bad if i==1 and bad else message())
        c.world(1_300_000_000)
        return c.result()

    def test_valid_stationary_plan_is_allowed(self):
        self.assertEqual(self.check([1,1.1,1.2,1.3])['status'],'PASS')

    def test_single_missing_plan_after_many_valid_frames_fails(self):
        r=self.check([1,1.1,1.2,1.3],message(empty=True))
        self.assertEqual(r['status'],'FAIL')
        self.assertEqual(r['empty_frames'],1)

    def test_publication_stops_in_middle_or_at_end(self):
        self.assertEqual(self.check([1,1.1,1.3])['publication_gaps'],1)
        self.assertTrue(self.check([1,1.1])['boundary_missing'])
        self.assertTrue(self.check([1.2,1.3])['boundary_missing'])

    def test_invalid_nonempty_plan_does_not_hide_failure(self):
        for bad in [message(estop=True),message(),message()]:
            if not bad.estop.is_estop:
                bad.trajectory_point[1].relative_time=0
            self.assertEqual(self.check([1,1.1,1.2,1.3],bad)['status'],'FAIL')
        bad=message();bad.trajectory_point[1].path_point.x=float('nan')
        self.assertEqual(self.check([1,1.1,1.2,1.3],bad)['status'],'FAIL')


if __name__=='__main__':unittest.main()
