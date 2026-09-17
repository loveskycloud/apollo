"""Apollo scene coordinates and a small, explicit semantic path schema.

All world coordinates are rebased in float64 before GPU float32 conversion.
Static extrinsics come from the record, never an assumed identity transform.
"""
import base64
import bisect
import json
import sys

import numpy as np
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from modules.common_msgs.localization_msgs import localization_pb2
from modules.common_msgs.transform_msgs import transform_pb2
from modules.common_msgs.planning_msgs import planning_pb2
from modules.common_msgs.perception_msgs import perception_obstacle_pb2
from modules.common_msgs.prediction_msgs import prediction_obstacle_pb2


def path_schema():
    fd = descriptor_pb2.FileDescriptorProto(name="webmonitor/planned_path.proto", package="webmonitor", syntax="proto3")
    msg = fd.message_type.add(name="PlannedPath")
    msg.field.add(name="xyz", number=1, type=1, label=3)  # packed repeated double
    msg.field.add(name="strip_lengths", number=2, type=13, label=3)
    msg.field.add(name="labels", number=3, type=9, label=3)
    pool = descriptor_pool.DescriptorPool()
    pool.Add(fd)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName("webmonitor.PlannedPath"))


PlannedPath = path_schema()


def rotation(q):
    x, y, z, w = q
    norm = np.linalg.norm(q)
    if not np.isfinite(norm) or norm < 1e-9:
        raise ValueError("Invalid zero/non-finite pose quaternion")
    x, y, z, w = np.asarray(q) / norm
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def multiply(a, b):
    x, y, z, w = a
    u, v, s, t = b
    q = np.array([w*u+x*t+y*s-z*v, w*v-x*s+y*t+z*u,
                  w*s+x*v-y*u+z*t, w*t-x*u-y*v-z*s])
    return q / np.linalg.norm(q)


