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
    "+ /lidar/**",
    "+ /vehicle/**",
    "+ /planning/**",
    "+ /perception/**",
    "+ /hdmap/**",
    "+ /routing/**",
    "+ /prediction/**",
]
PERCEPTION_CONTENTS = [
    # Do NOT use /** — VideoStream under /camera/** triggers
    # "2D visualizers require a pinhole ancestor" inside Spatial3D.
    "+ /lidar/**",
    "+ /vehicle/**",
    "+ /planning/**",
    "+ /prediction/**",
    "+ /tf/**",
    "+ /foxglove/**",
    "+ /hdmap/**",
    "+ /perception/**",
    "+ /obstacles/**",
]
CONTROL_CONTENTS = [
    "+ /lidar/**",
    "+ /vehicle/**",
    "+ /planning/**",
    "+ /perception/**",
    "+ /prediction/**",
    "+ /hdmap/**",
]
DEFAULT_CAMERAS = ["Front120", "Front30", "FrontLeft", "FrontRight"]


def _cams(names: list[str]) -> list[rrb.Spatial2DView]:
    return [rrb.Spatial2DView(origin=f"camera/{n}", name=n) for n in names]


def _spatial(name: str, contents: list[str]) -> rrb.Spatial3DView:
    # Anchor on lidar so Points3D+CoordinateFrame resolve without a map TF root.
    return rrb.Spatial3DView(origin="/lidar/up/points", name=name, contents=contents,
        eye_controls=rrb.EyeControls3D(position=(14, -18, 22), look_target=(0, 0, 0), eye_up=(0, 0, 1)))


def _debug(name: str, preset: str) -> rrb.View:
    return rrb.View(class_identifier="AdDebug", origin=f"/debug/{preset}", contents=[], name=name)


def build(layout: str) -> rrb.Blueprint:
    cams = DEFAULT_CAMERAS
    if layout == "planner":
        root = rrb.Horizontal(
            rrb.Vertical(_spatial("Planning 3D", PLANNER_CONTENTS), _debug("Trajectory XY", "trajectory"), row_shares=[3, 2]),
            rrb.Vertical(_debug("Planning profile", "profile"), _debug("Planning message", "planning"), row_shares=[1, 1]),
            column_shares=[1.5, 1],
            name="Planner",
        )
    elif layout == "perception":
        root = rrb.Horizontal(
            _spatial("Perception 3D", PERCEPTION_CONTENTS),
            rrb.Grid(contents=_cams(cams), grid_columns=2, name="Cameras"),
            column_shares=[1.5, 1],
            name="Perception",
        )
    elif layout == "control":
        root = rrb.Horizontal(
            rrb.Vertical(_spatial("Control 3D", CONTROL_CONTENTS), _debug("State transitions", "states"), row_shares=[3, 2]),
            rrb.Vertical(_debug("Speed tracking", "speed"), _debug("Steering feedback", "steering")),
            rrb.Vertical(_debug("Tracking errors", "errors"), _debug("Pedals", "pedals")),
            column_shares=[4/3, 1, 1],
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

    # Must match ad_shell.rs AD_APPLICATION_ID so blueprints activate on the AD workspace.
    application_id = "apollo_ad_viewer"
    mapping = {
        "planner": "planner",
        "perception": "perception",
        "control": "control",
    }
    for layout, stem in mapping.items():
        path = out / f"{stem}.rbl"
        build(layout).save(application_id, path=path)
        print(f"Wrote {path} ({path.stat().st_size} bytes) sdk={rr.__version__}")

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
