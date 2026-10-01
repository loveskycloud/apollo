#!/usr/bin/env python3
"""Compare real Cyber record streams, including order, timestamps and payloads."""
import argparse
import base64
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import math
from decimal import Decimal

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

ROOT = Path(__file__).resolve().parents[2]  # simulation/
TOOL = ROOT / "tools/apollo_record_tools/bin/apollo_record_tool"

EXCLUDED_WALL_FIELDS = [
    "planning.latency_stats.total_time_ms", "planning.latency_stats.init_frame_time_ms",
    "planning.latency_stats.task_stats[].time_ms", "control.latency_stats.total_time_ms",
    "control.latency_stats.controller_time_ms", "control.latency_stats.total_time_exceeded",
]

def algorithm_payload(channel, payload):
    """Exact protobuf values, canonical map order, explicitly excluding profiling.

    Preserve trajectories, commands, status, header timestamps and unknown fields.
    This never rewrites the recorded evidence, and is not a numerical tolerance.
    """
    if channel not in ("/apollo/planning", "/apollo/control"):
        return payload
    if "/opt/apollo/neo/python" not in sys.path:
        sys.path.insert(0, "/opt/apollo/neo/python")
    if channel == "/apollo/planning":
        from modules.common_msgs.planning_msgs.planning_pb2 import ADCTrajectory
        message = ADCTrajectory.FromString(payload)
        if message.HasField("latency_stats"):
            for name in ("total_time_ms", "init_frame_time_ms"):
                message.latency_stats.ClearField(name)
            for task in message.latency_stats.task_stats:
                task.ClearField("time_ms")
    else:
        from modules.common_msgs.control_msgs.control_cmd_pb2 import ControlCommand
        message = ControlCommand.FromString(payload)
        if message.HasField("latency_stats"):
            for name in ("total_time_ms", "controller_time_ms", "total_time_exceeded"):
                message.latency_stats.ClearField(name)
    def canonical_maps(msg):
        for field, value in msg.ListFields():
            if field.type != field.TYPE_MESSAGE:
                continue
            if field.label != field.LABEL_REPEATED:
                canonical_maps(value)
            elif field.message_type.GetOptions().map_entry:
                if hasattr(value, "items"):
                    for item in value.values():
                        if hasattr(item, "ListFields"): canonical_maps(item)
                else:
                    entries = sorted(value, key=lambda entry: entry.key)
                    copies = [type(entry).FromString(entry.SerializeToString()) for entry in entries]
                    del value[:]
                    value.extend(copies)
            else:
                for child in value: canonical_maps(child)
    canonical_maps(message)
    return message.SerializeToString(deterministic=True)

class RecordSchemas:
    """Decode with the descriptors saved in each record, not today's .proto files."""
    def __init__(self):
        self.schemas = {}
        self.classes = {}

    def add(self, event):
        self.schemas[event["channel"]] = event

    def decode(self, channel, payload):
        from google.protobuf import descriptor_pool, message_factory
        if channel not in self.classes:
            if "/opt/apollo/neo/python" not in sys.path:
                sys.path.insert(0, "/opt/apollo/neo/python")
            from cyber.proto.proto_desc_pb2 import ProtoDesc
            if channel not in self.schemas:
                raise ValueError(f"Record has no protobuf schema for {channel}")
            schema = self.schemas[channel]
            raw = base64.b64decode(schema["proto_desc_b64"], validate=True)
            if not raw:
                raise ValueError(f"Record has an empty protobuf schema for {channel}")
            tree = ProtoDesc.FromString(raw)
            pool = descriptor_pool.DescriptorPool()
            def add(node):
                for dependency in node.dependencies:
                    add(dependency)
                if node.desc:
                    pool.AddSerializedFile(node.desc)
            add(tree)
            descriptor = pool.FindMessageTypeByName(schema["type"])
            # Apollo system Python has protobuf 3; the viewer venv has protobuf 4.
            self.classes[channel] = (message_factory.GetMessageClass(descriptor)
                                     if hasattr(message_factory, "GetMessageClass") else
                                     message_factory.MessageFactory(pool).GetPrototype(descriptor))
        return self.classes[channel].FromString(payload)


