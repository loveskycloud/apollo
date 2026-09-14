"""Plan bounded playback windows with H.264 random-access prerequisites.

The index stores timestamps only, never image payloads. A new process can reuse
it between HTTP requests; file identity changes invalidate it automatically.
"""

import bisect
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

if __name__ == "__main__":
    from python_runtime import ensure_runtime
    ensure_runtime()

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

from mcap.reader import make_reader
from foxglove_schemas_protobuf.CompressedVideo_pb2 import CompressedVideo


def index_file(path):
    stat = path.stat()
    identity = [str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ino, 3]
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    cache = Path(tempfile.gettempdir()) / f"wm-video-index-{os.getuid()}-{key}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    result = {}
    with path.open("rb") as stream:
        reader = make_reader(stream)
        summary = reader.get_summary()
        if summary is None:
            raise ValueError("MCAP summary required for playback")
        for schema, channel, message in reader.iter_messages():
            item = result.setdefault(channel.topic, {"first": message.log_time, "pairs": [], "publish_first": message.publish_time})
            item["pairs"].append([message.publish_time, message.log_time])
            item["publish_first"] = min(item["publish_first"], message.publish_time)
            if schema is None or schema.name != "foxglove.CompressedVideo":
                continue
            item.setdefault("keys", [])
            item.setdefault("publish_keys", [])
            frame = CompressedVideo.FromString(message.data)
            if frame.format != "h264":
                raise ValueError(f"Unsupported random-access codec: {frame.format} ({channel.topic})")
            units = re.split(b"\x00\x00\x00?\x01", frame.data)[1:]
            types = {unit[0] & 31 for unit in units if unit}
            # Require a self-contained IDR, including decoder configuration.
            if {7, 8, 5}.issubset(types):
                item["keys"].append(message.log_time)
                item["publish_keys"].append(message.publish_time)
    for item in result.values():
        item["pairs"].sort()
        if "keys" in item:
            item["keys"] = sorted(set(item["keys"]))
            item["publish_keys"] = sorted(set(item["publish_keys"]))
    with tempfile.NamedTemporaryFile(mode="w", dir=cache.parent, delete=False) as out:
        json.dump(result, out)
        temporary = out.name
    os.replace(temporary, cache)
    return result


def clock_plan(index, begin, end, reset, topics):
    # Apollo bulky image channels are schema-only; their semantic counterparts
    # carry the actual compressed frames.
    topics = sorted(set(topics + ["/camera/" + t[len("/apollo/camera/"):].removesuffix("/compressed")
                                 for t in topics if t.startswith("/apollo/camera/")]))
    cameras = [t for t in topics if t in index and "keys" in index[t]]
    missing = [t for t in cameras if not index[t]["keys"]]
    if missing:
        raise ValueError("No self-contained H.264 keyframe: " + ", ".join(missing))
    first_samples = [index[t]["first"] for t in topics if t in index and t not in cameras]
    ready = max([index[t]["keys"][0] for t in cameras] + first_samples, default=begin)
    seek = max(begin, ready) if reset else begin
    end = max(end, seek + 500_000_000) if reset else end
    start = max(begin, seek - 1_000_000_000) if reset else begin
    for topic in cameras:
        keys = index[topic]["keys"]
        pos = bisect.bisect_right(keys, seek) - 1
        if pos >= 0:
            start = min(start, keys[pos])
    # P-frames before the first self-contained IDR cannot be decoded. Keep
    # this restriction local to the camera; lidar/pose windows are independent.
    topic_begin = {t: max(start, index[t]["keys"][0]) if t in cameras else start for t in topics}
    return {"import_begin_ns": start, "topic_begin_ns": topic_begin, "end_ns": end, "seek_ns": seek,
            "ready_ns": ready, "topics": topics}


def plan(index, begin, end, reset, topics):
    """Buffer both product clocks so switching clocks cannot reuse invalid coverage.

    The native MCAP reader indexes its storage slot log_time (= message_time).
    Map the publish-time window through actual timestamp pairs, never assume the
    two clocks agree or subtract an estimated latency. Payloads are not indexed.
    """
    message = clock_plan(index, begin, end, reset, topics)
    publish_index = {t: {"first": item["publish_first"],
                        **({"keys": item["publish_keys"]} if "keys" in item else {})}
                     for t, item in index.items()}
    publish = clock_plan(publish_index, begin, end, reset, topics)
    end = max(message["end_ns"], publish["end_ns"])
    ranges = {}
    for topic in message["topics"]:
        start = message["topic_begin_ns"][topic]
        intervals = [[start, end]] if start < end else []
        pairs = index.get(topic, {}).get("pairs", [])
        if pairs:
            # Include latest-at predecessor for sparse topics while paused.
            by_message = sorted(p[1] for p in pairs)
            pos = bisect.bisect_right(by_message, start) - 1
            if pos >= 0 and intervals:
                intervals[0][0] = min(start, by_message[pos])
            pub_start = publish["topic_begin_ns"][topic]
            left = max(0, bisect.bisect_right(pairs, [pub_start, 2**64]) - 1)
            right = bisect.bisect_left(pairs, [end, -1])
            times = [p[1] for p in pairs[left:right]]
            if times:
                intervals.append([min(times), max(times) + 1])
        merged = []
        for b, e in sorted(intervals):
            if merged and b <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([b, e])
        ranges[topic] = merged
    return {**publish, "end_ns": end, "topic_import_ranges": ranges}


if __name__ == "__main__":
    path, begin, end, reset, *topics = sys.argv[1:]
    print(json.dumps(plan(index_file(Path(path)), int(begin), int(end), reset == "1", topics)))
