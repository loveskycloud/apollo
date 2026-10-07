#!/usr/bin/env python3
"""On-demand MCAP topic decode → protobuf DebugString (Dreamview / cyber_monitor style).

Fails loudly on missing channel / bad schema / parse errors — no silent empty UI.

Usage:
  python3 mcap_topic_debug.py --mcap path.mcap --topic /apollo/localization/pose
  python3 mcap_topic_debug.py --mcap path.mcap --topic /apollo/localization/pose --at-ns 123
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

if __name__ == "__main__":
    from python_runtime import ensure_runtime
    ensure_runtime()

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
sys.path.insert(0, str(Path(os.environ.get("APOLLO_DISTRIBUTION_HOME", "/opt/apollo/neo")) / "python"))

from google.protobuf import descriptor_pb2, descriptor_pool
from google.protobuf.message_factory import GetMessageClass
from google.protobuf.text_format import MessageToString
from mcap.reader import make_reader


def pool_from_file_descriptor_set(schema_data: bytes) -> descriptor_pool.DescriptorPool:
    """MCAP semantic-mcap-v10+ stores google.protobuf.FileDescriptorSet."""
    if not schema_data:
        raise ValueError("empty protobuf schema data")
    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(schema_data)
    if not fds.file:
        raise ValueError(
            "schema data is not a non-empty FileDescriptorSet "
            "(re-convert with semantic-mcap-v10+ / Cyber ProtoDesc→FDS)"
        )
    pool = descriptor_pool.DescriptorPool()
    for fdp in fds.file:
        pool.Add(fdp)
    return pool


def debug_string(mcap_path: str, topic: str, at_ns: int | None) -> dict:
    with open(mcap_path, "rb") as fh:
        reader = make_reader(fh)
        summary = reader.get_summary()
        if summary is None:
            raise RuntimeError(f"MCAP has no summary: {mcap_path}")

        channel = None
        for ch in summary.channels.values():
            if ch.topic == topic:
                channel = ch
                break
        if channel is None:
            known = sorted(ch.topic for ch in summary.channels.values())
            raise RuntimeError(
                f"topic not in MCAP: {topic!r}; known={known[:20]}{'…' if len(known) > 20 else ''}"
            )

        schema = summary.schemas.get(channel.schema_id) if channel.schema_id else None
        if schema is None:
            raise RuntimeError(f"topic {topic!r} has no schema in MCAP")
        if schema.encoding != "protobuf":
            raise RuntimeError(
                f"topic {topic!r} schema encoding={schema.encoding!r} "
                f"(expected protobuf; refuse raw/silent fallback)"
            )

        pool = pool_from_file_descriptor_set(schema.data)
        try:
            desc = pool.FindMessageTypeByName(schema.name)
        except KeyError as err:
            raise RuntimeError(
                f"message type {schema.name!r} not in descriptor pool for {topic!r}"
            ) from err

        msg_cls = GetMessageClass(desc)
        chosen = None
        best_delta = None
        for _schema, _channel, message in reader.iter_messages(topics=[topic]):
            if at_ns is not None and _channel.metadata.get("message_time_available") == "false":
                raise RuntimeError(f"{topic}: message_time unavailable; use the publish_time Topic inspector")
            if at_ns is None:
                chosen = message
                continue
            # Match windowed playback. Never display a future message as the current frame.
            t = message.log_time
            if int(t) > int(at_ns):
                continue
            delta = abs(int(t) - int(at_ns))
            le = int(t) <= int(at_ns)
            if chosen is None:
                chosen = message
                best_delta = (0 if le else 1, delta)
                continue
            key = (0 if le else 1, delta)
            if key < best_delta:
                chosen = message
                best_delta = key

        if chosen is None:
            raise RuntimeError(
                f"topic {topic!r} has schema but zero messages in MCAP "
                f"(schemas-only convert? re-convert with payloads / semantic-mcap-v10)"
            )

        msg = msg_cls()
        try:
            msg.ParseFromString(chosen.data)
        except Exception as err:  # noqa: BLE001 — surface decode failure
            raise RuntimeError(
                f"protobuf ParseFromString failed for {topic!r} type={schema.name!r}: {err}"
            ) from err

        text = MessageToString(msg, as_utf8=True)
        # Hard budget: never freeze UI with multi-MB dumps — fail loud if too big.
        max_chars = 200_000
        if len(text) > max_chars:
            raise RuntimeError(
                f"DebugString for {topic!r} is {len(text)} chars "
                f"(budget {max_chars}); refuse silent truncate"
            )
        return {
            "status": "ok",
            "topic": topic,
            "type": schema.name,
            "message_time": None if summary.channels[chosen.channel_id].metadata.get("message_time_available") == "false" else chosen.log_time,
            "publish_time": chosen.publish_time,
            "at_ns": at_ns,
            "debug_string": text,
        }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mcap", required=True)
    p.add_argument("--topic", required=True)
    p.add_argument(
        "--at-ns",
        type=int,
        default=None,
        help="Prefer message nearest to this publish/log time (ns)",
    )
    args = p.parse_args()
    try:
        out = debug_string(args.mcap, args.topic, args.at_ns)
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 — surface to host API
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
