import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
from google.protobuf import descriptor_pb2
from google.protobuf.message_factory import GetMessageClass
from mcap.writer import Writer
from mcap_topic_debug import pool_from_file_descriptor_set, debug_string
from mcap_debug_query import DebugQueries, field_value


class DebugQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "test.mcap")
        fds = descriptor_pb2.FileDescriptorSet()
        file = fds.file.add(name="debug.proto", package="test", syntax="proto2")
        desc = file.message_type.add(name="Sample")
        desc.field.add(name="value", number=1, type=1, label=1)
        desc.field.add(name="enabled", number=2, type=8, label=1)
        schema = fds.SerializeToString()
        cls = GetMessageClass(pool_from_file_descriptor_set(schema).FindMessageTypeByName("test.Sample"))
        with open(self.path, "wb") as f:
            w = Writer(f); w.start()
            sid = w.register_schema(name="test.Sample", encoding="protobuf", data=schema)
            cid = w.register_channel(topic="/test", message_encoding="protobuf", schema_id=sid)
            other = w.register_channel(topic="/other", message_encoding="protobuf", schema_id=sid)
            w.register_channel(topic="/empty", message_encoding="protobuf", schema_id=sid)
            for i, value in enumerate([1, 3, 2]):
                msg = cls(value=value, enabled=i >= 1)
                w.add_message(channel_id=cid, log_time=(i+1)*1_000_000_000,
                              publish_time=(i+1)*1_000_000_000+100, data=msg.SerializeToString())
                w.add_message(channel_id=other, log_time=(i+1)*1_000_000_000,
                              publish_time=(i+1)*1_000_000_000+100, data=msg.SerializeToString())
            w.finish()
        self.engine = DebugQueries()

    def tearDown(self): self.temp.cleanup()

    def query(self, mode="snapshot", **extra):
        return self.engine.query(dict(mcap=self.path, mode=mode, topic="/test", at_ns="2000000000", **extra))

    def test_latest_at_and_explicit_clock(self):
        self.assertEqual(self.query()["message"]["value"], 3)
        self.assertEqual(self.query(clock="publish_time")["message"]["value"], 1)
        self.assertEqual(self.query()["previous_ns"], "1000000000")
        self.assertEqual(self.query()["next_ns"], "3000000000")

    def test_no_future_sample_and_zero_message_channel(self):
        self.engine.open(self.path)
        self.assertIsNone(self.engine.snapshot("/test", 1, "message_time")["sample_ns"])
        self.assertIn("zero messages", self.engine.snapshot("/empty", 4_000_000_000, "message_time")["notice"])

    def test_series_statistics_and_state_changes(self):
        kw = dict(begin_ns="1000000000", end_ns="3000000000", origin_ns="1000000000")
        result = self.query("series", signals=[dict(topic="/test",field="value")], **kw)["series"][0]
        self.assertEqual(result["points"], [[0,1],[1,3],[2,2]])
        self.assertEqual(result["stats"]["mean"], 2)
        states = self.query("states", signals=[dict(topic="/test",field="enabled")], **kw)["series"][0]
        self.assertEqual(states["points"], [[0,"False"],[1,"True"]])

    def test_missing_field_is_visible_error(self):
        result = self.query("series", signals=[dict(topic="/test",field="missing")],
                            begin_ns="1000000000", end_ns="3000000000", origin_ns="0")
        self.assertIn("Unknown field", result["series"][0]["error"])
        with self.assertRaisesRegex(ValueError,"Topic not present"):
            self.engine.snapshot("/absent", 4, "message_time")

    def test_cross_topic_series_preserves_requested_order(self):
        signals=[dict(topic="/test",field="value"),dict(topic="/other",field="value"),
                 dict(topic="/test",field="value")]
        result=self.query("series",signals=signals,begin_ns="1000000000",end_ns="3000000000",origin_ns="0")
        self.assertEqual([s["topic"] for s in result["series"]],[s["topic"] for s in signals])

    def test_index_reuse_and_memory_budget(self):
        self.query()
        first = self.engine.index("/test", "message_time")
        self.assertIs(first, self.engine.index("/test", "message_time"))
        self.engine.cache.clear()
        with patch("mcap_debug_query.MAX_TOPIC_BYTES",1):
            with self.assertRaisesRegex(ValueError,"budget"):
                self.engine.index("/test", "message_time")

    def test_legacy_debug_string_supported(self):
        result = debug_string(self.path,"/test",2_000_000_000)
        self.assertIn("value: 3",result["debug_string"])

    def test_health(self):
        result = self.query("health")
        self.assertEqual(result["selected"]["period_median_ms"],1000)
        self.assertEqual(result["selected"]["age_ms"],0)


if __name__ == "__main__": unittest.main()
