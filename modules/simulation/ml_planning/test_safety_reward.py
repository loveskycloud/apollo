"""Body-edge and safety-reward regressions for overtaking."""
import unittest
import numpy as np

from env import VectorEnv, body_clearance


class SafetyRewardTest(unittest.TestCase):
    def test_rear_must_clear_obstacle_front(self):
        gap, hit, rear = body_clearance(0, 0, 0, -.8, 0, 1, .4)
        self.assertFalse(hit)
        self.assertAlmostEqual(gap, .2)
        self.assertAlmostEqual(rear, .2)
        self.assertLess(rear, .35)

    def test_parallel_clearance_uses_body_edges(self):
        gap, hit, rear = body_clearance(0, .2, 0, 0, -.4, 1, .4)
        self.assertFalse(hit)
        self.assertAlmostEqual(gap, .15)
        self.assertLess(rear, 0)

    def test_near_miss_cost_grows_and_collision_dominates(self):
        env = VectorEnv(4)
        for key in ('x', 'y', 'yaw', 'speed', 'oy', 'ovs', 'ovl', 'curvature'):
            getattr(env, key)[:] = 0
        env.mask[:] = True; env.active[:] = True
        env.length[:] = 1; env.width[:] = .4; env.half_width[:] = 1
        env.goal[:] = 30
        env.extra_mask[:] = False
        env.ox[:] = [.62+.5+.08, .62+.5+.04, .62+.5+.02, .62+.5-.05]
        _, reward, done, info = env.step(np.zeros((4, 2)))
        self.assertGreater(reward[0], reward[1])
        self.assertGreater(reward[1], reward[2])
        self.assertLess(reward[3], -199)
        self.assertTrue(done[3])
        self.assertTrue(info[-1]['collision'])


if __name__ == '__main__':
    unittest.main()