class SceneContext:
    def __init__(self):
        self.poses = []
        self.times = []
        self.origin = None
        self.extrinsics = {}
        self.warnings = set()

    def has_lidar_tf(self, frame):
        seen = set()
        while frame != "localization":
            if frame in seen or frame not in self.extrinsics:
                return False
            seen.add(frame)
            frame = self.extrinsics[frame][0]
        return True

    def read(self, process, message_clock):
        for line in process.stdout:
            event = json.loads(line)
            if event.get("op") != "msg":
                continue
            data = base64.b64decode(event["data_b64"])
            if event["channel"] == "/apollo/localization/pose":
                loc = localization_pb2.LocalizationEstimate.FromString(data)
                if not loc.HasField("pose") or not loc.pose.HasField("position") or not loc.pose.HasField("orientation"):
                    continue
                p, o = loc.pose.position, loc.pose.orientation
                ns = message_clock(loc)
                if ns is None:
                    raise ValueError("Localization has no message_time; cannot align sensor geometry")
                self.poses.append((ns, np.array([p.x, p.y, p.z]), np.array([o.qx, o.qy, o.qz, o.qw])))
            elif event["channel"] == "/tf_static":
                tf = transform_pb2.TransformStampeds.FromString(data)
                for t in tf.transforms:
                    p, o = t.transform.translation, t.transform.rotation
                    edge = (t.header.frame_id, np.array([p.x, p.y, p.z]), np.array([o.qx, o.qy, o.qz, o.qw]))
                    old = self.extrinsics.get(t.child_frame_id)
                    if old is not None and (old[0] != edge[0] or not np.allclose(old[1], edge[1]) or not np.allclose(old[2], edge[2])):
                        raise ValueError(f"Conflicting static extrinsics for {t.child_frame_id}")
                    self.extrinsics[t.child_frame_id] = edge
        if process.wait() != 0:
            raise RuntimeError("Failed to index localization/static extrinsics")
        self.poses.sort(key=lambda row: row[0])
        self.times = [row[0] for row in self.poses]
        if self.poses:
            self.origin = self.poses[0][1].copy()
        else:
            print("Scene: no localization; lidar stays in sensor coordinates, no ego/plan overlay", file=sys.stderr)
        return self

    def lidar_pose(self, ns, frame):
        if self.origin is None:
            return np.zeros(3), np.array([0., 0., 0., 1.])
        i = bisect.bisect_right(self.times, ns)-1
        if i < 0:
            # Explicitly unlocalized: no future pose is ever used.
            return None
        _, p, q = self.poses[i]
        t, r = np.zeros(3), np.array([0., 0., 0., 1.])
        seen = set()
        while frame != "localization":
            if frame in seen or frame not in self.extrinsics:
                raise ValueError(f"Missing/cyclic static TF from lidar '{frame}' to localization")
            seen.add(frame)
            frame, parent_t, parent_q = self.extrinsics[frame]
            t, r = parent_t + rotation(parent_q) @ t, multiply(parent_q, r)
        return p-self.origin + rotation(q) @ t, multiply(q, r)

    def planned_path(self, payload):
        if self.origin is None:
            raise ValueError("Cannot place planning trajectory without localization origin")
        plan = planning_pb2.ADCTrajectory.FromString(payload)
        out = PlannedPath()
        for point in plan.trajectory_point:
            p = point.path_point
            if not p.HasField("x") or not p.HasField("y"):
                raise ValueError("Planning trajectory point missing x/y")
            # Apollo often omits planning z: plan is a horizontal curve at map-origin height.
            z = p.z if p.HasField("z") else self.origin[2]
            xyz = np.array([p.x, p.y, z])-self.origin
            if not np.all(np.isfinite(xyz)):
                raise ValueError("Non-finite planning trajectory point")
            out.xyz.extend(xyz)
        return out

    def append_strip(self, out, world_points, label=""):
        if self.origin is None:
            raise ValueError("Cannot place perception/prediction without localization origin")
        points = np.asarray(world_points, dtype=np.float64).reshape((-1, 3)) - self.origin
        if not np.all(np.isfinite(points)):
            raise ValueError("Non-finite perception/prediction geometry")
        out.xyz.extend(points.flatten())
        out.strip_lengths.append(len(points))
        out.labels.append(label)

    def perceived_obstacles(self, payload):
        obstacles = perception_obstacle_pb2.PerceptionObstacles.FromString(payload)
        out = PlannedPath()
        for obstacle in obstacles.perception_obstacle:
            required = ("position", "theta", "length", "width", "height")
            if not all(obstacle.HasField(f) for f in required):
                raise ValueError(f"Obstacle {obstacle.id} missing pose or box dimensions")
            p = obstacle.position
            if not all(p.HasField(f) for f in ("x", "y", "z")):
                raise ValueError(f"Obstacle {obstacle.id} missing XYZ")
            if min(obstacle.length, obstacle.width, obstacle.height) < 0:
                raise ValueError(f"Obstacle {obstacle.id} has negative dimensions")
            c, s = np.cos(obstacle.theta), np.sin(obstacle.theta)
            corners = []
            # Apollo position is the bounding box ground center (proto contract).
            for z in (0., obstacle.height):
                for x, y in ((-1,-1), (1,-1), (1,1), (-1,1)):
                    x, y = x*obstacle.length/2, y*obstacle.width/2
                    corners.append([p.x+c*x-s*y, p.y+s*x+c*y, p.z+z])
            label = f"{obstacle.id} {perception_obstacle_pb2.PerceptionObstacle.Type.Name(obstacle.type)}"
            for i, indices in enumerate(((0,1,2,3,0), (4,5,6,7,4), (0,4), (1,5), (2,6), (3,7))):
                self.append_strip(out, [corners[j] for j in indices], label if i == 0 else "")
        return out

    def predicted_trajectories(self, payload):
        prediction = prediction_obstacle_pb2.PredictionObstacles.FromString(payload)
        out = PlannedPath()
        for obstacle in prediction.prediction_obstacle:
            for trajectory in obstacle.trajectory:
                points = []
                for point in trajectory.trajectory_point:
                    p = point.path_point
                    if not p.HasField("x") or not p.HasField("y"):
                        raise ValueError("Prediction trajectory point missing x/y")
                    if self.origin is None:
                        raise ValueError("Cannot place prediction without localization origin")
                    points.append([p.x, p.y, p.z if p.HasField("z") else self.origin[2]])
                if points:
                    label = str(obstacle.perception_obstacle.id)
                    if trajectory.HasField("probability"):
                        label += f" p={trajectory.probability:.2f}"
                    self.append_strip(out, points, label)
        return out
