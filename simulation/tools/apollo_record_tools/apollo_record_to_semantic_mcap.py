#!/usr/bin/env python3
"""Convert Apollo CyberRT .record → MCAP for Rerun/Foxglove.

Preserves **all** Apollo bag topics (exact names, incl. leading '/') as protobuf
passthrough channels, and additionally emits Foxglove semantic overlays used by
the AD layouts (/tf, /vehicle, /lidar/up/points, /camera/<name>).

Timelines (MCAP envelope → Rerun):
  - publish_time  → MCAP publish_time → Rerun `publish_time` (default)
  - message_time  → MCAP log_time     → Rerun `message_time`

Acceleration:
  - Thread pool for heavy point-cloud packing (default: auto workers)
  - Optional GPU path (CuPy or PyTorch CUDA) for point filtering/packing when available
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import struct
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

if __name__ == "__main__":
    from python_runtime import ensure_runtime
    ensure_runtime()

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
sys.path.insert(0, "/opt/apollo/neo/python")

import numpy as np
from google.protobuf.descriptor_pb2 import FileDescriptorSet
from mcap.writer import Writer as McapWriter

from foxglove_schemas_protobuf.CompressedVideo_pb2 import CompressedVideo
from foxglove_schemas_protobuf.FrameTransform_pb2 import FrameTransform
from foxglove_schemas_protobuf.PackedElementField_pb2 import PackedElementField
from foxglove_schemas_protobuf.PointCloud_pb2 import PointCloud
from foxglove_schemas_protobuf.PoseInFrame_pb2 import PoseInFrame

from modules.common_msgs.localization_msgs import localization_pb2
from modules.common_msgs.sensor_msgs import pointcloud_pb2
from ad_scene import SceneContext, PlannedPath
from hd_map import MapMesh, resolve_map, load_map, build_map_meshes
from google.protobuf.message_factory import GetMessageClass
from mcap_topic_debug import pool_from_file_descriptor_set

CONVERTER_VERSION = "semantic-mcap-v14"


def message_time_ns(message):
    """Extract a declared generation/measurement time; never substitute bag time."""
    fields = message.DESCRIPTOR.fields_by_name
    if not any(name in fields for name in ("header", "measurement_time", "timestamp_sec_start", "transforms")):
        return None  # Schema explicitly has no generation clock; publish-only channel.
    for name in ("measurement_time", "timestamp_sec_start"):
        if name in fields and message.HasField(name):
            value = getattr(message, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid {message.DESCRIPTOR.full_name}.{name}")
            if value > 0:
                return seconds_to_ns(value)
    if "header" in fields and message.HasField("header"):
        header = message.header
        if "timestamp_sec" in header.DESCRIPTOR.fields_by_name and header.HasField("timestamp_sec"):
            value = header.timestamp_sec
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid {message.DESCRIPTOR.full_name}.header.timestamp_sec")
            return seconds_to_ns(value)
    if "transforms" in fields and message.transforms:
        # A TF batch represents its earliest transform; individual stamps stay in payload.
        return min(message_time_ns(t) for t in message.transforms)
    return None  # Timestamp field exists but this message did not populate it.

# Topics whose raw payloads are huge; semantic overlays already cover visualization.
# Topic View still lists them (schema). Writing bulky payloads requires --passthrough-payloads.


def is_bulky_topic(channel: str) -> bool:
    c = channel.lower()
    if c.endswith("/compressed") or "/compressed" in c:
        return True
    if "pointcloud" in c:
        return True
    # Uncompressed camera Image frames are multi-MB each.
    if "/apollo/camera/" in c and not c.endswith("/compressed"):
        return True
    return False


def cyber_proto_desc_to_fds(proto_desc_bytes: bytes) -> bytes:
    """Convert Cyber recursive ProtoDesc → google.protobuf.FileDescriptorSet.

    Same data Dreamview/cyber_monitor rely on via GetProtoDesc (file + dependencies).
    Raises on empty/invalid input — no silent raw downgrade.
    """
    from cyber.proto.proto_desc_pb2 import ProtoDesc
    from google.protobuf.descriptor_pb2 import FileDescriptorProto, FileDescriptorSet

    if not proto_desc_bytes:
        raise ValueError("empty Cyber ProtoDesc")

    root = ProtoDesc()
    root.ParseFromString(proto_desc_bytes)
    fds = FileDescriptorSet()
    seen: set[str] = set()

    def walk(node: ProtoDesc) -> None:
        for dep in node.dependencies:
            walk(dep)
        if not node.desc:
            return
        fdp = FileDescriptorProto()
        fdp.ParseFromString(node.desc)
        name = fdp.name or ""
        if not name:
            raise ValueError("FileDescriptorProto missing name in ProtoDesc tree")
        if name in seen:
            return
        seen.add(name)
        fds.file.add().CopyFrom(fdp)

    walk(root)
    if not fds.file:
        raise ValueError("ProtoDesc produced empty FileDescriptorSet")
    return fds.SerializeToString()


def schema_encoding_and_data(type_name: str, cyber_proto_desc: bytes) -> tuple[str, bytes]:
    """MCAP schema for an Apollo topic: protobuf + FDS from Cyber ProtoDesc only."""
    if not type_name:
        raise ValueError("missing message type for passthrough schema")
    fds = cyber_proto_desc_to_fds(cyber_proto_desc)
    return "protobuf", fds

LOCALIZATION_TOPIC = "/apollo/localization/pose"
POINTCLOUD_TOPIC = "/apollo/sensor/rslidar/up/PointCloud2"
CAMERA_TOPICS = {
    "Front120": "/apollo/camera/Front120/compressed",
    "Front30": "/apollo/camera/Front30/compressed",
    "FrontLeft": "/apollo/camera/FrontLeft/compressed",
    "FrontRight": "/apollo/camera/FrontRight/compressed",
    "Rear": "/apollo/camera/Rear/compressed",
    "RearLeft": "/apollo/camera/RearLeft/compressed",
    "RearRight": "/apollo/camera/RearRight/compressed",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--map", help="Apollo base_map directory or .bin/.txt/.json; simulation records use their verified map snapshot automatically")
    p.add_argument("records", nargs="+", help="Apollo .record file(s)")
    p.add_argument("-o", "--output", required=True, help="Output .mcap path")
    p.add_argument(
        "--tool",
        default=str(Path(__file__).resolve().parent / "bin" / "apollo_record_tool"),
    )
    p.add_argument("--begin-ns", type=int, default=0)
    p.add_argument("--duration-ms", type=int, default=0)
    p.add_argument("--max-points", type=int, default=120_000)
    p.add_argument("--no-lidar", action="store_true")
    p.add_argument("--no-localization", action="store_true")
    p.add_argument(
        "--no-passthrough",
        action="store_true",
        help="Do not register original Apollo topics at all",
    )
    p.add_argument(
        "--passthrough-payloads",
        action="store_true",
        help=(
            "Also write bulky Apollo payloads (camera images / lidar). "
            "By default, non-bulky topics (pose, chassis, ...) already write protobuf "
            "payloads for Topic View; bulky topics stay schemas-only + semantic overlays."
        ),
    )
    p.add_argument(
        "--schemas-only-passthrough",
        action="store_true",
        help="Register Apollo topic names only — no passthrough message bytes (Topic View empty).",
    )
    p.add_argument(
        "--no-semantic",
        action="store_true",
        help="Do not emit Foxglove semantic overlays for layout views",
    )
    p.add_argument(
        "--camera",
        action="append",
        default=[],
        choices=[*CAMERA_TOPICS.keys(), "all"],
        help="Cameras for semantic overlays; default 'all'",
    )
    p.add_argument(
        "--channel",
        "-c",
        action="append",
        default=[],
        help="If set, only dump these Apollo channels (default: all channels)",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Worker threads for point-cloud packing (0 = auto)",
    )
    p.add_argument(
        "--gpu",
        choices=["auto", "on", "off"],
        default="auto",
        help="GPU accelerate point packing when available (default: auto)",
    )
    p.add_argument("--progress-file", default=None)
    p.add_argument(
        "--default-timeline",
        choices=["publish", "message"],
        default="publish",
    )
    return p.parse_args()


def selected_camera_topics(args: argparse.Namespace) -> dict[str, str]:
    if not args.camera or "all" in args.camera:
        return dict(CAMERA_TOPICS)
    return {name: CAMERA_TOPICS[name] for name in args.camera}


def detect_gpu(mode: str) -> str | None:
    """Return 'cupy' | 'torch' | None. Default `auto` enables GPU when present."""
    if mode == "off":
        return None
    errors: list[str] = []
    try:
        import cupy as cp  # type: ignore

        if cp.cuda.runtime.getDeviceCount() > 0:
            # Touch device so missing CTK/headers fail here (not mid-convert).
            _ = cp.cuda.Device(0).compute_capability
            _ = cp.zeros(1, dtype=cp.float32)
            cp.cuda.Stream.null.synchronize()
            return "cupy"
        errors.append("cupy: no CUDA device")
    except Exception as e:
        errors.append(f"cupy: {type(e).__name__}: {e}")
    try:
        import torch

        if torch.cuda.is_available():
            return "torch"
        errors.append("torch: cuda not available")
    except Exception as e:
        errors.append(f"torch: {type(e).__name__}: {e}")
    if mode == "on":
        detail = "; ".join(errors) if errors else "no backend"
        print(
            f"WARN: --gpu on but no CuPy/PyTorch CUDA available ({detail}); using CPU",
            file=sys.stderr,
        )
    elif mode == "auto" and errors:
        # Surface why auto fell back — silent CPU was hard to diagnose.
        print(
            "INFO: --gpu auto → CPU (" + "; ".join(errors) + ")",
            file=sys.stderr,
        )
    return None


def write_progress(path: str | None, payload: dict) -> None:
    line = json.dumps(payload, ensure_ascii=False)
    print(line, file=sys.stderr, flush=True)
    if not path:
        return
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def collect_fds(fd, out: FileDescriptorSet, seen: set[str]) -> None:
    if fd.name in seen:
        return
    seen.add(fd.name)
    for dep in fd.dependencies:
        collect_fds(dep, out, seen)
    out.file.add().ParseFromString(fd.serialized_pb)


def schema_bytes_for(msg_cls) -> bytes:
    fds = FileDescriptorSet()
    collect_fds(msg_cls.DESCRIPTOR.file, fds, set())
    return fds.SerializeToString()


def seconds_to_ns(sec: float) -> int:
    return int(sec * 1_000_000_000)


def set_timestamp(msg, ns: int) -> None:
    msg.timestamp.seconds = ns // 1_000_000_000
    msg.timestamp.nanos = ns % 1_000_000_000


def camera_entity_topic(apollo_topic: str) -> str:
    name = apollo_topic.removeprefix("/apollo/camera/").removesuffix("/compressed")
    return f"/camera/{name.replace('/', '_')}"


def parse_h264_message(message_bytes: bytes) -> tuple[bytes | None, int | None]:
    """Extract H264 payload + optional measurement time from air_multimedia compressed image."""
    offset = 0
    h264 = None
    measurement_time = None
    timestamp_sec_start = None
    while offset + 1 < len(message_bytes):
        tag, offset = _read_varint(message_bytes, offset)
        field_number = tag >> 3
        wire_type = tag & 0x7
        if wire_type == 0:
            value, offset = _read_varint(message_bytes, offset)
            if field_number == 1:
                timestamp_sec_start = value
            elif field_number == 3:
                measurement_time = value
        elif wire_type == 1:
            offset += 8
        elif wire_type == 2:
            size, offset = _read_varint(message_bytes, offset)
            value = message_bytes[offset : offset + size]
            offset += size
            if field_number == 4:
                h264 = value
        elif wire_type == 5:
            offset += 4
        else:
            break
    message_seconds = measurement_time or timestamp_sec_start
    return h264, seconds_to_ns(message_seconds) if message_seconds else None


def _read_varint(buf: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if offset >= len(buf):
            raise ValueError("truncated varint")
        b = buf[offset]
        offset += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, offset
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")


def pack_pointcloud_bytes(
    payload: bytes,
    max_points: int,
    gpu: str | None,
) -> tuple[bytes, int, int, str] | None:
    """Return (packed_xyz_i, point_count, message_ns, sensor_frame) or None."""
    msg = pointcloud_pb2.PointCloud()
    msg.ParseFromString(payload)
    if not msg.point:
        return None
    message_ns = (
        seconds_to_ns(msg.measurement_time) if msg.HasField("measurement_time") else 0
    )
    xs = np.fromiter((pt.x for pt in msg.point), dtype=np.float64, count=len(msg.point))
    ys = np.fromiter((pt.y for pt in msg.point), dtype=np.float64, count=len(msg.point))
    zs = np.fromiter((pt.z for pt in msg.point), dtype=np.float64, count=len(msg.point))
    intens = np.fromiter(
        (getattr(pt, "intensity", 0.0) for pt in msg.point),
        dtype=np.float64,
        count=len(msg.point),
    )

    if gpu == "cupy":
        import cupy as cp  # type: ignore

        cxs, cys, czs, ci = map(cp.asarray, (xs, ys, zs, intens))
        mask = cp.isfinite(cxs) & cp.isfinite(cys) & cp.isfinite(czs)
        cxs, cys, czs, ci = cxs[mask], cys[mask], czs[mask], ci[mask]
        n = int(cxs.shape[0])
        if n == 0:
            return None
        step = 1
        if max_points and n > max_points:
            step = int(np.ceil(n / max_points))
        cxs, cys, czs, ci = cxs[::step], cys[::step], czs[::step], ci[::step]
        n = int(cxs.shape[0])
        packed = cp.column_stack(
            (cxs.astype(cp.float32), cys.astype(cp.float32), czs.astype(cp.float32), ci.astype(cp.float32))
        ).reshape(-1)
        buf = cp.asnumpy(packed).tobytes()
        return buf, n, message_ns, msg.header.frame_id

    if gpu == "torch":
        import torch

        device = torch.device("cuda")
        txs = torch.as_tensor(xs, device=device)
        tys = torch.as_tensor(ys, device=device)
        tzs = torch.as_tensor(zs, device=device)
        ti = torch.as_tensor(intens, device=device)
        mask = torch.isfinite(txs) & torch.isfinite(tys) & torch.isfinite(tzs)
        txs, tys, tzs, ti = txs[mask], tys[mask], tzs[mask], ti[mask]
        n = int(txs.numel())
        if n == 0:
            return None
        step = 1
        if max_points and n > max_points:
            step = int(np.ceil(n / max_points))
        txs, tys, tzs, ti = txs[::step], tys[::step], tzs[::step], ti[::step]
        n = int(txs.numel())
        packed = torch.stack(
            (txs.float(), tys.float(), tzs.float(), ti.float()), dim=1
        ).reshape(-1)
        buf = packed.detach().cpu().numpy().tobytes()
        return buf, n, message_ns, msg.header.frame_id

    # CPU / numpy
    mask = np.isfinite(xs) & np.isfinite(ys) & np.isfinite(zs)
    xs, ys, zs, intens = xs[mask], ys[mask], zs[mask], intens[mask]
    n = int(xs.shape[0])
    if n == 0:
        return None
    step = 1
    if max_points and n > max_points:
        step = int(np.ceil(n / max_points))
    xs, ys, zs, intens = xs[::step], ys[::step], zs[::step], intens[::step]
    n = int(xs.shape[0])
    packed = np.column_stack(
        (xs.astype(np.float32), ys.astype(np.float32), zs.astype(np.float32), intens.astype(np.float32))
    ).reshape(-1)
    return packed.tobytes(), n, message_ns, msg.header.frame_id


class SemanticMcapWriter:
    def __init__(self, path: Path, default_timeline: str, scene=None):
        self.path = path
        self.default_timeline = default_timeline
        self._fh = open(path, "wb")
        self.writer = McapWriter(self._fh)
        self.writer.start(profile="", library=f"apollo_record_to_semantic_mcap/{CONVERTER_VERSION}")
        self.channel_ids: dict[str, int] = {}
        self.schema_ids: dict[str, int] = {}
        self.scene = scene or SceneContext()
        self.origin: np.ndarray | None = self.scene.origin
        self.msg_count = 0
        self.passthrough_topics: set[str] = set()
        self.seq: dict[str, int] = {}
        self.message_classes = {}
        self.passthrough_schema_ids = {}
        self.publish_only_ids = {}
        # Cyber record header times (from dump-jsonl meta) — display as-is, do not recompute.
        self.header_begin_ns: int = 0
        self.header_end_ns: int = 0
        self.map_metadata = {}

        # Pose/PointCloud overlays use paths that do not collide with Apollo bags.
        # FrameTransform is registered lazily via ensure_tf_channel() so bag `/tf`
        # can be preserved as a passthrough channel when present.
        self._register_foxglove("foxglove.PoseInFrame", PoseInFrame, "/vehicle")
        self._register_foxglove("foxglove.PointCloud", PointCloud, "/lidar/up/points")
        self._register_foxglove("webmonitor.PlannedPath", PlannedPath, "/planning/trajectory")
        self.tf_topic: str | None = None

    def _register_foxglove(self, schema_name: str, cls, topic: str, metadata=None) -> None:
        sid = self.writer.register_schema(
            name=schema_name,
            encoding="protobuf",
            data=schema_bytes_for(cls),
        )
        self.schema_ids[schema_name] = sid
        cid = self.writer.register_channel(
            topic=topic,
            message_encoding="protobuf",
            schema_id=sid,
            metadata={
                "apollo_converter": CONVERTER_VERSION,
                "default_timeline": self.default_timeline,
                "layer": "semantic",
                **(metadata or {}),
            },
        )
        self.channel_ids[topic] = cid
        self.seq[topic] = 0

    def register_passthrough(self, topic: str, type_name: str, proto_desc: bytes) -> None:
        if topic in self.channel_ids:
            return
        # Cyber GetProtoDesc is recursive ProtoDesc (file + deps) — same as Dreamview/monitor.
        # Fail loud if conversion fails; do not silently store raw.
        try:
            encoding, schema_data = schema_encoding_and_data(type_name, proto_desc)
        except Exception as err:
            raise RuntimeError(
                f"ProtoDesc→FDS failed for topic={topic!r} type={type_name!r}: {err}"
            ) from err
        sid = self.writer.register_schema(
            name=type_name or "apollo.Unknown",
            encoding=encoding,
            data=schema_data,
        )
        self.passthrough_schema_ids[topic] = sid
        cid = self.writer.register_channel(
            topic=topic,
            message_encoding=encoding,
            schema_id=sid,
            metadata={
                "apollo_converter": CONVERTER_VERSION,
                "apollo_type": type_name,
                "layer": "passthrough",
                "schema_encoding": encoding,
                "proto_desc_bytes": str(len(proto_desc or b"")),
                "message_time_available": str(any(name in self.message_classes[topic].DESCRIPTOR.fields_by_name
                    for name in ("header", "measurement_time", "timestamp_sec_start", "transforms"))).lower(),
            },
        )
        self.channel_ids[topic] = cid
        self.passthrough_topics.add(topic)
        self.seq[topic] = 0

    def ensure_tf_channel(self) -> str:
        """Pick a FrameTransform topic that does not steal bag `/tf`."""
        if self.tf_topic is not None:
            return self.tf_topic
        # Prefer classic `/tf` only when the bag did not already claim it.
        topic = "/tf" if "/tf" not in self.channel_ids else "/foxglove/tf"
        self._register_foxglove("foxglove.FrameTransform", FrameTransform, topic)
        self.tf_topic = topic
        return topic

    def ensure_camera_channel(self, topic: str) -> int:

        if topic in self.channel_ids:
            return self.channel_ids[topic]
        sid = self.schema_ids.get("foxglove.CompressedVideo")
        if sid is None:
            sid = self.writer.register_schema(
                name="foxglove.CompressedVideo",
                encoding="protobuf",
                data=schema_bytes_for(CompressedVideo),
            )
            self.schema_ids["foxglove.CompressedVideo"] = sid
        cid = self.writer.register_channel(
            topic=topic,
            message_encoding="protobuf",
            schema_id=sid,
            metadata={
                "apollo_converter": CONVERTER_VERSION,
                "format": "h264",
                "layer": "semantic",
            },
        )
        self.channel_ids[topic] = cid
        self.seq[topic] = 0
        return cid

    def add(self, topic: str, publish_ns: int, message_ns: int, data: bytes) -> None:
        cid = self.channel_ids[topic]
        if message_ns is None:
            if topic not in self.passthrough_schema_ids:
                raise ValueError(f"Missing message_time for semantic channel {topic}")
            if topic not in self.publish_only_ids:
                self.publish_only_ids[topic] = self.writer.register_channel(
                    topic=topic, message_encoding="protobuf", schema_id=self.passthrough_schema_ids[topic],
                    metadata={"apollo_converter": CONVERTER_VERSION, "layer": "passthrough",
                              "message_time_available": "false"})
                self.scene.warnings.add(f"{topic}: messages without generation timestamp are publish_time only")
            cid = self.publish_only_ids[topic]
        seq = self.seq.get(topic, 0)
        self.seq[topic] = seq + 1
        self.writer.add_message(
            channel_id=cid,
            # MCAP requires an index timestamp. For publish-only schemas this
            # storage slot is NOT exposed as message_time (channel metadata).
            log_time=message_ns if message_ns is not None else publish_ns,
            publish_time=publish_ns,
            data=data,
            sequence=seq,
        )
        self.msg_count += 1

    def finish(self) -> None:
        self.writer.add_metadata(
            "apollo_converter",
            {
                "converter": CONVERTER_VERSION,
                "default_timeline": self.default_timeline,
                "passthrough_topics": str(len(self.passthrough_topics)),
                "header_begin_ns": str(self.header_begin_ns),
                "header_end_ns": str(self.header_end_ns),
                "scene_warnings": json.dumps(sorted(self.scene.warnings)),
                "hd_map": json.dumps(self.map_metadata),
                "clock_contract": "publish_time,message_time",
                "publish_time_source": "Apollo record timestamp_ns (simulation dispatch time; legacy Cyber recorder callback time)",
                "message_time_source": "payload measurement_time / header.timestamp_sec / timestamp_sec_start; unavailable schemas are publish-only",
            },
        )
        self.writer.finish()
        self._fh.close()


def write_localization(writer: SemanticMcapWriter, payload: bytes, publish_ns: int, message_ns: int) -> None:
    loc = localization_pb2.LocalizationEstimate()
    loc.ParseFromString(payload)
    if not loc.HasField("pose"):
        return
    pose = loc.pose
    p = pose.position
    o = pose.orientation
    display = np.array([p.x, p.y, p.z], dtype=np.float64)
    if writer.origin is None:
        writer.origin = display.copy()
    display = display - writer.origin
    qx, qy, qz, qw = float(o.qx), float(o.qy), float(o.qz), float(o.qw)

    # Optional diagnostic TF is separate from the wm_map_local scene.
    # Pose, point cloud and planned path share an explicit reference frame;
    # lidar mounting transforms are composed from the recorded static TF.
    tf_topic = writer.ensure_tf_channel()

    ft = FrameTransform()
    set_timestamp(ft, message_ns)
    ft.parent_frame_id = "map"
    ft.child_frame_id = "vehicle"
    ft.translation.x = float(display[0])
    ft.translation.y = float(display[1])
    ft.translation.z = float(display[2])
    ft.rotation.x = qx
    ft.rotation.y = qy
    ft.rotation.z = qz
    ft.rotation.w = qw
    writer.add(tf_topic, publish_ns, message_ns, ft.SerializeToString())

    pose_msg = PoseInFrame()
    set_timestamp(pose_msg, message_ns)
    pose_msg.frame_id = "wm_map_local"
    pose_msg.pose.position.x = float(display[0])
    pose_msg.pose.position.y = float(display[1])
    pose_msg.pose.position.z = float(display[2])
    pose_msg.pose.orientation.x = qx
    pose_msg.pose.orientation.y = qy
    pose_msg.pose.orientation.z = qz
    pose_msg.pose.orientation.w = qw
    writer.add("/vehicle", publish_ns, message_ns, pose_msg.SerializeToString())


def write_pointcloud_packed(
    writer: SemanticMcapWriter,
    packed: bytes,
    n: int,
    publish_ns: int,
    message_ns: int,
    sensor_frame: str,
) -> None:
    if message_ns is None:
        raise ValueError("Point cloud has no message_time; cannot align sensor geometry")
    pc = PointCloud()
    set_timestamp(pc, message_ns)
    pc.frame_id = "wm_map_local"
    missing_tf = writer.scene.origin is not None and not writer.scene.has_lidar_tf(sensor_frame)
    if missing_tf:
        warning = f"Missing static TF for {sensor_frame}: lidar is isolated in its sensor frame; cannot overlay ego/planning in this 3D view"
        if warning not in writer.scene.warnings:
            print(f"Scene warning: {warning}", file=sys.stderr)
            writer.scene.warnings.add(warning)
        placement = None
    else:
        placement = writer.scene.lidar_pose(message_ns, sensor_frame)
    if placement is None:
        # Sensor data is still visible before the first localization, but isolated
        # from map overlays. No nearest/future localization is substituted.
        pc.frame_id = f"isolated_sensor/{sensor_frame}" if missing_tf else "unlocalized_lidar"
        pc.pose.orientation.w = 1.0
    else:
        p, q = placement
        pc.pose.position.x, pc.pose.position.y, pc.pose.position.z = p
        pc.pose.orientation.x, pc.pose.orientation.y, pc.pose.orientation.z, pc.pose.orientation.w = q
    pc.point_stride = 16
    for name, offset, dtype in (
        ("x", 0, PackedElementField.FLOAT32),
        ("y", 4, PackedElementField.FLOAT32),
        ("z", 8, PackedElementField.FLOAT32),
        ("intensity", 12, PackedElementField.FLOAT32),
    ):
        f = pc.fields.add()
        f.name = name
        f.offset = offset
        f.type = dtype
    pc.data = packed
    writer.add("/lidar/up/points", publish_ns, message_ns, pc.SerializeToString())


def write_camera(
    writer: SemanticMcapWriter,
    apollo_topic: str,
    payload: bytes,
    publish_ns: int,
    message_ns: int,
) -> None:
    h264, _ = parse_h264_message(payload)
    if not h264:
        return
    topic = camera_entity_topic(apollo_topic)
    writer.ensure_camera_channel(topic)
    cv = CompressedVideo()
    set_timestamp(cv, message_ns)
    cv.frame_id = ""
    cv.format = "h264"
    cv.data = h264
    writer.add(topic, publish_ns, message_ns, cv.SerializeToString())


def run_dumper(args: argparse.Namespace, channels: list[str]) -> subprocess.Popen:
    cmd = [args.tool, "dump-jsonl"]
    if args.begin_ns:
        cmd += ["--begin-ns", str(args.begin_ns)]
    if args.duration_ms:
        cmd += ["--duration-ms", str(args.duration_ms)]
    for t in channels:
        cmd += ["-c", t]
    cmd += args.records
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        bufsize=1 << 20,
    )


def main() -> int:
    args = parse_args()
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out_path.with_suffix(out_path.suffix + ".partial")

    cameras = selected_camera_topics(args)
    camera_topics_set = set(cameras.values())
    do_passthrough = not args.no_passthrough
    do_semantic = not args.no_semantic
    # Default: write protobuf payloads for Topic View on non-bulky topics.
    write_light_payloads = do_passthrough and not args.schemas_only_passthrough
    write_bulky_payloads = bool(args.passthrough_payloads)
    gpu = detect_gpu(args.gpu)
    workers = args.workers if args.workers > 0 else min(8, max(2, (os.cpu_count() or 4)))

    # Default: dump ALL channels (empty list → no -c filter).
    dump_channels = list(args.channel) if args.channel else []

    write_progress(
        args.progress_file,
        {
            "status": "running",
            "progress": 0.0,
            "phase": "start",
            "message": (
                f"Converting {len(args.records)} record(s) → MCAP "
                f"(passthrough={'all-payloads' if write_bulky_payloads else ('light-payloads' if write_light_payloads else 'schemas')}, "
                f"semantic={'on' if do_semantic else 'off'}, "
                f"workers={workers}, gpu={gpu or 'off'})"
            ),
            "converter": CONVERTER_VERSION,
        },
    )

    scene = SceneContext()
    if do_semantic:
        scene.read(run_dumper(args, [LOCALIZATION_TOPIC, "/tf_static"]), message_time_ns)
    map_path = resolve_map(args.records, args.map) if do_semantic else None
    map_data = load_map(map_path) if map_path else None
    map_meshes = build_map_meshes(map_data, scene.origin) if map_data is not None else {}
    if do_semantic and not map_path:
        scene.warnings.add("No HD map selected: choose an Apollo map in Source before opening this record")
    proc = run_dumper(args, dump_channels)
    assert proc.stdout is not None

    writer: SemanticMcapWriter | None = None
    begin_ns = 0
    end_ns = 0
    last_progress_t = 0.0
    converted = 0
    passthrough_msgs = 0
    semantic_msgs = 0
    pending_pc = []

    # CPU-bound protobuf parse → process pool. Use spawn so CUDA (CuPy) can
    # initialize cleanly inside workers when --gpu auto/on.
    import multiprocessing as mp

    pool = ProcessPoolExecutor(
        max_workers=workers,
        mp_context=mp.get_context("spawn"),
    )

    def flush_pc_jobs(block: bool = False) -> None:
        nonlocal semantic_msgs
        still = []
        for fut, publish_ns, message_ns in pending_pc:
            if not block and not fut.done():
                still.append((fut, publish_ns, message_ns))
                continue
            result = fut.result()
            if result is None or writer is None:
                continue
            packed, n, _, sensor_frame = result
            write_pointcloud_packed(writer, packed, n, publish_ns, message_ns, sensor_frame)
            semantic_msgs += 1
        pending_pc[:] = still

    try:
        for line in proc.stdout:
            event = json.loads(line)
            op = event.get("op")
            if op == "meta":
                begin_ns = int(event.get("begin_ns") or 0)
                end_ns = int(event.get("end_ns") or 0)
                write_progress(
                    args.progress_file,
                    {
                        "status": "running",
                        "progress": 0.01,
                        "phase": "dump",
                        "message": "Reading Apollo record...",
                        "begin_ns": begin_ns,
                        "end_ns": end_ns,
                    },
                )
                continue

            if op == "schema":
                if writer is None:
                    writer = SemanticMcapWriter(tmp_out, args.default_timeline, scene)
                    writer.header_begin_ns = begin_ns
                    writer.header_end_ns = end_ns
                    if map_path:
                        writer.map_metadata = {"path": str(map_path), "sha256": hashlib.sha256(map_path.read_bytes()).hexdigest(),
                                               "origin": scene.origin.tolist(), "lanes": len(map_data.lane),
                                               "frame": "wm_map_local", "static": True}
                        for topic, mesh in map_meshes.items():
                            writer._register_foxglove("webmonitor.MapMesh", MapMesh, topic,
                                                     {"static": "true", "message_time_available": "false"})
                            # Storage placement only: the decoder emits timeless static
                            # geometry, never a fabricated map generation-time clock.
                            writer.add(topic, begin_ns, begin_ns, mesh.SerializeToString())
                fds = cyber_proto_desc_to_fds(base64.b64decode(event.get("proto_desc_b64") or ""))
                descriptor = pool_from_file_descriptor_set(fds).FindMessageTypeByName(event["type"])
                writer.message_classes[event["channel"]] = GetMessageClass(descriptor)
                if do_passthrough:
                    writer.register_passthrough(
                        event["channel"],
                        event.get("type") or "apollo.Unknown",
                        base64.b64decode(event.get("proto_desc_b64") or ""),
                    )
                continue

            if op != "msg":
                continue

            if writer is None:
                writer = SemanticMcapWriter(tmp_out, args.default_timeline, scene)
                writer.header_begin_ns = begin_ns
                writer.header_end_ns = end_ns

            publish_ns = int(event["timestamp_ns"])
            channel = event["channel"]
            payload = base64.b64decode(event["data_b64"])
            message_ns = message_time_ns(writer.message_classes[channel].FromString(payload))

            # 1) Topic list + Topic View payloads (protobuf FDS from Cyber ProtoDesc).
            if do_passthrough:
                if channel not in writer.channel_ids:
                    raise RuntimeError(
                        f"message for {channel!r} before schema event — refuse silent Unknown/raw"
                    )
                write_payload = write_light_payloads and (
                    write_bulky_payloads or not is_bulky_topic(channel)
                )
                if write_payload:
                    writer.add(channel, publish_ns, message_ns, payload)
                    passthrough_msgs += 1

            # 2) Semantic overlays for AD layouts.
            if do_semantic:
                if channel == LOCALIZATION_TOPIC and not args.no_localization:
                    write_localization(writer, payload, publish_ns, message_ns)
                    semantic_msgs += 1
                elif channel == "/apollo/planning":
                    path = scene.planned_path(payload)
                    writer.add("/planning/trajectory", publish_ns, message_ns, path.SerializeToString())
                    semantic_msgs += 1
                elif channel in ("/apollo/perception/obstacles", "/apollo/prediction"):
                    is_perception = channel.endswith("/obstacles")
                    target = "/perception/obstacles" if is_perception else "/prediction/trajectories"
                    if target not in writer.channel_ids:
                        writer._register_foxglove("webmonitor.PlannedPath", PlannedPath, target)
                    paths = scene.perceived_obstacles(payload) if is_perception else scene.predicted_trajectories(payload)
                    writer.add(target, publish_ns, message_ns, paths.SerializeToString())
                    semantic_msgs += 1
                elif channel == POINTCLOUD_TOPIC and not args.no_lidar:
                    fut = pool.submit(pack_pointcloud_bytes, payload, args.max_points, gpu)
                    pending_pc.append((fut, publish_ns, message_ns))
                    if len(pending_pc) >= workers * 2:
                        flush_pc_jobs(block=False)
                elif channel in camera_topics_set:
                    write_camera(writer, channel, payload, publish_ns, message_ns)
                    semantic_msgs += 1

            converted += 1
            now = time.time()
            if now - last_progress_t >= 0.25:
                last_progress_t = now
                pct = 0.05
                if end_ns > begin_ns > 0:
                    pct = min(
                        0.99,
                        max(0.02, (publish_ns - begin_ns) / float(end_ns - begin_ns)),
                    )
                write_progress(
                    args.progress_file,
                    {
                        "status": "running",
                        "progress": pct,
                        "phase": "convert",
                        "done": converted,
                        "passthrough": passthrough_msgs,
                        "semantic": semantic_msgs,
                        "begin_ns": begin_ns,
                        "end_ns": end_ns,
                        "message": (
                            f"Converted {converted} msgs "
                            f"(passthrough={passthrough_msgs}, semantic={semantic_msgs})..."
                        ),
                    },
                )

        flush_pc_jobs(block=True)
        pool.shutdown(wait=True)

        ret = proc.wait()
        if ret != 0:
            raise RuntimeError(f"apollo_record_tool dump-jsonl failed: exit {ret}")
        if writer is None:
            raise RuntimeError("No matching messages found in record (check channels / time range)")

        writer.finish()
        os.replace(tmp_out, out_path)
        write_progress(
            args.progress_file,
            {
                "status": "done",
                "progress": 1.0,
                "phase": "done",
                "done": converted,
                "passthrough": passthrough_msgs,
                "semantic": semantic_msgs,
                "topics": len(writer.channel_ids),
                "output": str(out_path),
                # Cyber header times — Properties shows these directly (no timeline scan).
                "begin_ns": begin_ns,
                "end_ns": end_ns,
                "message": (
                    f"Wrote {out_path} ({converted} msgs, "
                    f"{len(writer.channel_ids)} topics, gpu={gpu or 'off'})"
                ),
            },
        )
        print(
            json.dumps(
                {
                    "op": "done",
                    "output": str(out_path),
                    "messages": converted,
                    "topics": len(writer.channel_ids),
                    "passthrough": passthrough_msgs,
                    "semantic": semantic_msgs,
                    "gpu": gpu or "off",
                }
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        pool.shutdown(wait=False, cancel_futures=True)
        if tmp_out.exists():
            tmp_out.unlink(missing_ok=True)
        write_progress(
            args.progress_file,
            {"status": "error", "progress": 0.0, "phase": "error", "message": str(exc)},
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def cache_key_for(records: list[str], extra: str = "") -> str:
    h = hashlib.sha256()
    h.update(CONVERTER_VERSION.encode())
    h.update(extra.encode())
    for r in records:
        p = Path(r).resolve()
        st = p.stat()
        h.update(str(p).encode())
        h.update(str(st.st_size).encode())
        h.update(str(int(st.st_mtime_ns)).encode())
    return h.hexdigest()[:24]


if __name__ == "__main__":
    raise SystemExit(main())
