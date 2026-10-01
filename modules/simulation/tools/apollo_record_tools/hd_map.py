"""Apollo HD map → static meshes, ported from scene_editor/mapRoadViz.ts.

Use the real protobuf parser and Earcut (also used by Three ShapeGeometry).
Do not copy the editor's map-centroid origin: playback geometry shares the
localization origin. Boundaries, rather than a guessed lane width, define roads.
"""
import hashlib
import json
from pathlib import Path

import mapbox_earcut
import numpy as np
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory, text_format, json_format
from modules.common_msgs.map_msgs import map_pb2


def mesh_schema():
    fd = descriptor_pb2.FileDescriptorProto(name="webmonitor/map_mesh.proto", package="webmonitor", syntax="proto3")
    msg = fd.message_type.add(name="MapMesh")
    msg.field.add(name="xyz", number=1, type=1, label=3)
    msg.field.add(name="triangle_indices", number=2, type=13, label=3)
    msg.field.add(name="rgba", number=3, type=13, label=1)
    pool = descriptor_pool.DescriptorPool()
    pool.Add(fd)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName("webmonitor.MapMesh"))


MapMesh = mesh_schema()


def map_file(path):
    path = Path(path).resolve()
    if path.is_dir():
        # Apollo uses base_map.bin when present, otherwise base_map.txt.
        path = path / ("base_map.bin" if (path / "base_map.bin").is_file() else "base_map.txt")
    if not path.is_file():
        raise ValueError(f"HD map file not found: {path}")
    return path


def resolve_map(records, explicit=None):
    """Only a task's own snapshot is automatic; never guess a bag's map."""
    snapshots = []
    for record in records:
        parent = Path(record).resolve().parent
        manifest = parent.parent / "manifest.json"
        if parent.name.startswith("run-") and manifest.is_file():
            data = json.loads(manifest.read_text())
            if data.get("config", {}).get("map"):
                path = map_file(parent.parent / "map")
                expected = data.get("sha256", {}).get("map/" + path.name)
                if not expected or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise ValueError(f"Simulation map snapshot hash mismatch: {path}")
                snapshots.append(path)
    if snapshots:
        if len(set(snapshots)) != 1:
            raise ValueError("Records reference different simulation map snapshots")
        if explicit and map_file(explicit) != snapshots[0]:
            raise ValueError("Simulation replay must use its own map snapshot, not a different map")
        return snapshots[0]
    return map_file(explicit) if explicit else None


def load_map(path):
    path = map_file(path)
    data = map_pb2.Map()
    if path.suffix == ".bin":
        data.ParseFromString(path.read_bytes())
    elif path.suffix == ".json":
        json_format.Parse(path.read_text(), data)
    elif path.suffix == ".txt":
        text_format.Parse(path.read_text(), data)
    else:
        raise ValueError(f"Expected Apollo base_map.bin / .txt / .json: {path}")
    if not data.lane:
        raise ValueError(f"HD map has no lanes: {path}")
    return data


def curve_points(curve, origin):
    points = []
    for segment in curve.segment:
        if not segment.HasField("line_segment"):
            raise ValueError("HD map curve segment has no line_segment")
        for point in segment.line_segment.point:
            if not point.HasField("x") or not point.HasField("y"):
                raise ValueError("HD map point has no x/y")
            # Preserve measured elevation; absent z uses the localization plane.
            p = np.array([point.x, point.y, point.z if point.HasField("z") else origin[2]]) - origin
            if not np.isfinite(p).all():
                raise ValueError("HD map has non-finite coordinates")
            if not points or np.linalg.norm(p - points[-1]) > 1e-8:
                points.append(p)
    return np.array(points, dtype=np.float64).reshape((-1, 3))


def append_mesh(mesh, vertices, indices):
    offset = len(mesh.xyz) // 3
    mesh.xyz.extend(np.asarray(vertices).reshape(-1))
    mesh.triangle_indices.extend(int(i) + offset for i in np.asarray(indices).reshape(-1))


def ribbon(points, half_width, lift):
    """Port of buildRibbonFromCenter: local tangent normals and paired vertices."""
    if len(points) < 2:
        raise ValueError("HD map marking has fewer than two points")
    tangent = np.vstack((points[1] - points[0], points[2:] - points[:-2], points[-1] - points[-2]))[:, :2]
    lengths = np.linalg.norm(tangent, axis=1)
    if np.any(lengths < 1e-9):
        raise ValueError("HD map marking has a degenerate tangent")
    normals = np.column_stack((-tangent[:, 1], tangent[:, 0])) / lengths[:, None] * half_width
    vertices = np.repeat(points, 2, axis=0)
    vertices[::2, :2] += normals
    vertices[1::2, :2] -= normals
    vertices[:, 2] += lift
    indices = [[2*i, 2*i+1, 2*i+2, 2*i+1, 2*i+3, 2*i+2] for i in range(len(points)-1)]
    return vertices, indices


def build_map_meshes(data, origin):
    if origin is None or not np.isfinite(origin).all():
        raise ValueError("Cannot align HD map: recording has no valid localization origin")
    origin = np.asarray(origin, dtype=np.float64)
    result = {
        "/hdmap/road_surface": MapMesh(rgba=0x2E333DFF),
        "/hdmap/lane_boundaries": MapMesh(rgba=0xEBECEEFF),
        "/hdmap/lane_centerlines": MapMesh(rgba=0x6B8F71FF),
    }
    for lane in data.lane:
        center = curve_points(lane.central_curve, origin)
        left = curve_points(lane.left_boundary.curve, origin)
        right = curve_points(lane.right_boundary.curve, origin)
        if min(len(center), len(left), len(right)) < 2:
            raise ValueError(f"Lane {lane.id.id} needs centerline and both boundaries; refusing guessed geometry")
        polygon = np.vstack((left, right[::-1]))
        triangles = mapbox_earcut.triangulate_float64(polygon[:, :2].copy(), np.array([len(polygon)], dtype=np.uint32))
        if not len(triangles):
            raise ValueError(f"Lane {lane.id.id} has no triangulatable road surface")
        polygon[:, 2] += 0.01
        append_mesh(result["/hdmap/road_surface"], polygon, triangles)
        for boundary in (left, right):
            append_mesh(result["/hdmap/lane_boundaries"], *ribbon(boundary, 0.07, 0.04))
        append_mesh(result["/hdmap/lane_centerlines"], *ribbon(center, 0.04, 0.035))
    return result
