#!/usr/bin/env python3
"""Export selected Apollo CyberRT record channels to a Rerun .rrd file.

This script uses the C++ `apollo_record_tool dump-jsonl` bridge to avoid the
Python RecordReader wrapper compatibility issue in Apollo 10.0.
"""

import argparse
import base64
import struct
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
sys.path.insert(0, "/opt/apollo/neo/python")

import numpy as np
import rerun as rr
from PIL import Image
from google.protobuf import text_format

from modules.common_msgs.localization_msgs import localization_pb2
from modules.common_msgs.map_msgs import map_pb2
from modules.common_msgs.sensor_msgs import pointcloud_pb2


LOCALIZATION_TOPIC = "/apollo/localization/pose"
POINTCLOUD_TOPIC = "/apollo/sensor/rslidar/up/PointCloud2"
DESKEW_CLOUD_TOPIC = "liorf/deskew/cloud_info"
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
    parser = argparse.ArgumentParser()
    parser.add_argument("records", nargs="+", help="Apollo .record files")
    parser.add_argument("-o", "--output", required=True, help="Output .rrd file")
    parser.add_argument(
        "--tool",
        default=str(Path(__file__).parent / "bin" / "apollo_record_tool"),
        help="Path to apollo_record_tool",
    )
    parser.add_argument("--begin-ns", type=int, default=0)
    parser.add_argument("--duration-ms", type=int, default=0)
    parser.add_argument(
        "--time-mode",
        choices=["message", "publish"],
        default="message",
        help="Default timeline to use for record_time. Both publish_time and message_time are always written.",
    )
    parser.add_argument(
        "--max-points",
        type=int,
        default=200_000,
        help="Maximum lidar points to log per frame; 0 keeps all points.",
    )
    parser.add_argument(
        "--color-mode",
        choices=["intensity", "height", "range", "semantic-lite"],
        default="semantic-lite",
        help=(
            "Point cloud coloring. semantic-lite is a geometry/intensity "
            "heuristic: ground in yellow/brown, vegetation-like elevated "
            "points in green, strong reflectors in cyan/white."
        ),
    )
    parser.add_argument(
        "--no-center",
        action="store_true",
        help="Do not subtract first localization pose from trajectory.",
    )
    parser.add_argument(
        "--topic",
        action="append",
        default=[],
        help="Additional Apollo topic to pass through the dumper.",
    )
    parser.add_argument(
        "--no-lidar",
        action="store_true",
        help="Do not export lidar point clouds.",
    )
    parser.add_argument(
        "--no-localization",
        action="store_true",
        help="Do not export localization pose/trajectory.",
    )
    parser.add_argument(
        "--camera",
        action="append",
        default=[],
        choices=[*CAMERA_TOPICS.keys(), "all"],
        help="Camera stream to decode into Rerun images. Repeatable; use 'all' for all cameras.",
    )
    parser.add_argument(
        "--camera-max-frames",
        type=int,
        default=0,
        help="Maximum decoded frames per camera; 0 keeps all decoded frames.",
    )
    parser.add_argument(
        "--camera-mode",
        choices=["video", "images"],
        default="video",
        help="Use direct MP4 video references or decode frames to images. Default: video.",
    )
    parser.add_argument(
        "--video-dir",
        default=None,
        help="Directory for extracted camera MP4 files. Default: <output_stem>_videos next to output.",
    )
    parser.add_argument(
        "--hdmap",
        default=None,
        help="Apollo HDMap file to log as static map geometry (.bin protobuf or .txt pb text).",
    )
    return parser.parse_args()


def selected_camera_topics(args: argparse.Namespace) -> dict[str, str]:
    if not args.camera:
        return {}
    if "all" in args.camera:
        return dict(CAMERA_TOPICS)
    return {name: CAMERA_TOPICS[name] for name in args.camera}


def run_dumper(args: argparse.Namespace) -> subprocess.Popen:
    cmd = [args.tool, "dump-jsonl"]
    if args.begin_ns:
        cmd += ["--begin-ns", str(args.begin_ns)]
    if args.duration_ms:
        cmd += ["--duration-ms", str(args.duration_ms)]

    camera_topics = selected_camera_topics(args)
    topics = []
    if not args.no_localization:
        topics.append(LOCALIZATION_TOPIC)
    if not args.no_lidar:
        topics.append(POINTCLOUD_TOPIC)
        topics.append(DESKEW_CLOUD_TOPIC)
    topics.extend(camera_topics.values())
    topics.extend(args.topic)
    for topic in topics:
        cmd += ["-c", topic]
    cmd += args.records

    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        bufsize=1,
    )


