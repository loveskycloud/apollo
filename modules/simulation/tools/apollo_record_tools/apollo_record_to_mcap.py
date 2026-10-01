#!/usr/bin/env python3
"""Convert Apollo CyberRT record files to MCAP via the C++ record dumper."""

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

from mcap.writer import Writer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("records", nargs="+", help="Apollo .record files")
    parser.add_argument("-o", "--output", required=True, help="Output .mcap")
    parser.add_argument("--tool", default=str(Path(__file__).parent / "bin" / "apollo_record_tool"))
    parser.add_argument("--begin-ns", type=int, default=0)
    parser.add_argument("--duration-ms", type=int, default=0)
    parser.add_argument("-c", "--channel", action="append", default=[])
    parser.add_argument("-k", "--skip-channel", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cmd = [args.tool, "dump-jsonl"]
    if args.begin_ns:
        cmd += ["--begin-ns", str(args.begin_ns)]
    if args.duration_ms:
        cmd += ["--duration-ms", str(args.duration_ms)]
    for channel in args.channel:
        cmd += ["-c", channel]
    for channel in args.skip_channel:
        cmd += ["-k", channel]
    cmd += args.records

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        bufsize=1,
    )

    schema_by_channel = {}
    channel_by_name = {}
    sequence_by_channel = {}

    with open(args.output, "wb") as output:
        writer = Writer(output)
        writer.start()

        assert proc.stdout is not None
        for line in proc.stdout:
            event = json.loads(line)
            if event["op"] == "schema":
                schema_id = writer.register_schema(
                    name=event["type"],
                    encoding="protobuf",
                    data=base64.b64decode(event["proto_desc_b64"]),
                )
                channel_id = writer.register_channel(
                    topic=event["channel"],
                    message_encoding="protobuf",
                    schema_id=schema_id,
                    metadata={"apollo_type": event["type"]},
                )
                schema_by_channel[event["channel"]] = schema_id
                channel_by_name[event["channel"]] = channel_id
                sequence_by_channel[event["channel"]] = 0
            elif event["op"] == "msg":
                channel = event["channel"]
                channel_id = channel_by_name[channel]
                sequence = sequence_by_channel[channel]
                timestamp = int(event["timestamp_ns"])
                writer.add_message(
                    channel_id=channel_id,
                    log_time=timestamp,
                    publish_time=timestamp,
                    sequence=sequence,
                    data=base64.b64decode(event["data_b64"]),
                )
                sequence_by_channel[channel] = sequence + 1

        writer.finish()

    ret = proc.wait()
    if ret != 0:
        return ret
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
