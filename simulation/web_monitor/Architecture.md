# Architecture

## Overview

```text
[Apollo bag / .rrd / .mcap]
        │
        ▼
┌───────────────────┐     gRPC (--port)      ┌────────────────────────────┐
│ web_monitor_main  │ ─────────────────────► │ forked rerun-cli (0.37.1)  │
│ (C++ launcher)    │   + optional           │  native / --web-viewer     │
└───────────────────┘   --web-viewer HTTP    └─────────────┬──────────────┘
                                                          │
                          browser ◄── HTTP :9090 +        │
                          ?url=rerun+http://…:9876/proxy  │
                                                          ▼
                                              ┌───────────────────────┐
                                              │ re_viewer + AD shell  │
                                              │ (Carolanne / layouts) │
                                              └───────────┬───────────┘
                                                          │ query
                                              ┌───────────▼───────────┐
                                              │ re_chunk_store /      │
                                              │ re_importer (MCAP…)   │
                                              └───────────────────────┘
```

数据写入可由外部 SDK 完成；本包核心是 **Viewer 壳 + 布局 + Apollo 启动/打包**。

## Modules

| 模块 | 路径 | 职责 |
|------|------|------|
| 启动器 | `web_monitor_main.cc` | 解析 gflags，设置 `AD_LAYOUT_DIR`，exec `bin/rerun` |
| 构建包装 | `BUILD`、`scripts/build_viewer.sh` | Cargo 编 Viewer；genrule 拷贝 `bin/rerun`；apollo_package 安装 |
| AD UI 壳 | `rerun/.../ui/ad_shell.rs` | Source/Layout/Panel/Sim、主题、布局 `include_bytes`、窗口化播放 |
| 仿真侧栏 | `rerun/.../ui/ad_sim.rs` | 与 Source 等互斥停靠；固定 Config / Tasks 标签，三类任务与实时详情；View config 复制历史配置至可编辑表单，新提交不改旧任务 |
| 视口 Layers | `ad_shell` + `ad_layers.rs`，3D 左上角 | 语义图层树、父子复选框；空间 View 的 EntityBehavior.visible 独立于缓存/TF 加载 |
| 应用状态 | `rerun/.../app_state.rs` | 侧栏优先 AD shell；关 welcome；时间面板收起 |
| 播放条 | `rerun/.../re_time_panel/` | scene_editor 风格媒体栏；时钟/步长下拉、实时可编辑原始时间戳，无设置菜单/复位/跳到终点动作，保留 `TimeControlCommand` 与回执缓存，见 `docs/PLAYBACK_BAR.md` |
| Web 资源 | `re_web_viewer_server/web_viewer/` | wasm/js/`index.html`（full-bleed / 无官方顶栏） |
| 布局源 | `blueprints/` + `layouts/` + `re_viewer/layouts/` | 生成 / 分发 / 嵌入 `.rbl` |
| 大文件 API | `POST /api/open_local` | `.rrd`/`.rbl`：Host 整文件流推 |
| 窗口化播放 | `POST /api/playback_window` | `.mcap`：按时间窗 + Panel 勾选 topic 过滤导入（默认不含点云） |
| 录制会话恢复 | `ad_playback.rs` + `ad_shell.rs` + `web_tools.rs` | 静态 source descriptor 绑定真实源文件；每页 bookmark 保存精确游标/时钟/图层；布局激活及暂停 seek 提交后共用查询绑定；同一未变化文件保持稳定 recording ID，见 `docs/PLAYBACK_SESSION.md` |
| 算法调试窗口 | `rerun/.../ui/ad_debug_view.rs` + `ad_debug_panels.rs` | 八类只读工具作为 AdDebug ViewClass 停靠，配置保存在 blueprint 的 view/ad_config，共享播放时钟、独立异步响应 |
| 3D 仪表盘 | `re_viewer/ui/ad_dashboard.rs` + `re_view_spatial/ad_dashboard.rs` | 共享有界标量窗口、按游标选择真实 chassis latest-at，窗内底部居中收起/展开 |
| 相机导航 | `re_view_spatial/eye.rs` + `ui_3d.rs` | 每个 View 独立的俯视锁定/主车跟随状态；真实 view-space pose 的位置与水平 heading 驱动后上方追车或车头朝上俯视，统一约束拖拽/缩放/双击；不逐帧写 blueprint |
| 调试查询 API | `POST /api/debug_query` → `commands/debug_query.rs` → `tools/apollo_record_tools/mcap_debug_query.py` | 持久 Python worker，protobuf 按需解码、有界 LRU topic 索引、字段/曲线/轨迹/状态/频率查询 |

