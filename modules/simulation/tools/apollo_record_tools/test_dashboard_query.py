"""Dashboard contract: chassis feedback only, presence preserved, bounded prefetch."""
import os
import sys
import unittest
from types import SimpleNamespace

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
sys.path.insert(0, "/opt/apollo/neo/python")
from modules.common_msgs.chassis_msgs.chassis_pb2 import Chassis
from modules.common_msgs.perception_msgs.traffic_light_detection_pb2 import TrafficLightDetection
from mcap_debug_query import DebugQueries


class DashboardTests(unittest.TestCase):
    def engine(self, messages, traffic=None):
        engine = DebugQueries()
        tables = {"/apollo/canbus/chassis": (Chassis, messages)}
        if traffic is not None:
            tables["/apollo/perception/traffic_light"] = (TrafficLightDetection, traffic)
        engine.summary = SimpleNamespace(channels={k: SimpleNamespace(topic=k) for k in tables})
        def index(topic, clock):
            cls, samples = tables[topic]
            offset = 100 if clock == "publish_time" else 0
            rows = [(t+offset, t, t+100, m.SerializeToString()) for t, m in samples]
            return {"cls": cls, "rows": rows, "times": [r[0] for r in rows]}
        engine.index = index
        return engine

    def test_absent_is_not_zero_and_control_is_not_queried(self):
        engine = self.engine([(1, Chassis(steering_percentage=-18, driving_mode=1))])
        result = engine.dashboard_window(2, "message_time")
        self.assertEqual(set(result["streams"]), {"chassis", "traffic"})
        sample = result["streams"]["chassis"]["rows"][0]
        self.assertIsNone(sample["throttle_percentage"])
        self.assertIsNone(sample["brake_percentage"])
        self.assertEqual(sample["steering_percentage"], -18)
        self.assertEqual(sample["driving_mode"], "COMPLETE_AUTO_DRIVE")
        self.assertIn("not present", result["streams"]["traffic"]["notice"])

    def test_real_zero_nonfinite_and_absent_mode(self):
        engine = self.engine([(1, Chassis(throttle_percentage=0, brake_percentage=float("nan")))])
        sample = engine.dashboard_window(2, "message_time")["streams"]["chassis"]["rows"][0]
        self.assertEqual(sample["throttle_percentage"], 0)
        self.assertIsNone(sample["brake_percentage"])
        self.assertIsNone(sample["driving_mode"])  # not proto's default MANUAL

    def test_window_predecessor_and_explicit_clock(self):
        engine = self.engine([(i*1_000_000_000, Chassis(speed_mps=i)) for i in range(1, 12)])
        result = engine.dashboard_window(4_000_000_000, "publish_time")
        rows = result["streams"]["chassis"]["rows"]
        self.assertEqual(int(rows[0]["sample_ns"]), 2_000_000_100)
        self.assertLessEqual(int(rows[-1]["sample_ns"]), int(result["end_ns"]))
        current = [r for r in rows if int(r["sample_ns"]) <= 4_000_000_000][-1]
        self.assertEqual(current["speed_mps"], 3)

    def test_traffic_preserves_multiple_unknown_empty_and_limit(self):
        detection = TrafficLightDetection()
        detection.traffic_light.add(id="left", color=1, confidence=.8)
        detection.traffic_light.add(id="straight", color=3)
        detection.traffic_light.add(id="absent-color")
        engine = self.engine([], [(1, detection), (2, TrafficLightDetection())])
        rows = engine.dashboard_window(3, "message_time")["streams"]["traffic"]["rows"]
        self.assertEqual([l["color"] for l in rows[0]["lights"]], ["RED", "GREEN", None])
        self.assertEqual(rows[1]["lights"], [])
        for _ in range(30): detection.traffic_light.add()
        with self.assertRaisesRegex(ValueError, "32 traffic"):
            self.engine([], [(1, detection)]).dashboard_window(3, "message_time")


if __name__ == "__main__": unittest.main()
