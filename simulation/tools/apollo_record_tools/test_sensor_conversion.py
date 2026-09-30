"""Regression coverage for simulation sensor topics and raw image payloads."""
import os
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
import unittest
from types import SimpleNamespace
import numpy as np
from apollo_record_to_semantic_mcap import (
    RawImage, PointCloud, SceneContext, camera_entity_topic, lidar_entity_topic,
    write_raw_image, write_pointcloud_packed,
)


class CaptureWriter:
    def __init__(self):
        self.channel_ids = {}
        self.messages = []
        self.scene = SceneContext()
        self.scene.origin = np.zeros(3)
        self.scene.times = [1]
        self.scene.poses = [(1, np.zeros(3), np.array([0., 0., 0., 1.]))]
        self.scene.extrinsics = {
            "top": ("localization", np.array([0., 0., 2.]), np.array([0., 0., 0., 1.])),
            "front": ("localization", np.array([1., 0., 0.]), np.array([0., 0., 0., 1.])),
        }

    def _register_foxglove(self, name, cls, topic, metadata=None):
        self.channel_ids[topic] = name

    def add(self, topic, publish, message, payload):
        self.messages.append((topic, publish, message, payload))


class SensorConversionTests(unittest.TestCase):
    def test_two_lidars_keep_distinct_channels_and_extrinsics(self):
        writer = CaptureWriter()
        packed = np.array([[1., 2., 3., 4.]], dtype="<f4").tobytes()
        for sensor in ("top", "front"):
            source = f"/apollo/sensor/rslidar/{sensor}/PointCloud2"
            write_pointcloud_packed(writer, packed, 1, 20, 10, sensor, lidar_entity_topic(source))
        self.assertEqual([r[0] for r in writer.messages], ["/lidar/top/points", "/lidar/front/points"])
        clouds = [PointCloud.FromString(r[3]) for r in writer.messages]
        self.assertEqual(clouds[0].pose.position.z, 2)
        self.assertEqual(clouds[1].pose.position.x, 1)
        self.assertTrue(all(c.data == packed and c.frame_id == "wm_map_local" for c in clouds))
        self.assertEqual(lidar_entity_topic("/apollo/sensor/rslidar/up/PointCloud2"), "/lidar/up/points")

    def test_raw_image_preserves_pixels_stride_and_both_clocks(self):
        writer = CaptureWriter()
        # Two RGB pixels with row padding; must not drop padding or reorder RGB.
        msg = SimpleNamespace(width=2, height=1, step=8, encoding="rgb8",
                              data=bytes([255, 0, 0, 0, 255, 0, 99, 99]), frame_id="front")
        write_raw_image(writer, "/apollo/sensor/camera/front/image", msg, 20, 10)
        topic, publish, message, payload = writer.messages[0]
        image = RawImage.FromString(payload)
        self.assertEqual((topic, publish, message), ("/camera/front", 20, 10))
        self.assertEqual((image.width, image.height, image.step), (2, 1, 8))
        self.assertEqual(image.data, msg.data)
        self.assertEqual(image.timestamp.nanos, 10)
        self.assertEqual(camera_entity_topic("/apollo/sensor/camera/third_person/image"), "/camera/third_person")
        self.assertEqual(camera_entity_topic("/apollo/camera/Front120/compressed"), "/camera/Front120")

    def test_bad_images_fail_explicitly(self):
        for change in ({"encoding": "unknown"}, {"step": 2}, {"data": b""}, {"width": 0}):
            msg = SimpleNamespace(**dict(dict(width=1, height=1, step=3, encoding="rgb8",
                                               data=b"rgb", frame_id="front"), **change))
            with self.assertRaisesRegex(ValueError, "/apollo/sensor/camera/front/image"):
                write_raw_image(CaptureWriter(), "/apollo/sensor/camera/front/image", msg, 20, 10)


if __name__ == "__main__":
    unittest.main()
