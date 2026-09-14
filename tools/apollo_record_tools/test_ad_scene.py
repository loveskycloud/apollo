import os
import sys
import unittest
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
sys.path.insert(0, "/opt/apollo/neo/python")
import numpy as np
from ad_scene import SceneContext, rotation, planning_pb2, perception_obstacle_pb2, prediction_obstacle_pb2


class SceneTests(unittest.TestCase):
    def scene(self):
        scene = SceneContext()
        scene.origin = np.array([9900., 9000000., 3.])
        scene.poses = [(10, scene.origin + [1., 2., 0.], np.array([0., 0., 0., 1.])),
                       (20, scene.origin + [100., 100., 0.], np.array([0., 0., 0., 1.]))]
        scene.times = [10, 20]
        scene.extrinsics = {"lidar": ("imu", np.array([1., 0., 0.]), np.array([0., 0., 0., 1.])),
                            "imu": ("localization", np.array([0., 2., 0.]), np.array([0., 0., np.sqrt(.5), np.sqrt(.5)]))}
        return scene

    def test_latest_at_and_real_extrinsics(self):
        scene = self.scene()
        position, quaternion = scene.lidar_pose(19, "lidar")
        np.testing.assert_allclose(position, [1., 5., 0.], atol=1e-8)
        np.testing.assert_allclose(rotation(quaternion) @ [1., 0., 0.], [0., 1., 0.], atol=1e-8)
        self.assertIsNone(scene.lidar_pose(9, "lidar"))

    def test_missing_tf_is_not_identity(self):
        with self.assertRaisesRegex(ValueError, "Missing/cyclic"):
            self.scene().lidar_pose(15, "unknown")

    def test_planning_rebased_before_float32(self):
        scene = self.scene()
        plan = planning_pb2.ADCTrajectory()
        point = plan.trajectory_point.add().path_point
        point.x, point.y = 9900.1, 9000000.1
        out = scene.planned_path(plan.SerializeToString())
        np.testing.assert_allclose(out.xyz, [.1, .1, 0.], atol=1e-8)
        self.assertEqual(list(scene.planned_path(planning_pb2.ADCTrajectory().SerializeToString()).xyz), [])

    def test_obstacle_ground_center_and_explicit_empty_clear(self):
        scene = self.scene()
        msg = perception_obstacle_pb2.PerceptionObstacles()
        ob = msg.perception_obstacle.add(id=42, theta=0, length=4, width=2, height=1)
        ob.position.x, ob.position.y, ob.position.z = scene.origin
        out = scene.perceived_obstacles(msg.SerializeToString())
        self.assertEqual(list(out.strip_lengths), [5,5,2,2,2,2])
        points = np.array(out.xyz).reshape(-1,3)
        np.testing.assert_allclose(points[0], [-2,-1,0])
        self.assertEqual(points[:,2].max(), 1)
        self.assertTrue(out.labels[0].startswith("42"))
        self.assertFalse(scene.perceived_obstacles(perception_obstacle_pb2.PerceptionObstacles().SerializeToString()).xyz)

    def test_prediction_hypotheses_are_separate_and_not_invented(self):
        scene = self.scene()
        msg = prediction_obstacle_pb2.PredictionObstacles()
        obstacle = msg.prediction_obstacle.add()
        self.assertFalse(scene.predicted_trajectories(msg.SerializeToString()).xyz)
        for offset in (1,2):
            trajectory = obstacle.trajectory.add(probability=.5)
            for x in (0,1):
                p = trajectory.trajectory_point.add().path_point
                p.x, p.y = 9900+x, 9000000+offset
        out = scene.predicted_trajectories(msg.SerializeToString())
        self.assertEqual(list(out.strip_lengths), [2,2])
        np.testing.assert_allclose(list(out.xyz)[:6], [0,1,0,1,1,0])


if __name__ == "__main__":
    unittest.main()