## Dependencies

- **对上**：Apollo `cyber` / `common`（包声明）；运行时主要依赖已安装的 `rerun` 二进制与 layouts
- **对内**：`ad_shell` → `re_viewer_context` / `re_data_source` / `re_importer` / egui
- **布局生成**：Python `rerun` SDK（编码可能需 `rrd migrate` 对齐 0.37）

## Data Flow

1. **打开录制**：CLI 参数 / Source 下拉选择模式 + Open Path  
   - `.rrd`/`.rbl`：wasm → host `open_local` → 整文件 stream → proxy → Viewer  
   - `.mcap` / convert 产出：wasm → host `mcap_topics` + Topics 勾选 → `playback_window`（2 秒窗口、1 秒回看、目标预加载 5 秒，首窗兼顾 H264 关键帧）→ 过滤 `McapImporter` → proxy → 数据入库回执确认缓存条  
   - **禁止**对大 MCAP 再走整包 `open_local`（会堵 gRPC / 顶满内存）
2. **MCAP**：`re_importer`（需显式 feature：`mcap` 等）；Apollo protobuf 坏 schema 软跳过；可视化依赖 Foxglove 语义通道（见 `PLAYBACK_AFTER_CONVERT.md`）
3. **Layout**：加载嵌入 `.rbl` → 导入 blueprint，**retarget** 到当前 `active_recording().store_id()`；无数据时可 seed workspace recording
4. **播放时钟**：仅 `publish_time` / `message_time`，默认 `publish_time`；原始 Topic 与语义数据共用时间提取，缓存覆盖两种时钟的实际时间戳窗口。初始化前不发送面板查询。详见 `docs/CLOCK_CONTRACT.md`。
   - 时钟下拉切换暂停在原有绝对纳秒时间戳，保持进度位置，便于比较两个时钟下的数据。
5. **Panel**：算法调试工具窗口；直接查询原始 MCAP，无需勾选 3D 播放 topic。播放 topic 勾选在 3D 左上角，点云默认关闭。
6. **Debug query**：独立于 gRPC 可视化导入；以当前播放轴 latest-at 查询消息，曲线保留各 topic 原始采样时间。字段缺失/预算/超时显式呈现，详见 `docs/DEBUG_PANELS.md`。
7. **空间语义 v11**：`tools/apollo_record_tools/ad_scene.py` 预索引定位与静态外参，float64 重定位到 wm_map_local；点云采用测量时刻之前的真实定位与 TF。`re_mcap/decoders/ad_scene.rs` 解码规划 LineStrips3D，protobuf 解码器在 /vehicle 放置 GLB 与当前 Pose。缺失雷达外参显式告警并隔离传感器帧，禁止伪造 identity 对齐。
8. **Layout 编辑**：Panel 抽屉操作真实 ViewportBlueprint；Custom 保存 blueprint 字节（包括各工具配置），浏览器 app state 持久化；导出标准 .rbl。默认布局生成源是 `scripts/generate_layouts.py`。详见 `docs/AD_LAYOUTS.md`。
9. **图层与颜色 v12**：纯 Rust catalog 将语义 topic 归一为 sensing/planning/localization/perception/prediction；原始 proto 留在 Inspector。只在 recording/blueprint/view 集合/勾选状态变化时写空间 View 的实体可见性覆盖，隐藏不删除数据；TF/pose 依赖可隐藏加载。Points3D CPU 缓存计算固定高度色阶，无显式 colors 的 `/lidar/` 点云使用黄色基调。感知框和预测假设用可变长多折线协议、同一 wm_map_local 坐标系。
10. **HD map v14**：`tools/apollo_record_tools/hd_map.py` 迁移 scene_editor 的路面三角化和边界/中心线 ribbon；严格解析 Apollo Map protobuf，与主车共用定位原点。仿真读取并校验任务地图快照，普通 record 在 Source 指定地图。三个 `/hdmap/` 通道导入为静态 Mesh3D，Layers → map 控制显隐；缓存键包含地图内容。详见 `docs/HD_MAP.md`。

