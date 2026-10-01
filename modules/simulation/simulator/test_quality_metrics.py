import math
import unittest
from types import SimpleNamespace
from quality_metrics import body, drivable_area, motion_metrics


class QualityTests(unittest.TestCase):
    def test_explicit_undriveable_is_excluded_from_overlapping_driveable(self):
        def area(kind, coords):
            return SimpleNamespace(type=kind,id=SimpleNamespace(id=str(kind)),
                                   polygon=SimpleNamespace(point=[SimpleNamespace(x=x,y=y) for x,y in coords]))
        hdmap=SimpleNamespace(lane=[],ad_area=[
            area(1,[(-2,-2),(2,-2),(2,2),(-2,2)]),
            area(2,[(-.5,-.5),(.5,-.5),(.5,.5),(-.5,.5)])])
        road=drivable_area(hdmap)
        self.assertFalse(road.covers(body(0,0,0,.36,.36,.25)))
        self.assertTrue(road.covers(body(1,0,0,.36,.36,.25)))

    def test_resource_failure_does_not_become_behavior_pass(self):
        from task_service import execution_failure
        result=execution_failure(RuntimeError('RuntimeError: CUDA out of memory.'))
        self.assertEqual(result['failure_class'],'RESOURCE_EXHAUSTED')
        self.assertEqual(result['behavior_evaluation'],'NOT_EVALUATED')
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(execution_failure('Collision')['failure_class'],'ALGORITHM_OR_RUNTIME_FAILURE')

    def test_narrow_parking_contains_full_body_not_just_center(self):
        slot=body(0,0,0,.5,.5,.275)
        self.assertTrue(slot.covers(body(0,0,0,.36,.36,.25)))
        self.assertFalse(slot.covers(body(0,.03,0,.36,.36,.25)))
        self.assertFalse(slot.covers(body(0,0,math.radians(10),.36,.36,.25)))

    def test_stall_and_reverse_are_measured_separately_from_success(self):
        poses=[(i*.1,0,0,0,0) for i in range(151)]
        result=motion_metrics(poses)
        self.assertEqual(result['stops_over_10s'],1)
        self.assertEqual(result['longest_stop_s'],15)
        self.assertEqual(result['distance_travelled_m'],0)
        self.assertEqual(result['status'],'MEASURED')
        poses=[(i*.1,-i*.02,0,0,-.2) for i in range(11)]
        result=motion_metrics(poses)
        self.assertAlmostEqual(result['reverse_distance_m'],.2)
        self.assertLess(result['jerk_mps3']['max'],1e-9)

    def test_missing_and_nonmonotonic_samples_do_not_pass(self):
        self.assertEqual(motion_metrics([])['status'],'NOT_EVALUATED')
        with self.assertRaises(ValueError):motion_metrics([(0,0,0,0,0)]*3)
