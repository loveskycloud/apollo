"""Regression for the empty-road right-bias missed by arrival-only evaluation."""
import unittest

from evaluate_1haolou import centering_metrics


def row(s, lateral, obstacle=False, curvature=0):
    return dict(s=s, l=lateral, speed=.8, obs5=int(obstacle),
                curvature=curvature, obs10=curvature, obs11=curvature)


class CenteringTest(unittest.TestCase):
    def test_sustained_bias_fails_on_either_side(self):
        for lateral in (-.39, .39):
            result = centering_metrics([row(s, lateral) for s in range(8)])
            self.assertFalse(result['centering_passed'])
            self.assertEqual(result['clear_straight_samples'], 5)

    def test_recovery_distance_starts_after_bend_or_obstacle(self):
        for kwargs in ({'obstacle': True}, {'curvature': .3}):
            rows = [row(0, .3), row(1, .3, **kwargs), row(2, .3),
                    row(3, .2), row(4, .12), row(5, .05), row(6, .02)]
            result = centering_metrics(rows)
            self.assertTrue(result['centering_passed'])
            self.assertEqual(result['clear_straight_samples'], 2)


if __name__ == '__main__':
    unittest.main()
