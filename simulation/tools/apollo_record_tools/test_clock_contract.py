"""Two clocks, non-identical timestamps, and bounded dual-clock import coverage."""
import os
import sys
import unittest
from pathlib import Path
from mcap.reader import make_reader
from mcap.writer import Writer

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
sys.path.insert(0, "/opt/apollo/neo/python")
from modules.common_msgs.localization_msgs.localization_pb2 import LocalizationEstimate
from modules.common_msgs.sensor_msgs.pointcloud_pb2 import PointCloud
from apollo_record_to_semantic_mcap import message_time_ns
from mcap_playback_plan import plan
import test_mcap_debug_query


class ClockTests(unittest.TestCase):
    def test_unstamped_messages_remain_publish_only_with_visible_notice(self):
        fixture = test_mcap_debug_query.DebugQueryTests()
        fixture.setUp()
        try:
            with open(fixture.path, 'rb') as stream:
                messages = list(make_reader(stream).iter_messages(topics=['/test']))
            path = str(Path(fixture.temp.name) / 'unstamped.mcap')
            schema = messages[0][0]
            with open(path, 'wb') as stream:
                writer = Writer(stream)
                writer.start()
                sid = writer.register_schema(name=schema.name, encoding='protobuf', data=schema.data)
                yes = writer.register_channel(topic='/test', message_encoding='protobuf', schema_id=sid)
                no = writer.register_channel(topic='/test', message_encoding='protobuf', schema_id=sid,
                    metadata={'message_time_available': 'false'})
                for i, (_, _, msg) in enumerate(messages):
                    writer.add_message(channel_id=no if i == 1 else yes, log_time=msg.log_time,
                        publish_time=msg.publish_time, data=msg.data)
                writer.finish()
            fixture.path = path
            result = fixture.query(clock='message_time')
            self.assertEqual(result['message']['value'], 1)
            self.assertEqual(result['missing_message_time_count'], 1)
            self.assertIn('1 messages have no message_time', result['notice'])
            result = fixture.engine.query(dict(mcap=path, mode='snapshot', topic='/test', clock='publish_time', at_ns='2000000200'))
            self.assertEqual(result['message']['value'], 3)
            self.assertEqual(result['count'], 3)
            self.assertIsNone(result['message_time'])
        finally:
            fixture.tearDown()

    def test_generation_timestamp_is_not_replaced_by_publish_time(self):
        msg = LocalizationEstimate()
        msg.header.timestamp_sec = 1.25
        self.assertEqual(message_time_ns(msg), 1_250_000_000)
        msg.header.timestamp_sec = 0
        self.assertEqual(message_time_ns(msg), 0)
        msg.header.timestamp_sec = float("nan")
        with self.assertRaisesRegex(ValueError, "Invalid"):
            message_time_ns(msg)
        self.assertIsNone(message_time_ns(LocalizationEstimate()))

    def test_sensor_measurement_has_explicit_priority(self):
        msg = PointCloud()
        msg.header.timestamp_sec = 12
        msg.measurement_time = 10
        self.assertEqual(message_time_ns(msg), 10_000_000_000)

    def test_publish_window_maps_through_real_pairs_and_keeps_sparse_predecessor(self):
        index = {"/pose": {"first": 100, "publish_first": 1000,
                           "pairs": [[1000, 100], [2000, 110], [3000, 120]]}}
        result = plan(index, 2000, 2500, False, ["/pose"])
        self.assertEqual(result["seek_ns"], 2000)
        ranges = result["topic_import_ranges"]["/pose"]
        self.assertTrue(any(b <= 110 < e for b, e in ranges))
        self.assertTrue(any(b <= 120 < e for b, e in ranges))  # message-time predecessor
        self.assertEqual(result["end_ns"], 2500)

    def test_clock_names_are_exact_not_aliases(self):
        fixture = test_mcap_debug_query.DebugQueryTests()
        fixture.setUp()
        try:
            for name in ("publish_time", "message_time"):
                result = fixture.query(clock=name)
                self.assertIn("message_time", result)
                self.assertIn("publish_time", result)
                self.assertNotIn("log_time", result)
            for name in ("log_time", "message_log_time", "message_publish_time", "timestamp"):
                with self.assertRaisesRegex(ValueError, "Unsupported clock"):
                    fixture.query(clock=name)
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
