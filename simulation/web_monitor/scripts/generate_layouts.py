#!/usr/bin/env python3
"""Generate fresh Planning / Perception / Control .rbl layouts for web_monitor.

Requires rerun-sdk matching the Viewer major.minor (currently 0.37.x).
Does NOT migrate old files — deletes nothing; writes new .rbl into --output-dir.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import rerun as rr
import rerun.blueprint as rrb

ROOT = Path(__file__).resolve().parents[1]

PLANNER_CONTENTS = [
    "hdmap/**",
    "trajectory",
    "vehicle/**",
    "planning/**",
    "routing/**",
    "prediction/**",
]
PERCEPTION_CONTENTS = [
    "hdmap/**",
    "vehicle/**",
    "lidar/**",
    "vehicle/lidar/**",
    "perception/**",
    "obstacles/**",
]
CONTROL_CONTENTS = [
    "hdmap/**",
    "trajectory",
    "vehicle/**",
    "control/**",
    "chassis/**",
]
DEFAULT_CAMERAS = ["Front120", "Front30", "FrontLeft", "FrontRight"]


def _cams(names: list[str]) -> list[rrb.Spatial2DView]:
    return [rrb.Spatial2DView(origin=f"camera/{n}", name=n) for n in names]


def _spatial(name: str, contents: list[str]) -> rrb.Spatial3DView:
    return rrb.Spatial3DView(origin="/", name=name, contents=contents)


def build(layout: str) -> rrb.Blueprint:
    cams = DEFAULT_CAMERAS
    if layout == "planner":
        root = rrb.Horizontal(
            _spatial("Planner 3D", PLANNER_CONTENTS),
            rrb.Vertical(*_cams(cams[:2]), row_shares=[1, 1], name="Planner Cams"),
            column_shares=[3, 2],
            name="Planner",
        )
    elif layout == "perception":
        root = rrb.Horizontal(
            _spatial("Perception 3D", PERCEPTION_CONTENTS),
            rrb.Grid(contents=_cams(cams), grid_columns=2, name="Cameras"),
            column_shares=[3, 2],
            name="Perception",
        )
    elif layout == "control":
        root = rrb.Vertical(
            rrb.Horizontal(
                _spatial("Control 3D", CONTROL_CONTENTS),
                rrb.Spatial2DView(origin="camera/Front120", name="Front120"),
                column_shares=[3, 2],
                name="Control Scene",
            ),
            rrb.Horizontal(
                rrb.TimeSeriesView(
                    origin="control", name="Control Signals", contents=["control/**"]
                ),
                rrb.TimeSeriesView(
                    origin="chassis", name="Chassis", contents=["chassis/**"]
                ),
                column_shares=[1, 1],
                name="Control Plots",
            ),
            row_shares=[3, 2],
            name="Control",
        )
    else:
        raise ValueError(f"unknown layout: {layout}")
    # AD chrome owns panels — keep stock blueprint/time panels collapsed in the .rbl.
    return rrb.Blueprint(root, collapse_panels=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "-o",
        "--output-dir",
        default=str(ROOT / "layouts"),
        help="Directory for planner.rbl / perception.rbl / control.rbl",
    )
    p.add_argument(
        "--also-embed-dir",
        default=str(ROOT / "rerun/crates/viewer/re_viewer/layouts"),
        help="Also copy into this dir (include_bytes! path). Empty to skip.",
    )
    return p.parse_args()


def main() -> int:
    if not rr.__version__.startswith("0.37"):
        print(
            f"ERROR: need rerun-sdk 0.37.x (got {rr.__version__}); "
            "pip install 'rerun-sdk==0.37.1'",
            file=sys.stderr,
        )
        return 1

    args = parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    mapping = {
        "planner": "planner",
        "perception": "perception",
        "control": "control",
    }
    prev = Path.cwd()
    os.chdir(out)
    try:
        for layout, stem in mapping.items():
            build(layout).save(stem)
            path = out / f"{stem}.rbl"
            print(f"Wrote {path} ({path.stat().st_size} bytes) sdk={rr.__version__}")
    finally:
        os.chdir(prev)

    embed = (args.also_embed_dir or "").strip()
    if embed:
        embed_dir = Path(embed)
        embed_dir.mkdir(parents=True, exist_ok=True)
        for stem in mapping.values():
            src = out / f"{stem}.rbl"
            dst = embed_dir / f"{stem}.rbl"
            dst.write_bytes(src.read_bytes())
            print(f"Copied {dst}")

    print("Done. Rebuild viewer so include_bytes picks up embed dir:")
    print("  bash scripts/build_viewer.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
