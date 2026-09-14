"""Autonomous driving viewer blueprints for Rerun.

This module is project-owned application code. It does not modify the Rerun SDK.
Layouts bind entity-path filters to viewer panes for planner / perception / control.
"""

from __future__ import annotations

import rerun as rr
import rerun.blueprint as rrb

APP_ID = "apollo_record_viewer"
DEMO_APP_ID = "apollo_ad_viewer"

LAYOUT_CHOICES = [
    "planner",
    "perception",
    "control",
    "standard",
    "wide_3d",
    "welcome",
    "cameras_only",
]

DEFAULT_CAMERAS = [
    "Front120",
    "Front30",
    "FrontLeft",
    "FrontRight",
    "Rear",
    "RearLeft",
    "RearRight",
]

# Expected entity-path conventions for AD layers.
# Log data under these paths so the matching layout shows it.
PLANNER_CONTENTS = [
    "hdmap/**",
    "trajectory",
    "vehicle/**",
    "planning/**",
    "routing/**",
    "prediction/**",
]

PERCEPTION_CONTENTS = [
    # Do NOT use /** — VideoStream under /camera/** triggers
    # "2D visualizers require a pinhole ancestor" inside Spatial3D.
    "+ /lidar/**",
    "+ /vehicle/**",
    "+ /tf/**",
    "+ /foxglove/**",
    "+ /hdmap/**",
    "+ /perception/**",
    "+ /obstacles/**",
]

CONTROL_CONTENTS = [
    "hdmap/**",
    "trajectory",
    "vehicle/**",
    "control/**",
    "chassis/**",
]

WELCOME_MARKDOWN = """\
# Apollo 自动驾驶可视化平台

欢迎使用 Rerun AD Viewer。本工具用于回放与分析 Apollo Cyber Record 数据。

## Layout 预设

| Layout | 用途 | 主要图层 |
|--------|------|----------|
| `planner` | 规划调试 | `planning/**`, `routing/**`, `prediction/**`, 地图与轨迹 |
| `perception` | 感知调试 | `lidar/**`, `perception/**`, `obstacles/**`, 相机 |
| `control` | 控制调试 | `control/**`, `chassis/**`, 车辆轨迹与控制曲线 |

## 数据图层约定

| 图层 | 实体路径 |
|------|----------|
| 高精地图 | `hdmap/*` |
| 定位轨迹 | `trajectory`, `vehicle` |
| 激光雷达 | `lidar/up/points`, `vehicle/lidar/deskew_points` |
| 相机 | `camera/*` |
| 规划 | `planning/**` |
| 感知 | `perception/**`, `obstacles/**` |
| 控制 | `control/**`, `chassis/**` |

## 操作提示

- **时间轴**：底部 Time Panel 可切换 `message_time` / `publish_time`
- **3D 视图**：左键旋转，右键平移，滚轮缩放
- **蓝图面板**：左侧 Blueprint Panel 可调整布局与视图

---
*Powered by Rerun + Apollo Cyber RT*
"""

PANEL_PRESETS: dict[str, list] = {
    "standard": [
        rrb.BlueprintPanel(state="expanded"),
        rrb.SelectionPanel(state="collapsed"),
        rrb.TimePanel(state="expanded"),
    ],
    "debug": [
        rrb.BlueprintPanel(state="expanded"),
        rrb.SelectionPanel(state="expanded"),
        rrb.TimePanel(state="expanded"),
    ],
    "demo": [],
}


def log_welcome_document(entity: str = "ui/welcome") -> None:
    rr.log(entity, rr.TextDocument(WELCOME_MARKDOWN), static=True)


def _camera_views(cameras: list[str]) -> list[rrb.Spatial2DView]:
    return [
        rrb.Spatial2DView(origin=f"camera/{name}", name=name)
        for name in cameras
    ]


def _camera_grid(cameras: list[str], columns: int = 2) -> rrb.Grid:
    return rrb.Grid(contents=_camera_views(cameras), grid_columns=columns, name="Cameras")


def _spatial_3d_view(
    name: str = "Map + Lidar",
    contents: list[str] | None = None,
) -> rrb.Spatial3DView:
    return rrb.Spatial3DView(
        origin="/",
        name=name,
        contents=contents
        or [
            "hdmap/**",
            "trajectory",
            "vehicle/**",
            "lidar/**",
            "vehicle/lidar/**",
        ],
    )


