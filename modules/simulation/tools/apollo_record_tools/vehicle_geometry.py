"""Verified vehicle geometry shared by the ego model and planning ribbon."""
import hashlib
import json
import math
import os
from pathlib import Path

from google.protobuf import text_format
from modules.common_msgs.config_msgs import vehicle_config_pb2


def load_vehicle_geometry(records, explicit=None):
    snapshots = []
    for record in records:
        parent = Path(record).resolve().parent
        manifest = parent.parent / "manifest.json"
        if parent.name.startswith("run-") and manifest.is_file():
            data = json.loads(manifest.read_text())
            path = parent.parent / "vehicle/vehicle_param.pb.txt"
            expected = data.get("sha256", {}).get("vehicle/vehicle_param.pb.txt")
            if not expected or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError(f"Simulation vehicle snapshot hash mismatch: {path}")
            snapshots.append(path.resolve())
    if snapshots:
        if len(set(snapshots)) != 1:
            raise ValueError("Records reference different simulation vehicle snapshots")
        path = snapshots[0]
        if explicit and Path(explicit).resolve() != path:
            raise ValueError("Simulation replay must use its own vehicle snapshot")
    else:
        root = Path(__file__).resolve().parents[4]
        selected = explicit or os.environ.get("WEB_MONITOR_VEHICLE_CONFIG")
        if not selected:
            flags = root / "modules/common/data/global_flagfile.txt"
            for line in flags.read_text().splitlines():
                line = line.split("#", 1)[0].strip()
                if line.startswith("--vehicle_config_path="):
                    selected = line.split("=", 1)[1].strip()
            if not selected:
                raise ValueError(f"No vehicle_config_path in {flags}; select --vehicle-config")
        path = Path(selected)
        if not path.is_absolute():
            path = root / path
        path = path.resolve(strict=True)
    payload = path.read_bytes()
    config = text_format.Parse(payload.decode(), vehicle_config_pb2.VehicleConfig()).vehicle_param
    fields = ("length", "width", "height", "front_edge_to_center", "back_edge_to_center",
              "left_edge_to_center", "right_edge_to_center", "max_deceleration")
    geometry = {}
    for key in fields:
        value = getattr(config, key)
        if not config.HasField(key) or not math.isfinite(value):
            raise ValueError(f"Vehicle configuration missing/invalid {key}: {path}")
        if (value >= 0 if key == "max_deceleration" else value <= 0):
            raise ValueError(f"Vehicle configuration invalid {key}={value}: {path}")
        geometry[key] = value
    for dimension, a, b in (("length", "front_edge_to_center", "back_edge_to_center"),
                            ("width", "left_edge_to_center", "right_edge_to_center")):
        if not math.isclose(geometry[dimension], geometry[a] + geometry[b], abs_tol=1e-5):
            raise ValueError(f"Vehicle {dimension} disagrees with edge distances: {path}")
    return {**geometry, "path": str(path), "sha256": hashlib.sha256(payload).hexdigest()}