def point_xyz(point) -> tuple[float, float, float]:
    return (float(point.x), float(point.y), float(point.z))


def read_varint(buf: bytes, offset: int) -> tuple[int, int]:
    shift = 0
    value = 0
    while True:
        byte = buf[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7


def seconds_to_ns(seconds: float) -> int:
    return int(seconds * 1_000_000_000)


def choose_time_ns(publish_ns: int, message_ns: int | None, state: dict) -> int:
    if state["time_mode"] == "message" and message_ns:
        return message_ns
    return publish_ns


def set_rerun_time(
    publish_ns: int,
    message_ns: int | None,
    state: dict,
    sequence: int | None = None,
) -> None:
    active_ns = choose_time_ns(publish_ns, message_ns, state)
    if state["start_ns"] is None:
        state["start_ns"] = active_ns
    if state["publish_start_ns"] is None:
        state["publish_start_ns"] = publish_ns
    if message_ns and state["message_start_ns"] is None:
        state["message_start_ns"] = message_ns

    rr.set_time("record_time", duration=(active_ns - state["start_ns"]) / 1e9)
    rr.set_time("publish_time", duration=(publish_ns - state["publish_start_ns"]) / 1e9)
    if message_ns and state["message_start_ns"] is not None:
        rr.set_time("message_time", duration=(message_ns - state["message_start_ns"]) / 1e9)
    rr.set_time("record_index", sequence=state["message_index"] if sequence is None else sequence)


def parse_h264_message(message_bytes: bytes) -> tuple[bytes | None, int | None]:
    """Extract data and message time from apollo.air_multimedia.H264CompressedImage.

    We parse only the protobuf wire fields we need so this works even when the
    project-specific pb2 module is not available in the Apollo package.
    """
    offset = 0
    h264 = None
    timestamp_sec_start = None
    measurement_time = None
    while offset < len(message_bytes):
        key, offset = read_varint(message_bytes, offset)
        field_number = key >> 3
        wire_type = key & 0x07
        if wire_type == 0:
            _, offset = read_varint(message_bytes, offset)
        elif wire_type == 1:
            value = message_bytes[offset : offset + 8]
            offset += 8
            if field_number == 5:
                timestamp_sec_start = struct.unpack("<d", value)[0]
            elif field_number == 6:
                measurement_time = struct.unpack("<d", value)[0]
        elif wire_type == 2:
            size, offset = read_varint(message_bytes, offset)
            value = message_bytes[offset : offset + size]
            offset += size
            if field_number == 4:
                h264 = value
        elif wire_type == 5:
            offset += 4
        else:
            raise ValueError(f"unsupported protobuf wire type: {wire_type}")
    message_seconds = measurement_time or timestamp_sec_start
    return h264, seconds_to_ns(message_seconds) if message_seconds else None


def parse_deskew_cloud_message(message_bytes: bytes) -> tuple[bytes | None, tuple[float, float, float] | None]:
    """Extract embedded apollo.drivers.PointCloud from cloudWithPoseInfo."""
    offset = 0
    initial_guess = {}
    cloud = None
    while offset < len(message_bytes):
        key, offset = read_varint(message_bytes, offset)
        field_number = key >> 3
        wire_type = key & 0x07
        if wire_type == 0:
            _, offset = read_varint(message_bytes, offset)
        elif wire_type == 1:
            offset += 8
        elif wire_type == 2:
            size, offset = read_varint(message_bytes, offset)
            value = message_bytes[offset : offset + size]
            offset += size
            if field_number == 13:
                cloud = value
        elif wire_type == 5:
            value = message_bytes[offset : offset + 4]
            offset += 4
            if field_number in (7, 8, 9):
                initial_guess[field_number] = struct.unpack("<f", value)[0]
        else:
            raise ValueError(f"unsupported protobuf wire type: {wire_type}")
    pose = None
    if all(field in initial_guess for field in (7, 8, 9)):
        pose = (initial_guess[7], initial_guess[8], initial_guess[9])
    return cloud, pose


def log_deskew_pointcloud(
    message_bytes: bytes,
    publish_ns: int,
    state: dict,
    max_points: int,
    color_mode: str,
) -> None:
    cloud_payload, _ = parse_deskew_cloud_message(message_bytes)
    if not cloud_payload:
        return
    log_pointcloud(
        cloud_payload,
        publish_ns,
        state,
        max_points,
        color_mode,
        entity="vehicle/lidar/deskew_points",
    )


def log_localization(message_bytes: bytes, publish_ns: int, state: dict) -> None:
    msg = localization_pb2.LocalizationEstimate()
    msg.ParseFromString(message_bytes)
    if not msg.HasField("pose") or not msg.pose.HasField("position"):
        return
    message_ns = seconds_to_ns(msg.measurement_time) if msg.HasField("measurement_time") else None
    timestamp_ns = choose_time_ns(publish_ns, message_ns, state)

    pos = np.array(point_xyz(msg.pose.position), dtype=np.float32)
    if state["origin"] is None and not state["no_center"]:
        state["origin"] = pos.astype(np.float64)
        log_hdmap_if_ready(state)

    display_pos = pos.astype(np.float64)
    if state["origin"] is not None:
        display_pos = display_pos - state["origin"]
    display_pos = display_pos.astype(np.float32)

    state["trajectory"].append(display_pos.tolist())

    set_rerun_time(publish_ns, message_ns, state)
    rr.log("vehicle/position", rr.Points3D([[0.0, 0.0, 0.0]], colors=[[255, 0, 0]], radii=0.8))
    if len(state["trajectory"]) >= 2:
        rr.log(
            "trajectory",
            rr.LineStrips3D([state["trajectory"]], colors=[[255, 180, 0]], radii=0.15),
        )

    if msg.pose.HasField("orientation"):
        q = msg.pose.orientation
        rr.log(
            "vehicle",
            rr.Transform3D(
                translation=display_pos,
                quaternion=[float(q.qx), float(q.qy), float(q.qz), float(q.qw)],
            ),
        )


def map_point_xyz(point, state: dict) -> list[float]:
    xyz = np.array([float(point.x), float(point.y), float(point.z)], dtype=np.float64)
    if state["origin"] is not None:
        xyz[:2] -= state["origin"][:2]
        if not state.get("hdmap_flat_z"):
            xyz[2] -= state["origin"][2]
    return xyz.astype(np.float32).tolist()


def curve_to_points(curve, state: dict) -> list[list[float]]:
    points: list[list[float]] = []
    for segment in curve.segment:
        if segment.HasField("line_segment"):
            for point in segment.line_segment.point:
                points.append(map_point_xyz(point, state))
    return points


def polygon_to_points(polygon, state: dict) -> list[list[float]]:
    points = [map_point_xyz(point, state) for point in polygon.point]
    if len(points) > 2:
        points.append(points[0])
    return points


def load_hdmap(path: str) -> map_pb2.Map:
    hdmap = map_pb2.Map()
    data = Path(path).read_bytes()
    if Path(path).suffix == ".bin":
        hdmap.ParseFromString(data)
    else:
        text_format.Parse(data.decode("utf-8"), hdmap)
    return hdmap


def log_line_strips(entity: str, strips: list[list[list[float]]], color: list[int], radius: float = 0.08) -> None:
    strips = [strip for strip in strips if len(strip) >= 2]
    if strips:
        rr.log(entity, rr.LineStrips3D(strips, colors=[color], radii=radius), static=True)


def log_hdmap_static(state: dict) -> None:
    hdmap_path = state.get("hdmap_path")
    if not hdmap_path or state.get("hdmap_logged"):
        return
    if state["origin"] is None and not state["no_center"]:
        return

    hdmap = load_hdmap(hdmap_path)
    map_z_values = []
    for lane in hdmap.lane:
        curves = []
        if lane.HasField("central_curve"):
            curves.append(lane.central_curve)
        if lane.HasField("left_boundary") and lane.left_boundary.HasField("curve"):
            curves.append(lane.left_boundary.curve)
        if lane.HasField("right_boundary") and lane.right_boundary.HasField("curve"):
            curves.append(lane.right_boundary.curve)
        for curve in curves:
            for segment in curve.segment:
                if segment.HasField("line_segment"):
                    map_z_values.extend(point.z for point in segment.line_segment.point)
    if map_z_values and max(map_z_values) - min(map_z_values) < 0.1 and abs(np.mean(map_z_values)) < 1.0:
        # This dataset's HDMap is a flat 2D map with z=0, while localization z is
        # an absolute altitude. Keep map z at 0 so it aligns with the centered car.
        state["hdmap_flat_z"] = True

    lane_centers = []
    lane_left = []
    lane_right = []
    for lane in hdmap.lane:
        if lane.HasField("central_curve"):
            lane_centers.append(curve_to_points(lane.central_curve, state))
        if lane.HasField("left_boundary") and lane.left_boundary.HasField("curve"):
            lane_left.append(curve_to_points(lane.left_boundary.curve, state))
        if lane.HasField("right_boundary") and lane.right_boundary.HasField("curve"):
            lane_right.append(curve_to_points(lane.right_boundary.curve, state))

    road_edges = []
    for road in hdmap.road:
        for section in road.section:
            if not section.HasField("boundary"):
                continue
            boundary = section.boundary
            for edge in boundary.outer_polygon.edge:
                if edge.HasField("curve"):
                    road_edges.append(curve_to_points(edge.curve, state))
            for hole in boundary.hole:
                for edge in hole.edge:
                    if edge.HasField("curve"):
                        road_edges.append(curve_to_points(edge.curve, state))

    crosswalks = []
    for crosswalk in hdmap.crosswalk:
        if crosswalk.HasField("polygon"):
            crosswalks.append(polygon_to_points(crosswalk.polygon, state))

    stop_lines = []
    for stop_sign in hdmap.stop_sign:
        stop_lines.extend(curve_to_points(curve, state) for curve in stop_sign.stop_line)
    for signal in hdmap.signal:
        stop_lines.extend(curve_to_points(curve, state) for curve in signal.stop_line)

    signal_boundaries = []
    for signal in hdmap.signal:
        if signal.HasField("boundary"):
            signal_boundaries.append(polygon_to_points(signal.boundary, state))

    log_line_strips("hdmap/lane_center", lane_centers, [80, 180, 255], radius=0.05)
    log_line_strips("hdmap/lane_left_boundary", lane_left, [245, 245, 245], radius=0.07)
    log_line_strips("hdmap/lane_right_boundary", lane_right, [245, 245, 245], radius=0.07)
    log_line_strips("hdmap/road_boundary", road_edges, [255, 210, 80], radius=0.1)
    log_line_strips("hdmap/crosswalk", crosswalks, [80, 220, 120], radius=0.08)
    log_line_strips("hdmap/stop_line", stop_lines, [255, 80, 80], radius=0.12)
    log_line_strips("hdmap/signal_boundary", signal_boundaries, [255, 120, 255], radius=0.08)

    print(
        "Logged HDMap "
        f"{hdmap_path}: lanes={len(hdmap.lane)} roads={len(hdmap.road)} "
        f"crosswalks={len(hdmap.crosswalk)} stop_signs={len(hdmap.stop_sign)} signals={len(hdmap.signal)}",
        file=sys.stderr,
    )
    state["hdmap_logged"] = True


def log_hdmap_if_ready(state: dict) -> None:
    if state.get("hdmap_path"):
        log_hdmap_static(state)


def normalize(values: np.ndarray, low: float | None = None, high: float | None = None) -> np.ndarray:
    if values.size == 0:
        return values.astype(np.float32)
    lo = float(np.nanpercentile(values, 2) if low is None else low)
    hi = float(np.nanpercentile(values, 98) if high is None else high)
    if hi <= lo:
        hi = lo + 1.0
    return np.clip((values.astype(np.float32) - lo) / (hi - lo), 0.0, 1.0)


def colorize_points(points: np.ndarray, intensities: np.ndarray, mode: str) -> np.ndarray:
    if mode == "intensity":
        gray = normalize(intensities, 0, 255)
        return (np.stack([gray, gray, gray], axis=1) * 255).astype(np.uint8)

    if mode == "height":
        z = normalize(points[:, 2])
        colors = np.zeros((points.shape[0], 3), dtype=np.float32)
        colors[:, 0] = np.clip((z - 0.45) * 2.5, 0, 1)          # red on high points
        colors[:, 1] = np.clip(1.0 - np.abs(z - 0.5) * 1.8, 0, 1)  # green mid
        colors[:, 2] = np.clip((0.55 - z) * 2.2, 0, 1)          # blue low
        return (colors * 255).astype(np.uint8)

    if mode == "range":
        distance = np.linalg.norm(points[:, :2], axis=1)
        d = normalize(distance)
        colors = np.zeros((points.shape[0], 3), dtype=np.float32)
        colors[:, 0] = np.clip(1.5 - 2.0 * d, 0, 1)  # near red/orange
        colors[:, 1] = np.clip(1.0 - np.abs(d - 0.45) * 2.0, 0, 1)
        colors[:, 2] = np.clip((d - 0.25) * 1.4, 0, 1)  # far blue
        return (colors * 255).astype(np.uint8)

    # "semantic-lite": this is not true semantic segmentation. It is a common
    # field-review style heuristic that makes lidar structure easier to read.
    z = points[:, 2]
    z_rel = z - np.nanpercentile(z, 5)
    intensity = intensities.astype(np.float32)
    colors = np.zeros((points.shape[0], 3), dtype=np.uint8)

    ground = z_rel < 0.35
    low_obstacle = (z_rel >= 0.35) & (z_rel < 1.2)
    vegetation_like = (z_rel >= 1.2) & (intensity < 180)
    strong_reflector = intensity >= 180
    high_structure = (z_rel >= 1.2) & ~vegetation_like & ~strong_reflector

    colors[ground] = np.array([210, 170, 70], dtype=np.uint8)        # ground: yellow/brown
    colors[low_obstacle] = np.array([235, 120, 45], dtype=np.uint8)  # curbs/cars: orange
    colors[vegetation_like] = np.array([35, 185, 65], dtype=np.uint8)  # foliage-like: green
    colors[high_structure] = np.array([70, 145, 230], dtype=np.uint8)  # poles/buildings: blue
    colors[strong_reflector] = np.array([220, 245, 255], dtype=np.uint8)  # signs/reflectors
    return colors


def log_pointcloud(
    message_bytes: bytes,
    publish_ns: int,
    state: dict,
    max_points: int,
    color_mode: str,
    point_origin: np.ndarray | None = None,
    entity: str = "lidar/up/points",
) -> None:
    msg = pointcloud_pb2.PointCloud()
    msg.ParseFromString(message_bytes)
    if not msg.point:
        return
    message_ns = seconds_to_ns(msg.measurement_time) if msg.HasField("measurement_time") else None
    timestamp_ns = choose_time_ns(publish_ns, message_ns, state)

    count = len(msg.point)
    step = 1
    if max_points and count > max_points:
        step = int(np.ceil(count / max_points))

    points = np.empty(((count + step - 1) // step, 3), dtype=np.float32)
    intensities = np.empty((points.shape[0],), dtype=np.uint8)
    for out_i, point in enumerate(msg.point[::step]):
        points[out_i] = (point.x, point.y, point.z)
        intensities[out_i] = min(int(point.intensity), 255)
    if point_origin is not None:
        points = (points.astype(np.float64) - point_origin).astype(np.float32)

    colors = colorize_points(points, intensities, color_mode)

    set_rerun_time(publish_ns, message_ns, state)
    rr.log(
        entity,
        rr.Points3D(points, colors=colors, radii=0.03),
    )


def camera_entity_name(topic: str) -> str:
    name = topic.removeprefix("/apollo/camera/").removesuffix("/compressed")
    return "camera/" + name.replace("/", "_")


def collect_camera_frame(
    channel: str,
    payload: bytes,
    publish_ns: int,
    state: dict,
) -> None:
    h264, message_ns = parse_h264_message(payload)
    if not h264:
        return
    timestamp_ns = choose_time_ns(publish_ns, message_ns, state)
    camera = state["cameras"].setdefault(
        channel,
        {
            "path": state["tmp_dir"] / f"{len(state['cameras']):02d}.h264",
            "publish_timestamps": [],
            "message_timestamps": [],
        },
    )
    with camera["path"].open("ab") as out:
        out.write(h264)
    camera["publish_timestamps"].append(publish_ns)
    camera["message_timestamps"].append(message_ns or publish_ns)


def video_asset_path(output_path: str, video_dir: str | None, topic: str) -> Path:
    output = Path(output_path)
    base_dir = Path(video_dir) if video_dir else output.with_suffix("").parent / f"{output.with_suffix('').name}_videos"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / f"{camera_entity_name(topic).replace('/', '_')}.mp4"


def remux_camera_to_mp4(topic: str, camera: dict, output_path: str, video_dir: str | None) -> Path | None:
    timestamps = camera["message_timestamps"]
    if not timestamps:
        return None
    duration_sec = max((timestamps[-1] - timestamps[0]) / 1e9, 1e-3)
    fps = max((len(timestamps) - 1) / duration_sec, 1.0) if len(timestamps) > 1 else 25.0
    mp4_path = video_asset_path(output_path, video_dir, topic)
    # Force a browser-friendly, seek-friendly MP4 instead of embedding decoded
    # frames in Rerun. The raw H264 packets in this dataset are missing enough
    # stream metadata for reliable remuxing, and sparse keyframes cause WebCodecs
    # seek failures in the web viewer.
    transcode_cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "warning",
        "-probesize",
        "100M",
        "-analyzeduration",
        "100M",
        "-r",
        f"{fps:.6f}",
        "-i",
        str(camera["path"]),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-profile:v",
        "baseline",
        "-level",
        "4.0",
        "-pix_fmt",
        "yuv420p",
        "-bf",
        "0",
        "-g",
        "10",
        "-keyint_min",
        "10",
        "-sc_threshold",
        "0",
        "-x264-params",
        "repeat-headers=1",
        "-movflags",
        "+faststart",
        str(mp4_path),
    ]
    result = subprocess.run(transcode_cmd, check=False)
    if result.returncode != 0 or not mp4_path.exists() or mp4_path.stat().st_size == 0:
        print(f"Failed to create MP4 for {topic}", file=sys.stderr)
        return None
    return mp4_path


def frame_timestamps(timestamps: list[int], frame_count: int) -> list[int]:
    if frame_count <= 0 or not timestamps:
        return []
    if frame_count == len(timestamps):
        return timestamps
    if frame_count == 1:
        return [timestamps[0]]
    sample_idx = np.linspace(0, len(timestamps) - 1, num=frame_count)
    return [timestamps[int(round(i))] for i in sample_idx]


def log_camera_video_references(topic: str, camera: dict, mp4_path: Path, state: dict) -> int:
    video_entity = camera_entity_name(topic) + "/video_asset"
    frame_entity = camera_entity_name(topic)
    video_asset = rr.AssetVideo(path=mp4_path)
    rr.log(video_entity, video_asset, static=True)

    video_frame_ns = list(video_asset.read_frame_timestamps_nanos())
    if not video_frame_ns:
        # Fallback to message-count based frame times if the MP4 has no index.
        duration_ns = max(camera["message_timestamps"][-1] - camera["message_timestamps"][0], 1)
        count = len(camera["message_timestamps"])
        video_frame_ns = [int(i * duration_ns / max(count - 1, 1)) for i in range(count)]

    publish_ts = frame_timestamps(camera["publish_timestamps"], len(video_frame_ns))
    message_ts = frame_timestamps(camera["message_timestamps"], len(video_frame_ns))
    for idx, video_ns in enumerate(video_frame_ns):
        publish_ns = publish_ts[idx] if idx < len(publish_ts) else camera["publish_timestamps"][0]
        message_ns = message_ts[idx] if idx < len(message_ts) else publish_ns
        set_rerun_time(publish_ns, message_ns, state, sequence=state["message_index"] + idx)
        rr.log(frame_entity, rr.VideoFrameReference(nanoseconds=int(video_ns), video_reference=video_entity))
    print(f"Logged {len(video_frame_ns)} video frame references for {topic}: {mp4_path}", file=sys.stderr)
    return len(video_frame_ns)


def decode_and_log_cameras(state: dict, camera_max_frames: int) -> int:
    rendered = 0
    for topic, camera in state["cameras"].items():
        frames_dir = state["tmp_dir"] / f"frames_{len(topic)}_{abs(hash(topic))}"
        frames_dir.mkdir(parents=True, exist_ok=True)
        pattern = str(frames_dir / "frame_%06d.png")
        cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(camera["path"]),
            pattern,
        ]
        subprocess.run(cmd, check=False)

        image_paths = sorted(frames_dir.glob("frame_*.png"))
        if camera_max_frames:
            image_paths = image_paths[:camera_max_frames]

        timestamps = camera["message_timestamps"]
        if image_paths and timestamps:
            # Raw H264 decoding can skip packets before SPS/PPS/keyframes. There is
            # no reliable per-frame PTS in the raw Annex-B stream, so map decoded
            # frames across the camera message-time span instead of assuming one
            # decoded image per record message.
            if len(image_paths) == 1:
                frame_timestamps = [timestamps[0]]
            else:
                sample_idx = np.linspace(0, len(timestamps) - 1, num=len(image_paths))
                frame_timestamps = [timestamps[int(round(i))] for i in sample_idx]
        else:
            frame_timestamps = []
        entity = camera_entity_name(topic)
        for idx, image_path in enumerate(image_paths):
            if idx >= len(frame_timestamps):
                break
            timestamp_ns = frame_timestamps[idx]
            image = np.asarray(Image.open(image_path).convert("RGB"))
            set_rerun_time(timestamp_ns, timestamp_ns, state, sequence=state["message_index"] + idx)
            rr.log(entity, rr.Image(image))
            rendered += 1
        print(f"Decoded {min(len(image_paths), len(timestamps))} frames for {topic}", file=sys.stderr)
    return rendered


def log_camera_videos(state: dict, output_path: str, video_dir: str | None) -> int:
    rendered = 0
    for topic, camera in state["cameras"].items():
        mp4_path = remux_camera_to_mp4(topic, camera, output_path, video_dir)
        if mp4_path is None:
            continue
        rendered += log_camera_video_references(topic, camera, mp4_path, state)
    return rendered


def main() -> int:
    args = parse_args()
    proc = run_dumper(args)
    if proc.stdout is None:
        raise RuntimeError("failed to capture dumper stdout")

    rr.init("apollo_record_viewer", spawn=False)
    rr.save(args.output)
    rr.log("/", rr.ViewCoordinates.RIGHT_HAND_Z_UP, static=True)

    with tempfile.TemporaryDirectory(prefix="apollo_rerun_") as tmp:
        state = {
            "start_ns": None,
            "message_index": 0,
            "publish_start_ns": None,
            "message_start_ns": None,
            "origin": None,
            "trajectory": [],
            "no_center": args.no_center,
            "tmp_dir": Path(tmp),
            "cameras": {},
            "time_mode": args.time_mode,
            "hdmap_path": args.hdmap,
            "hdmap_logged": False,
            "hdmap_flat_z": False,
            "deskew_origin": None,
        }
        log_hdmap_if_ready(state)

        handled = 0
        camera_topics = set(selected_camera_topics(args).values())
        for line in proc.stdout:
            event = json.loads(line)
            if event["op"] != "msg":
                continue

            timestamp_ns = int(event["timestamp_ns"])

            channel = event["channel"]
            payload = base64.b64decode(event["data_b64"])
            if channel == LOCALIZATION_TOPIC:
                log_localization(payload, timestamp_ns, state)
                handled += 1
            elif channel == POINTCLOUD_TOPIC:
                log_pointcloud(payload, timestamp_ns, state, args.max_points, args.color_mode)
                handled += 1
            elif channel == DESKEW_CLOUD_TOPIC:
                log_deskew_pointcloud(payload, timestamp_ns, state, args.max_points, args.color_mode)
                handled += 1
            elif channel in camera_topics:
                collect_camera_frame(channel, payload, timestamp_ns, state)

            state["message_index"] += 1

        ret = proc.wait()
        if ret != 0:
            return ret
        if args.camera_mode == "video":
            handled += log_camera_videos(state, args.output, args.video_dir)
        else:
            handled += decode_and_log_cameras(state, args.camera_max_frames)
        log_hdmap_if_ready(state)

    print(f"Wrote {args.output} with {handled} rendered messages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