## Runtime

| 模式 | 说明 |
|------|------|

| Web（默认） | `--web-viewer --web-viewer-port 9090`，gRPC `--port 9876`，`--server-memory-limit 8GiB`；**单端口入口**：`http://HOST:9090/` 自动连 proxy，无需 `?url=`；`--grpc_host` / `--web_port` / env 覆盖浏览器端 host/port |
| Native | `--native` 或 `--web_viewer=false` |
| 环境变量 | `WEB_MONITOR_RERUN` 覆盖二进制；`AD_LAYOUT_DIR` 布局目录；`WEB_MONITOR_OPEN_ROOTS` 限制 open_local 路径前缀 |

## Deployment

```bash
bash scripts/build_viewer.sh
# Apollo 工作区：
buildtool build -p simulation   # 或文档指定的 package；避免 backup 树冲突
```

安装产物（典型）：

- `/opt/apollo/neo/bin/web_monitor_main`
- `/opt/apollo/neo/share/simulation/web_monitor/bin/rerun`
- `/opt/apollo/neo/share/simulation/web_monitor/layouts/*.rbl`

## Constraints

Simulation tasks use a separate persistent JSON-lines worker behind `/api/sim`;
the viewer only edits/polls the queue. Sim is a resizable docked sidebar, with
Simulation Config / Simulation Tasks tabs over one scrolling content area.
Accepted submissions select Tasks; selecting a task opens live detail without
modifying the draft. An explicit View config action copies its submitted config
into the editable form, preserving extra fields; Start always enqueues a new ID.
Historical jobs are never edited. Task groups preserve backend stages and FIFO
order; finished includes failures and cancellation with their original statuses.
Both bag and world inputs use the shared
`simulation/simulator` process and algorithm DAG lifecycle. See
[`docs/SIMULATION_TASKS.md`](docs/SIMULATION_TASKS.md) for the scheduling contract,
artifact format, replay path, and repeatability scope.

`configuration_tools.py` applies the explicitly selected profile to the workspace
using AEM-style symlinks, writes map/vehicle flags and derives half_vehicle_width
from protobuf width / 2. A workspace lock covers application and execution;
per-task configuration is then frozen. No new backups, rollback or recovery are
performed; invalid inputs and application failures are surfaced. Native
`environment_tools` applies flags before map loading and validates them after each
module initializes. `configuration.json` and analysis expose the effective paths.

- 禁止用 overlay/patch 栈替代对 `rerun/` 原文件的修改
- 布局变更必须同步 `layouts/` 与 `re_viewer/layouts/` 并重编
- 0.37 无独立 WebSocket 端口；旧 `--ws_server_port` gflag 仅兼容、忽略
- 升级问题与决策以 `docs/RERUN_UPGRADE.md` 为准

## Architecture Decisions

- **Vendored fork**：深度定制 UI，将 Rerun 源码作为一等产品代码维护，而非薄包装依赖
- **Cargo + Bazel 分工**：避免 `rules_rust` 全量编巨型 workspace
- **大文件 host 流推**：浏览器堆限制下的唯一产品路径（P25）
- **布局内存导入**：绕过 web Viewer catalog 对 blueprint 的不可靠路径（P22）
- **主题运行时覆盖**：0.37 无旧 `design_tokens.json`，用 `apply_ad_theme` 叠 Carolanne