def messages(path, tool=TOOL, schemas=None):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Record does not exist: {path}")
    diagnostics = tempfile.TemporaryFile(mode="w+t")
    proc = subprocess.Popen([str(tool), "dump-jsonl", str(path)],
                            stdout=subprocess.PIPE, stderr=diagnostics, text=True)
    count = 0
    try:
        for line in proc.stdout:
            event = json.loads(line)
            if event["op"] == "schema" and schemas is not None:
                schemas.add(event)
            if event["op"] == "msg":
                count += 1
                yield (event["channel"], int(event["timestamp_ns"]),
                       base64.b64decode(event["data_b64"], validate=True))
        if proc.wait() != 0:
            diagnostics.seek(0)
            errors = diagnostics.read()
            raise RuntimeError(f"Record decoder failed: {errors[-4000:]}")
        if not count:
            raise ValueError(f"Record is empty: {path}")
    finally:
        if proc.poll() is None:
            proc.terminate()
        proc.wait()
        proc.stdout.close()
        diagnostics.close()


_MISSING = object()


def json_changes(left, right, path=""):
    """Presence-aware leaf differences in official ProtoJSON (no default filling)."""
    original_left, original_right = left, right
    if isinstance(left, dict) and (isinstance(right, dict) or right is _MISSING):
        right = {} if right is _MISSING else right
        for key in sorted(left.keys() | right.keys()):
            yield from json_changes(left.get(key, _MISSING), right.get(key, _MISSING),
                                    f"{path}.{key}" if path else key)
        if left or right:
            return
    elif isinstance(right, dict) and left is _MISSING:
        yield from json_changes({}, right, path)
        if right:
            return
    elif isinstance(left, list) and (isinstance(right, list) or right is _MISSING):
        right = [] if right is _MISSING else right
        for index in range(max(len(left), len(right))):
            yield from json_changes(left[index] if index < len(left) else _MISSING,
                                    right[index] if index < len(right) else _MISSING,
                                    f"{path}[{index}]")
        if left or right:
            return
    elif isinstance(right, list) and left is _MISSING:
        yield from json_changes([], right, path)
        if right:
            return
    left, right = original_left, original_right
    # Preserve IEEE signed zero too; ProtoJSON keeps -0.0 but Python == does not.
    signed_zero = (isinstance(left, float) and isinstance(right, float) and
                   left == right == 0 and math.copysign(1, left) != math.copysign(1, right))
    if left != right or signed_zero:
        yield {"field": path, "left_present": left is not _MISSING,
               "right_present": right is not _MISSING,
               "left_value": None if left is _MISSING else left,
               "right_value": None if right is _MISSING else right}


def message_difference(index, pair, compared, schemas, topic_indices, include_messages=False):
    from google.protobuf.json_format import MessageToDict
    sides, values = [], []
    for i, item in enumerate(pair):
        if item is None:
            sides.append(None)
            values.append(_MISSING)
            continue
        channel, stamp, payload = item
        raw = schemas[i].decode(channel, payload)
        value = MessageToDict(schemas[i].decode(channel, compared[i][2]),
                              preserving_proto_field_name=True)
        raw_json = MessageToDict(raw, preserving_proto_field_name=True)
        message_time = raw_json.get("header", {}).get("timestamp_sec")
        side = {"topic": channel, "topic_frame": topic_indices[i][channel],
                "publish_time": str(stamp),
                "message_time": (str(int(Decimal(str(message_time)) * 1_000_000_000))
                                 if message_time is not None else None),
                "message_type": raw.DESCRIPTOR.full_name,
                "raw_sha256": hashlib.sha256(payload).hexdigest()}
        if include_messages:
            side["message"] = raw_json
        sides.append(side)
        values.append(value)
    fields = list(json_changes(*values))
    metadata = list(json_changes(
        {k: sides[0][k] for k in ("topic", "publish_time", "message_type")} if sides[0] else _MISSING,
        {k: sides[1][k] for k in ("topic", "publish_time", "message_type")} if sides[1] else _MISSING))
    result = {"stream_index": index, "left": sides[0], "right": sides[1],
              "metadata_changes": metadata, "field_changes": fields}
    if pair[0] and pair[1] and compared[0][2] != compared[1][2] and not fields:
        # Unknown fields / wire representation cannot be attributed to a named
        # JSON field. Expose the evidence explicitly, never silently call it equal.
        result["wire_difference"] = {
            "reason": "Protobuf bytes differ but named JSON fields are equal (unknown fields or wire representation)",
            "left_base64": base64.b64encode(compared[0][2]).decode(),
            "right_base64": base64.b64encode(compared[1][2]).decode()}
    return result