def _planner_viewport(cameras: list[str]) -> rrb.ContainerLike:
    """Planner layout: map + planning trajectory dominant, optional front camera."""
    front_cams = [c for c in cameras if c.startswith("Front")][:2] or cameras[:2]
    return rrb.Horizontal(
        _spatial_3d_view(name="Planner 3D", contents=PLANNER_CONTENTS),
        rrb.Vertical(
            *(_camera_views(front_cams) or [rrb.Spatial2DView(origin="camera/Front120", name="Front120")]),
            rrb.TextDocumentView(origin="ui/welcome", name="Planner Notes"),
            row_shares=[2, 2, 1] if len(front_cams) >= 2 else [3, 1],
            name="Planner Side",
        ),
        column_shares=[3, 2],
        name="Planner",
    )


def _perception_viewport(cameras: list[str]) -> rrb.ContainerLike:
    """Perception layout: lidar/obstacles 3D + multi-camera grid."""
    return rrb.Horizontal(
        _spatial_3d_view(name="Perception 3D", contents=PERCEPTION_CONTENTS),
        _camera_grid(cameras, columns=2),
        column_shares=[3, 2],
        name="Perception",
    )


def _control_viewport(cameras: list[str]) -> rrb.ContainerLike:
    """Control layout: vehicle/map 3D + control time-series + one front camera."""
    front = next((c for c in cameras if c.startswith("Front")), cameras[0] if cameras else "Front120")
    return rrb.Vertical(
        rrb.Horizontal(
            _spatial_3d_view(name="Control 3D", contents=CONTROL_CONTENTS),
            rrb.Spatial2DView(origin=f"camera/{front}", name=front),
            column_shares=[3, 2],
            name="Control Scene",
        ),
        rrb.Horizontal(
            rrb.TimeSeriesView(origin="control", name="Control Signals", contents=["control/**"]),
            rrb.TimeSeriesView(origin="chassis", name="Chassis", contents=["chassis/**"]),
            column_shares=[1, 1],
            name="Control Plots",
        ),
        row_shares=[3, 2],
        name="Control",
    )


def _main_viewport(cameras: list[str], layout: str) -> rrb.ContainerLike:
    if layout == "planner":
        return _planner_viewport(cameras)
    if layout == "perception":
        return _perception_viewport(cameras)
    if layout == "control":
        return _control_viewport(cameras)

    camera_grid = _camera_grid(cameras)

    if layout == "wide_3d":
        return rrb.Vertical(
            _spatial_3d_view(),
            camera_grid,
            row_shares=[3, 2],
            name="AD View",
        )

    if layout == "cameras_only":
        return camera_grid

    return rrb.Horizontal(
        _spatial_3d_view(),
        camera_grid,
        column_shares=[3, 2],
        name="AD View",
    )


def build_blueprint(
    *,
    layout: str = "perception",
    cameras: list[str] | None = None,
    timeline: str = "message_time",
    panel_mode: str = "standard",
    show_welcome: bool = True,
) -> rrb.Blueprint:
    """Build an autonomous-driving viewer blueprint.

    Primary layouts:
        - planner: planning / routing / prediction on map
        - perception: lidar + obstacles + camera grid
        - control: vehicle pose + control/chassis time series

    Extra layouts: standard, wide_3d, welcome, cameras_only
    panel_mode: standard | debug | demo
    """
    cameras = cameras or DEFAULT_CAMERAS
    effective = "standard" if layout == "welcome" else layout
    viewport = _main_viewport(cameras, effective)

    if layout == "welcome":
        root = rrb.Vertical(
            rrb.TextDocumentView(origin="ui/welcome", name="Welcome"),
            viewport,
            row_shares=[1, 4],
            name="AD Viewer",
        )
    else:
        root = viewport

    parts: list = [root]
    collapse = panel_mode == "demo"
    if not collapse:
        parts.extend(PANEL_PRESETS.get(panel_mode, PANEL_PRESETS["standard"]))

    return rrb.Blueprint(*parts, collapse_panels=collapse)


def apply_blueprint(
    *,
    layout: str = "perception",
    cameras: list[str] | None = None,
    timeline: str = "message_time",
    panel_mode: str = "standard",
    show_welcome: bool = True,
    make_active: bool = True,
) -> None:
    if show_welcome:
        log_welcome_document()

    blueprint = build_blueprint(
        layout=layout,
        cameras=cameras,
        timeline=timeline,
        panel_mode=panel_mode,
        show_welcome=show_welcome,
    )
    rr.send_blueprint(blueprint, make_active=make_active, make_default=True)
