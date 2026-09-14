"""Explicit profile/map/vehicle application, without backup or auto-recovery.

Use AEM's profiles/current + per-file symlink convention. Preflight errors and
I/O failures propagate; never substitute installed defaults for broken inputs.
"""
import contextlib
import fcntl
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

GLOBAL_FLAGS = Path("modules/common/data/global_flagfile.txt")
VEHICLE_CONFIG = Path("modules/common/data/vehicle_param.pb.txt")


def vehicle_geometry(path):
    os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
    if "/opt/apollo/neo/python" not in sys.path:
        sys.path.insert(0, "/opt/apollo/neo/python")
    from google.protobuf import text_format
    from modules.common_msgs.config_msgs.vehicle_config_pb2 import VehicleConfig
    message = VehicleConfig()
    text_format.Parse(Path(path).read_text(), message)
    vehicle = message.vehicle_param
    if not vehicle.HasField("width") or not math.isfinite(vehicle.width) or vehicle.width <= 0:
        raise ValueError(f"Vehicle width must be explicitly set, finite and positive: {path}")
    return {"vehicle_width": vehicle.width, "half_vehicle_width": vehicle.width / 2}


def rewrite_global_flags(content, map_dir, vehicle_path, half_width):
    """Replace active settings once, preserve unrelated flags and comments."""
    if not math.isfinite(half_width) or half_width <= 0:
        raise ValueError("Invalid half_vehicle_width")
    values = {"map_dir": str(map_dir), "vehicle_config_path": str(vehicle_path),
              "half_vehicle_width": repr(half_width)}
    if any(any(c.isspace() for c in value) for value in values.values()):
        raise ValueError("gflags paths cannot contain whitespace")
    seen, lines = set(), []
    for line in content.splitlines():
        match = re.match(r"^\s*--(map_dir|vehicle_config_path|half_vehicle_width)(?:\s*=|\s+)", line)
        if match:
            key = match.group(1)
            if key not in seen:
                lines.append(f"--{key}={values[key]}")
                seen.add(key)
        else:
            lines.append(line)
    lines.extend(f"--{key}={value}" for key, value in values.items() if key not in seen)
    return "\n".join(lines) + "\n"


def atomic_text(path, content):
    """Replace the named file atomically, without editing its symlink target."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".sim-config-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def update_global_flagfile(path, map_dir, vehicle_path):
    geometry = vehicle_geometry(vehicle_path)
    path = Path(path)
    atomic_text(path, rewrite_global_flags(path.read_text(), map_dir, vehicle_path,
                                          geometry["half_vehicle_width"]))
    return geometry


def _target(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe profile path: {relative}")
    target = root / relative
    if not target.parent.resolve().is_relative_to(root):
        raise ValueError(f"Profile target parent leaves workspace: {target}")
    if target.is_dir() and not target.is_symlink():
        raise ValueError(f"Expected configuration file, found directory: {target}")
    return target


def _link(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(".sim-link-" + uuid.uuid4().hex)
    try:
        temporary.symlink_to(source)
        os.replace(temporary, target)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


@contextlib.contextmanager
def configuration_lock(workspace):
    directory = Path(workspace) / "data/simulation"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".configuration.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def apply_workspace_configuration(workspace, profile, map_dir, vehicle_path):
    """Caller holds configuration_lock. No backups, rollback or recovery.

    Overwrites are explicitly requested profile changes. Preflight before any
    write; report I/O failures as failures even if some files were already set.
    """
    root = Path(workspace).resolve()
    profile = Path(profile).resolve() if profile else None
    map_dir, vehicle_path = Path(map_dir).resolve(), Path(vehicle_path).resolve()
    geometry = vehicle_geometry(vehicle_path)
    if not any((map_dir / name).is_file() for name in ("base_map.txt", "base_map.bin")):
        raise ValueError(f"Selected map has no base_map: {map_dir}")
    current = root / "profiles/current"
    if current.exists() and not current.is_symlink():
        raise ValueError(f"profiles/current must be a symlink: {current}")
    if current.is_symlink() and not current.exists():
        raise ValueError(f"Broken active profile link: {current}")
    if not profile and current.is_symlink():
        raise ValueError("An active workspace profile exists; explicitly select a profile instead of inheriting it as workspace defaults")
    planned = {}
    if profile:
        profile_vehicle = profile / VEHICLE_CONFIG
        if not profile_vehicle.is_file():
            raise ValueError(f"Selected profile has no vehicle parameters: {profile_vehicle}")
        if profile_vehicle.resolve() != vehicle_path:
            raise ValueError(f"Vehicle config does not match the selected profile; select {profile_vehicle}")
        for base, dirs, files in os.walk(profile):
            dirs.sort()
            for name in sorted(files):
                source = Path(base) / name
                relative = source.relative_to(profile)
                if relative.parts[0] in (".git", ".codex", ".agents", "profiles", "data"):
                    raise ValueError(f"Profile cannot overwrite workspace state: {relative}")
                if not source.is_file():
                    raise ValueError(f"Broken profile input: {source}")
                _target(root, relative)
                planned[relative] = source
        if current.is_symlink() and current.resolve() != profile:
            previous = current.resolve()
            for base, _, files in os.walk(previous):
                for name in files:
                    relative = (Path(base) / name).relative_to(previous)
                    target = root / relative
                    if relative not in planned and target.is_symlink() and (
                            target.resolve().is_relative_to(previous) or
                            str(current) in os.readlink(target)):
                        raise ValueError(f"Selected profile is missing active configuration {relative}; automatic recovery is disabled")
    global_path = _target(root, GLOBAL_FLAGS)
    _target(root, VEHICLE_CONFIG)
    content = (profile / GLOBAL_FLAGS).read_text() if profile and GLOBAL_FLAGS in planned else global_path.read_text()
    updated = rewrite_global_flags(content, map_dir, vehicle_path, geometry["half_vehicle_width"])
    if profile:
        _link(profile, current)
        for relative in sorted(planned):
            if relative != GLOBAL_FLAGS:
                _link(current / relative, _target(root, relative))
    if not profile and vehicle_path != root / VEHICLE_CONFIG:
        _link(vehicle_path, root / VEHICLE_CONFIG)
    atomic_text(global_path, updated)
    for relative, source in planned.items():
        if relative != GLOBAL_FLAGS and (root / relative).resolve(strict=True) != source.resolve(strict=True):
            raise RuntimeError(f"Profile application did not take effect: {relative}")
    if global_path.read_text() != updated:
        raise RuntimeError(f"Global flagfile application did not take effect: {global_path}")
    return {"profile": str(profile) if profile else "", "map_dir": str(map_dir),
            "vehicle_config_path": str(vehicle_path), **geometry,
            "global_flagfile": str(global_path), "profile_file_count": len(planned)}
