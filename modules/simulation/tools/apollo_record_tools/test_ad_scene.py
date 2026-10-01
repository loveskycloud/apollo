import os
import sys
import unittest
import hashlib
import json
import tempfile
from pathlib import Path
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
sys.path.insert(0, "/opt/apollo/neo/python")
import numpy as np
from ad_scene import SceneContext, rotation, planning_pb2, perception_obstacle_pb2, prediction_obstacle_pb2
from vehicle_geometry import load_vehicle_geometry


class SceneTests(unittest.TestCase):
    def scene(self):
        scene = SceneContext()
        scene.origin = np.array([9900., 9000000., 3.])
        scene.poses = [(10, scene.origin + [1., 2., 0.], np.array([0., 0., 0., 1.])),
                       (20, scene.origin + [100., 100., 0.], np.array([0., 0., 0., 1.]))]
        scene.times = [10, 20]
        scene.vehicle = {"width": .5, "left_edge_to_center": .25, "right_edge_to_center": .25,
                         "max_deceleration": -1.}
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

    def test_ribbon_matches_configured_edges_and_deceleration_colors(self):
        scene = self.scene()
        scene.vehicle.update(width=.7, left_edge_to_center=.4, right_edge_to_center=.3)
        plan = planning_pb2.ADCTrajectory()
        for i, acceleration in enumerate((.2, -.5, -1., -2.)):
            point = plan.trajectory_point.add(a=acceleration)
            point.path_point.x, point.path_point.y = 9900+i, 9000000
            point.path_point.theta = np.pi/2 if i == 3 else 0
        out = scene.planned_path(plan.SerializeToString())
        pairs = np.array(out.ribbon_xyz).reshape(-1, 2, 3)
        np.testing.assert_allclose(np.linalg.norm(pairs[:, 0]-pairs[:, 1], axis=1), .7)
        np.testing.assert_allclose(pairs[0], [[0, .4, .025], [0, -.3, .025]])
        np.testing.assert_allclose(pairs[3], [[2.6, 0, .025], [3.3, 0, .025]], atol=1e-8)
        colors = [int(c).to_bytes(4, "big") for c in out.vertex_rgba[::2]]
        self.assertEqual(colors[0], bytes((40,210,90,220)))
        self.assertEqual(colors[-1], bytes((240,45,45,220)))
        self.assertLess(colors[0][0], colors[1][0])
        self.assertLess(colors[1][0], colors[2][0])
        self.assertEqual(colors[2], colors[3])

    def test_brake_feedback_is_latest_at_not_future_or_stale(self):
        scene = self.scene()
        scene.brakes = [(1_000_000_000, .5), (2_000_000_000, 1.)]
        scene.brake_times = [row[0] for row in scene.brakes]
        plan = planning_pb2.ADCTrajectory()
        for x in (9900,9901):
            p = plan.trajectory_point.add(a=.1).path_point
            p.x, p.y, p.theta = x, 9000000, 0
        color = lambda ns: scene.planned_path(plan.SerializeToString(), ns).vertex_rgba[0]
        self.assertEqual(color(900_000_000), 0x28D25ADC)
        self.assertEqual(color(1_200_000_000), 0x8C8044DC)
        self.assertEqual(color(1_600_000_000), 0x28D25ADC)
        self.assertEqual(color(2_000_000_000), 0xF02D2DDC)
        plan.trajectory_point[0].ClearField("a")
        self.assertEqual(color(900_000_000), 0x8C96A0D2)

    def test_vehicle_snapshot_is_used_and_verified(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            (root / "run-1").mkdir()
            vehicle = root / "vehicle/vehicle_param.pb.txt"
            vehicle.parent.mkdir()
            vehicle.write_text('vehicle_param { length: .72 width: .5 height: .66 '
                               'front_edge_to_center: .62 back_edge_to_center: .1 '
                               'left_edge_to_center: .25 right_edge_to_center: .25 max_deceleration: -1 }')
            manifest = {"sha256": {"vehicle/vehicle_param.pb.txt": hashlib.sha256(vehicle.read_bytes()).hexdigest()}}
            (root / "manifest.json").write_text(json.dumps(manifest))
            record = str(root / "run-1/simulation.record")
            config = load_vehicle_geometry([record])
            self.assertEqual(config["width"], .5)
            self.assertEqual(config["path"], str(vehicle))
            with self.assertRaisesRegex(ValueError, "own vehicle snapshot"):
                load_vehicle_geometry([record], str(root / "other.pb.txt"))
            vehicle.write_text(vehicle.read_text().replace('width: .5', 'width: .6'))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                load_vehicle_geometry([record])
            with self.assertRaisesRegex(ValueError, "disagrees with edge distances"):
                load_vehicle_geometry([], str(vehicle))

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
