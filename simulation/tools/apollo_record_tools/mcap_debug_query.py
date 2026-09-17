#!/usr/bin/env python3
"""Indexed, read-only AD debug queries. JSON-lines worker owned by rerun-cli.

All sample selection uses an explicit clock and latest-at, never nearest/future.
Raw topic indexes are LRU bounded; only requested fields cross into the browser.
"""
from __future__ import annotations

import bisect
from collections import OrderedDict
import json
import math
import os
import re
import statistics
import sys

if __name__ == "__main__":
    from python_runtime import ensure_runtime
    ensure_runtime()

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
from google.protobuf.json_format import MessageToDict
from google.protobuf.message_factory import GetMessageClass
from mcap.reader import make_reader
from mcap_topic_debug import pool_from_file_descriptor_set

MAX_TOPIC_BYTES = 128 * 1024 * 1024
MAX_CACHE_BYTES = 256 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def field_value(message, path):
    """Protobuf field path, including explicit repeated indexes (never eval)."""
    value = message
    for token in path.split("."):
        match = re.fullmatch(r"([A-Za-z_]\w*)(?:\[(\d+)\])?", token)
        if not match:
            raise ValueError(f"Invalid field path: {path}")
        name, index = match.groups()
        desc = value.DESCRIPTOR.fields_by_name.get(name)
        if desc is None:
            raise ValueError(f"Unknown field: {path}")
        if desc.has_presence and not value.HasField(name):
            return None
        value = getattr(value, name)
        if index is not None:
            if int(index) >= len(value):
                return None
            value = value[int(index)]
        if desc.enum_type is not None and index is None:
            return desc.enum_type.values_by_number[int(value)].name
    if isinstance(value, (float, int, bool, str)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    raise ValueError(f"Field is not a scalar; select a leaf/index: {path}")


def leaves(value, prefix=""):
    result = []
    if isinstance(value, dict):
        for key, child in value.items():
            result.extend(leaves(child, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            result.extend(leaves(child, f"{prefix}[{i}]"))
    else:
        result.append({"path": prefix, "value": value})
    if len(result) > 12000:
        raise ValueError("Message exceeds 12000 leaf display budget; select a smaller topic")
    return result


class DebugQueries:
    def __init__(self):
        self.identity = None
        self.path = ""
        self.summary = None
        self.cache = OrderedDict()
        self.cache_bytes = 0

    def open(self, path):
        st = os.stat(path)
        identity = (os.path.realpath(path), st.st_size, st.st_mtime_ns)
        if identity != self.identity:
            with open(path, "rb") as f:
                summary = make_reader(f).get_summary()
            if summary is None or summary.statistics is None:
                raise ValueError("MCAP summary/statistics required")
            self.identity, self.path, self.summary = identity, path, summary
            self.cache.clear()
            self.cache_bytes = 0

    def index(self, topic, clock):
        key = (topic, clock)
        if key in self.cache:
            self.cache.move_to_end(key)
            if self.cache[key].get("missing_message_time_count", 0) and hasattr(self, "clock_warnings"):
                self.clock_warnings[topic] = self.cache[key]["missing_message_time_count"]
            return self.cache[key]
        channels = [c for c in self.summary.channels.values() if c.topic == topic]
        if not channels:
            raise ValueError(f"Topic not present: {topic}")
        if len({c.schema_id for c in channels}) != 1:
            raise ValueError(f"Multiple schemas for topic: {topic}")
        schema = self.summary.schemas.get(channels[0].schema_id)
        if schema is None or schema.encoding != "protobuf":
            raise ValueError(f"Protobuf schema required: {topic}")
        cls = GetMessageClass(pool_from_file_descriptor_set(schema.data).FindMessageTypeByName(schema.name))
        rows, size, missing_time = [], 0, 0
        with open(self.path, "rb") as f:
            for _, channel, msg in make_reader(f).iter_messages(topics=[topic]):
                if clock == "message_time" and channel.metadata.get("message_time_available") == "false":
                    missing_time += 1
                    continue
                size += len(msg.data) + 64
                if size > MAX_TOPIC_BYTES:
                    raise ValueError(f"Topic index exceeds 128 MiB budget: {topic}; use the 3D/Image viewer for bulk sensors")
                t = msg.log_time if clock == "message_time" else msg.publish_time
                message_time = None if channel.metadata.get("message_time_available") == "false" else msg.log_time
                rows.append((t, message_time, msg.publish_time, msg.data))
        rows.sort(key=lambda r: r[0])
        if missing_time and not rows:
            raise ValueError(f"{topic}: message_time unavailable ({missing_time} messages have no generation timestamp); select publish_time")
        while self.cache and self.cache_bytes + size > MAX_CACHE_BYTES:
            _, removed = self.cache.popitem(last=False)
            self.cache_bytes -= removed["bytes"]
        result = {"rows": rows, "times": [r[0] for r in rows], "cls": cls,
                  "type": schema.name, "bytes": size, "missing_message_time_count": missing_time}
        if missing_time and hasattr(self, "clock_warnings"):
            self.clock_warnings[topic] = missing_time
        self.cache[key] = result
        self.cache_bytes += size
        return result

    @staticmethod
    def decode(index, row):
        message = index["cls"]()
        message.ParseFromString(row[3])
        return message

    def snapshot(self, topic, at, clock):
        index = self.index(topic, clock)
        i = bisect.bisect_right(index["times"], at) - 1
        result = {"topic": topic, "type": index["type"], "count": len(index["rows"]),
                  "missing_message_time_count": index["missing_message_time_count"],
                  "sample_ns": None, "previous_ns": None, "next_ns": None, "fields": []}
        if i + 1 < len(index["rows"]):
            result["next_ns"] = str(index["times"][i + 1])
        if i < 0:
            result["notice"] = "No message at or before playhead" if index["rows"] else "Schema only: zero messages in this bag"
            return result
        row = index["rows"][i]
        msg = self.decode(index, row)
        data = MessageToDict(msg, preserving_proto_field_name=True)
        if len(json.dumps(data)) > 1_000_000:
            raise ValueError("Message exceeds 1 MB inspector budget; use a semantic sensor panel")
        result.update(sample_ns=str(row[0]), message_time=None if row[1] is None else str(row[1]), publish_time=str(row[2]),
                      age_ms=(at - row[0]) / 1e6, message=data, fields=leaves(data))
        if i > 0:
            result["previous_ns"] = str(index["times"][i - 1])
        return result

    def series(self, expressions, begin, end, origin, clock, states=False):
        if not 0 < len(expressions) <= 24:
            raise ValueError("Select between 1 and 24 signals")
        if end <= begin or end - begin > 120_000_000_000:
            raise ValueError("Query window must be positive and at most 120 seconds")
        output = []
        grouped = {}
        for order, expression in enumerate(expressions):
            grouped.setdefault(expression["topic"], []).append({**expression, "_order": order})
        for topic, group in grouped.items():
            index = self.index(topic, clock)
            start = bisect.bisect_left(index["times"], begin)
            stop = bisect.bisect_right(index["times"], end)
            if states and start > 0:
                start -= 1  # initial state at left edge
            series = [{**e, "points": [], "times_ns": [], "missing": 0} for e in group]
            for row in index["rows"][start:stop]:
                msg = self.decode(index, row)
                for s in series:
                    if "error" in s:
                        continue
                    try:
                        val = field_value(msg, s["field"])
                        if val is None:
                            s["missing"] += 1
                            continue
                        if states:
                            if s["points"] and s["points"][-1][1] == str(val):
                                continue
                            val = str(val)
                        elif not isinstance(val, (int, float)) or isinstance(val, bool):
                            raise ValueError("Not a numeric signal; use State transitions")
                        s["points"].append([(row[0] - origin) / 1e9, val])
                        s["times_ns"].append(str(row[0]))
                    except (ValueError, AttributeError) as error:
                        s["error"] = str(error)
            for s in series:
                s["source_count"] = stop - start
                if not s["points"] and "error" not in s:
                    s["error"] = "No populated samples for this field in the requested window"
                if not states and s["points"]:
                    values = [p[1] for p in s["points"]]
                    s["stats"] = {"min": min(values), "max": max(values),
                                  "mean": statistics.fmean(values),
                                  "rms": math.sqrt(statistics.fmean(v * v for v in values))}
                if len(s["points"]) > 12000:
                    raise ValueError("Signal exceeds 12000 point budget; narrow the query window (no silent downsampling)")
            output.extend(series)
        output.sort(key=lambda s: s["_order"])
        for s in output:
            del s["_order"]
        return {"series": output}

    def health(self, topic, at, clock):
        stat = self.summary.statistics
        seconds = (stat.message_end_time - stat.message_start_time) / 1e9
        topics = []
        for c in sorted(self.summary.channels.values(), key=lambda c: c.topic):
            count = stat.channel_message_counts.get(c.id, 0)
            schema = self.summary.schemas.get(c.schema_id)
            topics.append({"topic": c.topic, "type": schema.name if schema else "No schema",
                           "count": count, "bag_average_hz": count / seconds if seconds else 0})
        result = {"topics": topics, "duration_s": seconds}
        if topic:
            try:
                index = self.index(topic, clock)
            except ValueError as error:
                result["selected_error"] = str(error)
                return result
            times = index["times"]
            periods = [(b - a) / 1e6 for a, b in zip(times, times[1:])]
            i = bisect.bisect_right(times, at) - 1
            result["selected"] = {"topic": topic, "count": len(times),
                "age_ms": (at - times[i]) / 1e6 if i >= 0 else None,
                "sample_ns": str(times[i]) if i >= 0 else None}
            if periods:
                median = statistics.median(periods)
                result["selected"].update(period_min_ms=min(periods), period_median_ms=median,
                    period_max_ms=max(periods), duplicates=sum(p == 0 for p in periods),
                    gaps_over_2x_median=sum(p > median * 2 for p in periods))
        return result

    def trajectory(self, at, clock):
        index = self.index("/apollo/planning", clock)
        i = bisect.bisect_right(index["times"], at) - 1
        if i < 0:
            raise ValueError("No planning trajectory at or before playhead")
        msg = self.decode(index, index["rows"][i])
        planned = []
        for point in msg.trajectory_point:
            if not point.HasField("path_point") or not all(point.path_point.HasField(f) for f in ("x", "y")):
                raise ValueError("Planning trajectory point is missing path_point.x/y")
            planned.append([point.path_point.x, point.path_point.y])
        if not planned:
            raise ValueError("Planning message has no trajectory_point")
        loc = self.index("/apollo/localization/pose", clock)
        a = bisect.bisect_left(loc["times"], at - 10_000_000_000)
        b = bisect.bisect_right(loc["times"], at)
        actual = []
        for row in loc["rows"][a:b]:
            m = self.decode(loc, row)
            if not m.HasField("pose") or not m.pose.HasField("position"):
                raise ValueError("Localization sample is missing pose.position")
            if not all(m.pose.position.HasField(f) for f in ("x", "y")):
                raise ValueError("Localization sample is missing pose.position.x/y")
            actual.append([m.pose.position.x, m.pose.position.y])
        return {"planned": planned, "actual": actual, "sample_ns": str(index["times"][i]),
                "age_ms": (at - index["times"][i]) / 1e6,
                "localization_ns": str(loc["times"][b-1]) if b > a else None,
                "coordinate_frame": "Apollo map XY (meters); no additional TF applied"}

    def query(self, request):
        self.clock_warnings = {}
        self.open(request["mcap"])
        clock = request.get("clock", "message_time")
        if clock not in ("message_time", "publish_time"):
            raise ValueError(f"Unsupported clock: {clock}")
        at = int(request["at_ns"])
        mode = request["mode"]
        if mode == "dashboard_window":
            data = self.dashboard_window(at, clock)
        elif mode == "snapshot":
            data = self.snapshot(request["topic"], at, clock)
        elif mode in ("series", "states"):
            data = self.series(request["signals"], int(request["begin_ns"]), int(request["end_ns"]),
                               int(request["origin_ns"]), clock, mode == "states")
        elif mode == "health":
            data = self.health(request.get("topic", ""), at, clock)
        elif mode == "profile":
            index = self.index("/apollo/planning", clock)
            i = bisect.bisect_right(index["times"], at) - 1
            if i < 0:
                data = {"notice": "No planning sample at or before the playhead"}
            else:
                message = self.decode(index, index["rows"][i])
                data = {"sample_ns": str(index["times"][i]), "age_ms": (at-index["times"][i])/1e6}
                for field in ("v", "a", "kappa"):
                    values = []
                    for point in message.trajectory_point:
                        parent = point.path_point if field == "kappa" else point
                        if point.HasField("relative_time") and parent.HasField(field):
                            values.append([point.relative_time, getattr(parent, field)])
                    data[field] = values
                if not all(data[field] for field in ("v", "a", "kappa")):
                    data["notice"] = "Some profile fields are absent in this planning sample"
        elif mode == "trajectory":
            data = self.trajectory(at, clock)
        else:
            raise ValueError(f"Unknown query mode: {mode}")
        if self.clock_warnings:
            warning = "; ".join(f"{topic}: {count} messages have no message_time (available on publish_time)"
                                for topic, count in self.clock_warnings.items())
            data["notice"] = (data.get("notice", "") + " " + warning).strip()
        return {"status": "ok", "at_ns": str(at), "clock": clock, "mode": mode, **data}

    def dashboard_window(self, at, clock):
        """Small prefetched scalar window; client selects exact latest-at every paint."""
        begin, end = at - 1_000_000_000, at + 5_000_000_000
        topics = {
            "chassis": ("/apollo/canbus/chassis", ["speed_mps", "steering_percentage",
                "throttle_percentage", "brake_percentage", "driving_mode", "gear_location",
                "parking_brake", "signal.turn_signal"]),
            "traffic": ("/apollo/perception/traffic_light", []),
        }
        present = {c.topic for c in self.summary.channels.values()}
        output = {}
        for name, (topic, fields) in topics.items():
            entry = {"topic": topic, "rows": [], "notice": None}
            output[name] = entry
            if topic not in present:
                entry["notice"] = "Topic not present in this bag"
                continue
            index = self.index(topic, clock)
            start = max(0, bisect.bisect_right(index["times"], begin) - 1)
            stop = bisect.bisect_right(index["times"], end)
            if stop - start > 10000:
                raise ValueError(f"Dashboard exceeds 10000 sample budget: {topic}")
            for row in index["rows"][start:stop]:
                msg = self.decode(index, row)
                sample = {"sample_ns": str(row[0])}
                for field in fields:
                    sample[field] = field_value(msg, field)
                if name == "traffic":
                    if len(msg.traffic_light) > 32:
                        raise ValueError("Dashboard exceeds 32 traffic light display budget")
                    sample["lights"] = [{key: field_value(light, key)
                        for key in ("id", "color", "confidence", "blink")}
                        for light in msg.traffic_light]
                entry["rows"].append(sample)
        return {"begin_ns": str(begin), "end_ns": str(end), "streams": output}


def main():
    engine = DebugQueries()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            result = engine.query(request)
            text = json.dumps(result, ensure_ascii=False, allow_nan=False)
            if len(text.encode()) > MAX_RESPONSE_BYTES:
                raise ValueError("Query exceeds 4 MiB response budget; narrow fields/time window")
        except Exception as error:
            text = json.dumps({"status": "error", "message": str(error)})
        print(text, flush=True)


if __name__ == "__main__":
    main()
