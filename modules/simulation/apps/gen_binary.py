#!/usr/bin/env python3
"""Compile Apollo and generate binary.tar.gz inside the development container.

source modules/simulation/setup.bash
gen_binary.py --type simulation
gen_binary.py --type all --output binary-all.tar.gz
"""
import argparse
import fcntl
import os
from pathlib import Path
import shutil
import sys
import tempfile

from _cli import WORKSPACE_ROOT, run_cli
from modules.simulation.tools.package.binary_packager import (
    Builder, DEFAULT_PROFILES, load_profiles, log,
)


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", "--type", default="simulation", help="Build profile; default: simulation")
    parser.add_argument("--profiles-file", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--list-profiles", action="store_true")
    parser.add_argument("--config", choices=("cpu", "gpu"), help="Override profile's Bazel config")
    parser.add_argument("--target", action="append", default=[], help="Explicit target (repeatable), replacing profile targets")
    parser.add_argument("--include", action="append", default=[], metavar="SOURCE[=DEST]", help="Include map/profile/scene data at a relative package path")
    parser.add_argument("--output", type=Path, default=WORKSPACE_ROOT / "binary.tar.gz")
    parser.add_argument("--jobs", type=int, default=min(os.cpu_count() or 1, 6))
    parser.add_argument("--bazel-arg", action="append", default=[], help="Extra Bazel build/cquery flag; use --bazel-arg=--flag=value")
    parser.add_argument("--compress-level", type=int, choices=range(1, 10), default=6)
    return parser


def validate_args(parser, args, profiles):
    if args.profile not in profiles:
        parser.error(f"Unknown profile {args.profile!r}; available: {', '.join(profiles)}")
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    for program in ("bazel", "readelf", "ldd", "patchelf"):
        if not shutil.which(program):
            parser.error(f"Missing {program}; run inside the Apollo development container")
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    profile = profiles[args.profile]
    args.config = args.config or profile.get("config", "cpu")
    return profile


def build_package(args, profile):
    # Serialise compilation and collection: CPU/GPU builds share Bazel paths.
    lock_path = WORKSPACE_ROOT / ".cache/gen_binary.lock"
    lock_path.parent.mkdir(exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("Waiting for another gen_binary.py in this workspace")
            fcntl.flock(lock, fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(prefix=".gen-binary-", dir=args.output.parent) as folder:
            Builder(args, profile, Path(folder)).build()


def main(argv=None):
    # Each CLI follows: parse -> validate -> execute -> return exit code.
    parser = make_parser()
    args = parser.parse_args(argv)
    profiles = load_profiles(args.profiles_file)
    if args.list_profiles:
        for name, profile in profiles.items():
            print(f"{name}: {profile.get('description', '')}")
        return 0
    profile = validate_args(parser, args, profiles)
    build_package(args, profile)
    return 0


if __name__ == "__main__":
    sys.exit(run_cli(main))