def difference_page(left, right, offset=0, limit=1, include_messages=False, algorithm=True):
    """Page every mismatch, not only the first 20 retained in the summary.

    Stream indexes and topic frame indexes are zero based. Clock strings are ns;
    message_time is the header timestamp_sec expressed in ns, null if absent.
    Closing a partially consumed page always terminates both decoder subprocesses.
    """
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("Difference offset must be nonnegative; limit must be 1..10")
    if type(include_messages) is not bool:
        raise ValueError("include_messages must be boolean")
    schemas = [RecordSchemas(), RecordSchemas()]
    streams = [messages(path, schemas=schema) for path, schema in zip((left, right), schemas)]
    topic_indices = [{}, {}]
    found, diffs, has_more = 0, [], False
    try:
        for index, pair in enumerate(itertools.zip_longest(*streams)):
            for i, item in enumerate(pair):
                if item:
                    topic_indices[i][item[0]] = topic_indices[i].get(item[0], -1) + 1
            compared = tuple(None if item is None else
                             (item[0], item[1], algorithm_payload(item[0], item[2]))
                             for item in pair) if algorithm else pair
            if compared[0] == compared[1]:
                continue
            found += 1
            if found <= offset:
                continue
            if len(diffs) == limit:
                has_more = True
                break
            diffs.append(message_difference(index, pair, compared, schemas, topic_indices,
                                            include_messages))
    finally:
        for stream in streams:
            stream.close()
    return {"diffs": diffs, "offset": offset, "next_offset": offset + len(diffs),
            "has_more": has_more, "timestamp_unit": "nanoseconds",
            "index_base": 0, "include_messages": include_messages,
            "excluded_wall_fields": EXCLUDED_WALL_FIELDS if algorithm else []}

def compare(left, right, algorithm=False):
    counts = [0, 0]
    diffs = []
    hashes = [hashlib.sha256(), hashlib.sha256()]
    different = 0
    raw_different = 0
    for index, pair in enumerate(itertools.zip_longest(messages(left), messages(right))):
        for i, item in enumerate(pair):
            if item is not None:
                counts[i] += 1
                channel, stamp, payload = item
                hashes[i].update(json.dumps([channel, str(stamp), len(payload)]).encode())
                hashes[i].update(payload)
        raw_different += pair[0] != pair[1]
        compared = tuple(None if item is None else (item[0], item[1], algorithm_payload(item[0], item[2]))
                         for item in pair) if algorithm else pair
        if compared[0] != compared[1]:
            different += 1
            if len(diffs) < 20:
                def describe(item):
                    return None if item is None else {"channel": item[0], "time_ns": str(item[1]),
                        "sha256": hashlib.sha256(item[2]).hexdigest()}
                diffs.append({"index": index, "left": describe(pair[0]), "right": describe(pair[1])})
    return {"result": "PASS" if different == 0 else "FAIL", "comparison": "exact message order + timestamp + protobuf values (profiling excluded, maps canonicalized)" if algorithm else "exact message order + timestamp + payload",
            "excluded_wall_fields": EXCLUDED_WALL_FIELDS if algorithm else [],
            "raw_result": "PASS" if raw_different == 0 else "FAIL", "raw_different_messages": raw_different,
            "left_count": counts[0], "right_count": counts[1], "different_messages": different,
            "left_sha256": hashes[0].hexdigest(), "right_sha256": hashes[1].hexdigest(),
            "diffs": diffs, "diffs_truncated": different > len(diffs)}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--left", required=True)
    parser.add_argument("--right", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--details", action="store_true", help="Write a JSON list of message/field differences")
    parser.add_argument("--offset", type=int, default=0, help="Zero-based difference index")
    parser.add_argument("--limit", type=int, default=1, help="Messages per detail page (1..10)")
    parser.add_argument("--include-messages", action="store_true", help="Include both complete ProtoJSON messages")
    parser.add_argument("--algorithm", action="store_true", help="Exclude only listed wall-time profiling fields")
    args = parser.parse_args()
    if args.details:
        page = difference_page(args.left, args.right, args.offset, args.limit,
                               args.include_messages, args.algorithm)
        Path(args.output).write_text(json.dumps(page["diffs"], indent=2, allow_nan=False))
        print(json.dumps({key: value for key, value in page.items() if key != "diffs"}))
        return 0
    try:
        result = compare(args.left, args.right, algorithm=args.algorithm)
    except Exception as error:
        result = {"result": "ERROR", "message": str(error)}
    Path(args.output).write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    return 0 if result["result"] == "PASS" else 1

if __name__ == "__main__":
    sys.exit(main())
