"""Field evidence must agree with the exact comparator, never replace it."""
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_service import TaskService
from bag_diff import RecordSchemas, difference_page, json_changes, message_difference


class DetailTests(unittest.TestCase):
    def test_presence_nested_repeated_maps_and_signed_zero(self):
        changes = list(json_changes(
            {"points": [{"z": 0.0}], "map": {"key": 4}, "empty": {}},
            {"points": [{"z": -0.0}, {"x": 2}], "map": {"key": 5}, "new": 0}))
        by_field = {d["field"]: d for d in changes}
        self.assertEqual(set(by_field), {"points[0].z", "points[1].x", "map.key", "empty", "new"})
        self.assertFalse(by_field["new"]["left_present"])
        self.assertEqual(by_field["new"]["right_value"], 0)
        self.assertFalse(by_field["empty"]["right_present"])
        self.assertFalse(by_field["points[1].x"]["left_present"])

    def schema(self):
        import sys
        sys.path.insert(0, "/opt/apollo/neo/python")
        from cyber.proto.proto_desc_pb2 import ProtoDesc
        from google.protobuf.descriptor_pb2 import FileDescriptorProto
        file = FileDescriptorProto(name="detail_fixture.proto", package="fixture", syntax="proto2")
        message = file.message_type.add(name="Message")
        message.field.add(name="count", number=1, type=3, label=1)
        message.field.add(name="z", number=2, type=1, label=1)
        tree = ProtoDesc(desc=file.SerializeToString())
        return {"op": "schema", "channel": "/test", "type": "fixture.Message",
                "proto_desc_b64": base64.b64encode(tree.SerializeToString()).decode()}

    def test_record_descriptor_presence_and_int64(self):
        registry = RecordSchemas()
        registry.add(self.schema())
        a = registry.decode("/test", b"")
        b = registry.decode("/test", b"")
        b.count = 9007199254740993
        b.z = 0
        pair = (("/test", 123, a.SerializeToString()), ("/test", 124, b.SerializeToString()))
        diff = message_difference(7, pair, pair, [registry, registry], [{"/test": 2}] * 2, True)
        self.assertEqual(diff["left"]["message"], {})
        self.assertEqual(diff["right"]["message"]["count"], "9007199254740993")
        self.assertIsNone(diff["left"]["message_time"])
        self.assertEqual(diff["metadata_changes"][0]["field"], "publish_time")
        self.assertEqual({c["field"] for c in diff["field_changes"]}, {"count", "z"})
        json.dumps(diff, allow_nan=False)

    def test_unknown_bytes_not_hidden(self):
        registry = RecordSchemas()
        registry.add(self.schema())
        pair = (("/test", 0, b""), ("/test", 0, b"\x18\x01"))
        diff = message_difference(0, pair, pair, [registry] * 2, [{"/test": 0}] * 2)
        self.assertIn("wire_difference", diff)
        self.assertEqual(diff["field_changes"], [])

    def test_invalid_schema_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "no protobuf schema"):
            RecordSchemas().decode("/missing", b"")
        registry = RecordSchemas()
        schema = self.schema()
        schema["proto_desc_b64"] = ""
        registry.add(schema)
        with self.assertRaisesRegex(ValueError, "empty protobuf schema"):
            registry.decode("/test", b"")

    def test_pages_cover_all_mismatches_and_close_decoders(self):
        schema = self.schema()
        closed = []
        def stream(path, schemas):
            schemas.add(schema)
            try:
                for i in range(25 if path == "left" else 24):
                    yield "/test", i, b"\x08\x01" if path == "left" else b"\x08\x02"
            finally:
                closed.append(path)
        with patch("bag_diff.messages", side_effect=stream):
            indexes = []
            for offset in (0, 10, 20):
                page = difference_page("left", "right", offset, 10, algorithm=False)
                indexes.extend(d["stream_index"] for d in page["diffs"])
            self.assertEqual(indexes, list(range(25)))
            self.assertFalse(page["has_more"])
            self.assertIsNone(page["diffs"][-1]["right"])
            self.assertEqual(len(closed), 6)
            self.assertEqual(difference_page("left", "right", 25, algorithm=False)["diffs"], [])

    def test_paging_rejects_invalid_inputs(self):
        for args in ({"offset": -1}, {"offset": True}, {"limit": 0}, {"limit": 11},
                     {"include_messages": "false"}):
            with self.assertRaises(ValueError):
                difference_page("left", "right", **args)

    def test_historical_task_query_does_not_rewrite_job_or_run(self):
        with tempfile.TemporaryDirectory() as directory:
            task = {"id": "old", "stage": "failed", "outputs": ["left", "right"]}
            path = Path(directory) / "jobs.json"
            path.write_text(json.dumps({"old": task}))
            service = TaskService(directory)
            try:
                before = path.read_bytes()
                with patch("task_service.difference_page", return_value={"diffs": []}) as page:
                    response = service.request({"action": "differences", "id": "old"})
                    page.assert_called_once_with("left", "right", offset=0, limit=1, include_messages=False)
                self.assertEqual(response["differences"]["task_id"], "old")
                self.assertNotIn("id", response)
                self.assertEqual(path.read_bytes(), before)
                for request in ({"id": "missing"}, {"id": "old", "comparison": -1},
                                {"id": "old", "comparison": True}):
                    with self.assertRaises(ValueError):
                        service.request({"action": "differences", **request})
                service.jobs["old"]["stage"] = "simulation_running"
                with self.assertRaisesRegex(ValueError, "finished task"):
                    service.request({"action": "differences", "id": "old"})
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
