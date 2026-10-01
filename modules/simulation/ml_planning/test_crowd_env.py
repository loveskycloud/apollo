import unittest
import numpy as np
from env import VectorEnv


class CrowdEnvironmentTest(unittest.TestCase):
    def environment(self):
        env = VectorEnv(1)
        env.x[:] = env.y[:] = env.yaw[:] = env.speed[:] = env.curvature[:] = 0
        env.mask[:] = False
        env.active[:] = True
        env.extra_mask[:] = False
        env.extra_mask[:, 0] = True
        env.extra_ovs[:] = env.extra_ovl[:] = 0
        env.extra_length[:] = env.extra_width[:] = .4
        env.extra_ox[:] = 20
        env.extra_oy[:] = 0
        env.goal[:] = 30
        env.half_width[:] = 1
        return env

    def test_secondary_actor_is_observed(self):
        env = self.environment()
        env.extra_ox[0, 0] = 3
        observation = env.obs()[0]
        self.assertEqual(observation[5], 1)
        self.assertAlmostEqual(observation[3], .3)

    def test_secondary_actor_collision_terminates_episode(self):
        env = self.environment()
        env.extra_ox[0, 0] = .5
        _, reward, done, episodes = env.step(np.zeros((1, 2)))
        self.assertTrue(done[0])
        self.assertTrue(episodes[0]['collision'])
        self.assertLess(reward[0], -199)

    def test_arrival_does_not_hide_an_incoming_rear_collision(self):
        env=self.environment()
        env.x[:]=29.8
        env.extra_ox[0,0]=27.8
        env.extra_ovs[0,0]=.3
        _,_,done,episodes=env.step(np.array([[0.,-8.]]))
        self.assertFalse(done[0])
        self.assertEqual(episodes,[])
        env.extra_oy[0,0]=1.2
        _,_,done,episodes=env.step(np.array([[0.,-8.]]))
        self.assertTrue(done[0])
        self.assertTrue(episodes[0]['success'])

    def test_inactive_secondary_actor_is_not_observed(self):
        env = self.environment()
        env.active[:] = False
        self.assertEqual(env.obs()[0, 5], 0)


if __name__ == '__main__':
    unittest.main()
