# Rerun 升级记录（0.19.1 → 0.37.1）

> Apollo `simulation/web_monitor`：升级引擎以获得官方 MCAP 支持，同时尽量保持现有 AD 前端（Source / Layout / Panel / Sim + 播放条）外观与交互不变。

## 目标

| 项 | 说明 |
|----|------|
| 起始版本 | vendored Rerun **0.19.1**（深度定制 UI） |
| 目标版本 | Rerun **0.37.1**（含 `re_importer` MCAP） |
| UI | Carolanne 主题、左侧方块导航、Source 侧栏、媒体播放条 **视觉与布局保持不变** |
| 构建 | 仍为 **Cargo 编 Viewer + Bazel/buildtool 编 `web_monitor_main`** |
| 大文件 | 本机读盘 + WS 流推（后续项；本升级先保证编译运行） |

## TODO（完成即勾选）

- [x] 盘点定制补丁并备份到 `upgrade_backup/custom_patches/`
- [x] 选定目标版本 0.37.1（crates.io 最新稳定；含 MCAP importer）
- [x] 创建本升级文档骨架
- [x] 将 0.19.1 源码移至 `rerun_0.19.1_backup/`，解压 0.37.1 为 `rerun/`
- [x] 裸 0.37.1 在 Apollo 容器内 `cargo` 编译 + wasm 通过（需 Rust **1.96.0**）
- [x] 迁移 AD UI：`ad_shell` / welcome stub / app_state 布局顺序
- [x] 迁移播放条：`re_time_panel` 收起态媒体栏（样式不变）
- [x] 迁移主题 / CJK 字体 / `web_viewer/index.html`
- [x] 适配 API 变更（见下文「适配清单」）直至编译通过
- [x] 安装 `bin/rerun`（`rerun-cli 0.37.1`，含 AD UI）
- [x] 补全部署完整教程（本文末）
- [x] 端到端验证：`web_monitor_main` 启动 + 浏览器确认 AD chrome（Source/Layout/Panel/Sim；空启动无官方顶栏/welcome）
  - 空启动：左轨可见；底部媒体条需打开录制进入 `LocalRecording` 后出现（见 P18）
  - 文件选择 / CJK：代码已接线，建议人工再点一次 Source→Open local 与中文标签确认

## 定制文件清单（0.19.1 → 已移植到 `rerun/`）

已备份：`upgrade_backup/custom_patches/`

| 路径（相对 `rerun/`） | 作用 | 0.37 状态 |
|----------------------|------|-----------|
| `crates/viewer/re_viewer/src/ui/ad_shell.rs` | Source/Layout/Panel/Sim 壳 + Carolanne `apply_theme` | 已移植并适配 |
| `crates/viewer/re_viewer/src/app_state.rs` | 侧栏优先、强制收起时间面板、关 welcome | 已接线 |
| `crates/viewer/re_viewer/src/ui/mod.rs` | 导出 `AdShell` / `apply_ad_theme` | 已接线 |
| `crates/viewer/re_viewer/src/web_tools.rs` | wasm 隐藏 `<input type=file>` 选文件 | 已适配 `LogDataSource::File` |
| `crates/viewer/re_viewer/src/ui/welcome_screen/mod.rs` | 去掉官方欢迎页 | stub（匹配 0.37 签名） |
| `crates/viewer/re_viewer/src/startup_options.rs` | `hide_welcome_screen: true` 默认 | 已改 |
| `crates/viewer/re_viewer/src/web.rs` | wasm 启动默认隐藏 welcome | `unwrap_or(true)` |
| `crates/viewer/re_time_panel/src/time_control_ui.rs` | AD 媒体播放条 | 命令式 API |
| `crates/viewer/re_time_panel/src/time_panel.rs` | 收起态高度 56 + `media_bar_ui` | 已挂钩 |
| `crates/viewer/re_ui/src/design_tokens.rs` | CJK `DroidSansFallback` | 已插入字体族 |
| `crates/viewer/re_ui/data/DroidSansFallback.ttf` | CJK 字体文件 | 已存在 |
| `crates/viewer/re_web_viewer_server/web_viewer/index.html` | full-bleed / `#1E1A28` | 已改 |
| `crates/viewer/re_viewer/layouts/*.rbl` | Perception/Planning/Control 布局 | 已拷入 |
| `web_monitor_main.cc` / `BUILD` / `scripts/build_viewer.sh` | Apollo 包装 | 沿用 |

> 注：0.37 **无** `design_tokens.json`；Carolanne 色通过运行时 `ad_shell::apply_theme` 覆盖 egui visuals。

## 遇到的问题（持续追加）

### P0 — GitHub 直连 403

- **现象**：`github.com` / `api.github.com` 返回 403；`gh` 不可用。
- **处理**：用 `https://codeload.github.com/rerun-io/rerun/tar.gz/refs/tags/<ver>` 拉源码包成功（已验证 0.23.0～0.37.1）。

### P1 — 跨度过大（0.19.1 → 0.37.1）

- **现象**：中间约 18 个次版本；`egui`、面板、blueprint、time panel 结构均有破坏性变更。
- **处理**：以「先裸编 0.37.1 → 再逐项移植定制」为序；移植时对照备份补丁，禁止一次性大面积粘贴。

### P2 — 磁盘与产物体积

- **现象**：单份 Rerun 源码树约 10GB+（含 `target` 时更大）。
- **处理**：保留 `rerun_0.19.1_backup` 直至新版验证通过；不在备份树里跑 `cargo build`。

### P3 — Bazel 不编 Rust

- **说明**：升级不改变模型——Bazel 只编 C++/`genrule` 调 `build_viewer.sh`。无需 `rules_rust` 全量编 Rerun。

### P4 — 工具链过旧

- **现象**：主机/容器默认 `rustc 1.80.0`；0.37.1 的 `rust-toolchain.toml` 要求 **`channel = "1.96.0"`**。
- **处理**：`rustup install 1.96.0` 且 `rustup target add wasm32-unknown-unknown --toolchain 1.96.0`。

### P5 — 缺少系统库 `libudev`

- **现象**：`libudev-sys` build.rs：`No package 'libudev' found`。
- **处理**：容器内安装 `libudev-dev`（及常见 GUI/`pkg-config` 依赖）。

### P6 — `TimeControl` 不可再直接 `&mut` 改状态

- **现象**：0.37 UI 层不得直接 mutate `TimeControl`；需收集 `TimeControlCommand`，由 `time_panel.show_panel` 发送 `SystemCommand::TimeControlCommands`。
- **处理**：重写 `media_bar_ui` / 运输控件：`Pause`、`SetPlayState(Playing)`、`MoveBeginning`、`MoveEndAndFollow`、`SetSpeed`、`SetTime` / `SetTimeClamped`、`SetActiveTimeline`。`PlayState` 来自 `re_sdk_types::blueprint::components::PlayState`。

### P7 — `TimesPerTimeline` 已删除

- **现象**：0.19 用 `TimesPerTimeline` 查时间范围；0.37 无此类型。
- **处理**：改用 `entity_db.time_range_for(time_ctrl.timeline_name())` 与 `entity_db.timelines()`（`ad_shell` 与 media bar 均已改）。

### P8 — `DataSource` / `FileContents` → `LogDataSource`

- **现象**：`FileContents` API 消失；加载走 `SystemCommand::LoadDataSource(LogDataSource::…)`。
- **处理**：
  - wasm 本地文件：`LogDataSource::File { file_source, path, file: web_sys::File }`（保留 File 句柄，不整文件读入内存）。
  - HTTP：`LogDataSource::HttpUrl { url: url::Url }`。
  - 布局 `.rbl`：native 写临时文件再 `LogDataSource::File`；wasm 用 `Blob`→`File` 再加载。见「Adaptation — layout apply」。

### P9 — egui `Rounding` → `CornerRadius`；`TextEdit::frame`

- **现象**：`.rounding(` 改为 `.corner_radius(`；`TextEdit::frame` 不再接受 `bool`，需 `egui::Frame`。
- **处理**：`ad_shell` 全面用 `CornerRadius`；时钟编辑框 `.frame(egui::Frame::NONE)`。

### P10 — 设计 token 文件形态变化

- **现象**：0.37 无 `design_tokens.json`，改用 `dark_theme.ron` + 代码侧 `DesignTokens`。
- **处理**：不改 ron 色板；运行时 `apply_ad_theme(ui.ctx())` 覆盖 panel/window/widget 填充与描边为 Carolanne 紫。

### P11 — `index.html` 路径迁移

- **现象**：旧路径 `rerun/web_viewer/index.html`；新路径 `crates/viewer/re_web_viewer_server/web_viewer/index.html`。
- **处理**：在新路径应用 `#1E1A28`、canvas `top:0`、隐藏 `.header`。`build-web-viewer` 不会覆盖该 HTML。

### P12 — 编译时误写 `crate::crate::time_control_ui`

- **现象**：`time_panel.rs` 导入写成 `use crate::crate::time_control_ui::…`，导致 E0433。
- **处理**：改为 `crate::time_control_ui::…`。收起态调用 `media_bar_ui` + `paint_prototype_ruler` / `prototype_ruler_x_range`。

### P13 — LocalRecording 同时画 AD shell 与 stock 左栏会叠两层

- **现象**：0.37 `app_state` 默认在 `LocalRecording` 调 `left_panel_ui`（recordings + blueprint）；AD 产品 UI 由 `ad_shell` 替代。
- **处理**：`LocalRecording` 路径只调 `ad_shell.show`，**不再**调 `left_panel_ui`。其他路由（Loading / Redap / Table）仍保留 stock 左栏。

### P14 — Cursor `Read` 工具可能读到备份缓存

- **现象**：编辑前用编辑器 Read 可能看到 `rerun_0.19.1_backup` 旧内容。
- **处理**：改文件前一律用 shell `head`/`sed`/`wc` 核对真实磁盘内容。

### P15 — CLI 去掉 `--ws-server-port`；连接改为 gRPC proxy URL

- **现象**：0.19 启动：`--port 9876 --ws-server-port 9877 --web-viewer --web-viewer-port 9090`；浏览器 `?url=ws://localhost:9877`。0.37 报错 `unexpected argument '--ws-server-port'`。
- **处理**：
  - `web_monitor_main.cc` 不再传 `--ws-server-port`；`--web-viewer` 隐含 `--serve-web`；并加 `--hide-welcome-screen`。
  - gflag `--ws_server_port` **保留但忽略**（兼容旧脚本，非默认值时打 warning）。
  - 浏览器连接改为：`http://localhost:9090/?url=rerun+http://localhost:9876/proxy`（端口 9876 = gRPC，不再有独立 WS 9877）。

### P17 — `upgrade_backup/custom_patches/BUILD` 导致 install 冲突

- **现象**：`buildtool build -p simulation/web_monitor` 失败：`Install conflict` — `web_monitor_main` 与 `upgrade_backup/custom_patches/web_monitor_main` 都要装到 `bin/web_monitor_main`。
- **处理**：将备份目录中的 `BUILD` / `cyberfile.xml` 重命名为 `*.upgrade_backup`，避免 Bazel 把补丁树当成独立 package。

### P18 — 启动无录制时 AD 左轨不显示（仅 LocalRecording 画 shell）

- **现象**：0.37 启动路由是 `Route::welcome_page()` = `RedapServer(EXAMPLES_ORIGIN)`；`ad_shell` 原先只在 `LocalRecording` 绘制。
- **处理**：welcome/examples 路由改用 `ad_shell.show_with_app_ctx`；去掉踢回 welcome 的 redirect；隐藏 stock 顶栏。空启动无媒体条属预期，打开录制后进入 `LocalRecording` 才有底部播放条。

### P20 — Layout / Source / Panel 侧栏点不开

- **现象**：点击 Layout（或其它）方块，高亮可能变了但二级抽屉不展开。
- **原因**：egui **0.36** 将 `Panel::show_animated_inside` 标为 deprecated，且 **`is_expanded` 按值传入**；内部拖拽关闭/双击切换会改局部副本，**写不回** `self.layout_open`，动画与状态脱节。三层抽屉关闭时还会各留一条 `drag_to_open` 把手，干扰交互。
- **处理**：改为 `show_collapsible(ui, &mut self.layout_open|source_open|panel_open, …)`，并 `.drag_to_open(false)`。

### P21 — Layout `.rbl` 为 0.19 格式，0.37 无法加载

- **现象**：点击 Layout → Control/Perception/Planning 报错：`Invalid encoding options: You are trying to load an old .rrd file that's not supported by this version of Rerun`（文件如 `control.rbl`）。
- **原因**：0.19 的 `.rbl` 编码过旧，`rerun rrd migrate` **无法**从该版本升级；官方也不保证 blueprint 跨大版本兼容。
- **处理**：
  1. 用现有 `scripts/generate_layouts.py` + `blueprints/ad_blueprint.py`（Python SDK）重新生成；
  2. `rerun rrd migrate *.rbl` 升到当前 Viewer 编码；
  3. 写入 `layouts/` 与 `rerun/crates/viewer/re_viewer/layouts/`（后者被 `include_bytes!` 打进二进制）；
  4. 旧文件备份在 `upgrade_backup/layouts_0.19/`。
- **以后改布局**：改 `ad_blueprint.py` 后执行：
  ```bash
  python3 scripts/generate_layouts.py -o layouts
  ./bin/rerun rrd migrate layouts/*.rbl
  cp layouts/*.rbl rerun/crates/viewer/re_viewer/layouts/
  bash scripts/build_viewer.sh   # 必须重编，include_bytes 才会更新
  ```

### P22 — Layout 仍报 Viewer catalog / old RRD（即使 .rbl 已重生成）

- **现象**：新 `.rbl` 经 `rrd verify` 通过，但网页点 Layout 仍报 `Failed to load file via the Viewer catalog: failed to read RRD metadata`。
- **原因**：debug wasm 曾默认 `use_viewer_catalog=true`；嵌入布局走 `LoadDataSource(File)` 时被 catalog 接管，web 上对 blueprint 不可靠。
- **处理**：
  1. `apply_layout` 改为 `re_importer::import_from_file_contents` + `SystemCommand::AddReceiver`（内存导入，绕过 catalog）；
  2. 默认关闭 `experimental.use_viewer_catalog`；
  3. 布局文件用 `scripts/generate_layouts.py` 重写并 `rrd migrate`。

### P23 — 网页打开 `.mcap` 报 `No importer support`

- **现象**：Source → Browse 选 `slice_test.mcap` 报 `Data source failed: No importer support for "slice_test.mcap"`。
- **原因**：workspace 对 `re_importer` 设了 `default-features = false`；wasm 的 `re_viewer` / `re_data_source` 未再打开 `mcap` feature，故 `McapImporter` 未编进网页端。原生 `bin/rerun` 因 `rerun` crate 的 `importers` feature 统一打开了 MCAP，所以 CLI 路径正常。
- **处理**：在 `re_data_source` 与 `re_viewer` 的 `Cargo.toml` 显式启用 `re_importer` features：`image`, `video`, `mcap`, `urdf`；重建 wasm + CLI。


### P24 — `slice_test.mcap` 过了 importer，但仍报 Apollo protobuf schema

- **现象**：开启 MCAP feature 后不再出现 `No importer support`；改为 `Invalid schema apollo.localization.LocalizationEstimate: imported file '.../header.proto' has not been added`。
- **原因**：该 MCAP 内的 protobuf schema 依赖未内嵌的 `.proto` 文件（Apollo `common_msgs`），Rerun 的 protobuf decoder 无法解析。
- **处理**（已完，见 P30 / v6+）：透传改 `raw` encoding；protobuf decoder 软跳过坏 FDS；可视化只依赖自包含 Foxglove schema。与 P23 不同。


### P25 — 网页无法打开大 `.rrd` / `.mcap`（“文件太大” / 整包进浏览器）

- **现象**：Browse 选 `apollo_front120_camera_20s.rrd`（~919MB）失败或卡死；Source「Open path」原先在 wasm 上直接拒绝。
- **原因**：浏览器端 `LogDataSource::File` 会把整文件读进 JS/wasm 堆；另有 4GiB `u32::MAX` 硬限制。这不是产品可接受的大文件路径。
- **处理**：本机读盘 + gRPC 流推：
  1. `serve_web` 持有共享 `Arc<LogReceiverSet>` + keepalive；
  2. HTTP `POST /api/open_local`（body=绝对路径）在 **host** 上 `LogDataSource::File::stream` 并 `receive_set.add`；
  3. 已连接的网页 Viewer 经既有 `rerun+http://…/proxy` 收流；
  4. AD Source「Open path」在 wasm 上改为调用该 API（`.rrd` / `.rbl` / `.mcap`）。
- **可选**：`WEB_MONITOR_OPEN_ROOTS=/apollo_workspace:/home/...` 限制可打开前缀。

## 适配清单（API / 工程）— 实作结果

| 区域 | 0.19.1 | 0.37.1 实作 |
|------|--------|-------------|
| MCAP | 无内置 loader | `re_importer` + **显式 features**（P23）；Source Browse / CLI / `--recording=` |
| `PanelState` | `re_types::…` | `re_sdk_types::blueprint::components::PanelState`；时间面板强制 `Collapsed` |
| `TimeControl` / 播放条 | 直接 mutate | `Vec<TimeControlCommand>` + `media_bar_ui`；collapsed `.exact_size(56.0)` |
| `ViewerContext` | 字段较少 | `ad_shell.show(&ViewerContext, ui)`；布局加载走 `command_sender` |
| `LoadDataSource` | `DataSource` | `LogDataSource::{File,HttpUrl,…}` |
| web 文件选择 | 隐藏 input | `web_tools::pick_local_recording_files` → `LogDataSource::File` + `web_sys::File` |
| 设计 token | `design_tokens.json` | 运行时 `apply_ad_theme`（无 json） |
| CJK 字体 | `DroidSansFallback.ttf` | `design_tokens.rs::set_fonts` 插入 Proportional/Monospace |
| 欢迎页 | stub | stub + `hide_welcome_screen: true`（native Default + wasm unwrap_or） |
| 布局 `.rbl` | `FileContents` | native temp file / wasm Blob→File（见下） |

### P26 — Layout 样式无法切换 / 视口空白

- **现象**：Layout 抽屉点 Planning / Perception / Control 无效果，或应用后中心视图样式消失。
- **原因**：`.rbl` 导入时 `forced_application_id` **只**来自 `ImporterSettings::opened_store_id`（见 `re_importer::importer_rrd`）。原先写死 `application_id = "apollo_ad_viewer"` 对 `.rbl` 无效，blueprint 留在 SDK 保存时的 app id，无法挂到当前 recording。
- **处理**：`apply_layout` 取 `ctx.active_recording().store_id()` 写入 `opened_store_id`；无 recording 时 warn 并跳过。安全副本：`upgrade_backup/ported_0.37/ad_shell.rs`。
- **验收**：先 Open path 打开 `.rrd`，再切 Layout；日志应有 `Layout … retarget app_id=…`，视口出现对应 3D/相机分区。

### P27 — Layout 与数据解耦（无 recording 也要出面板）

- **要求**：Layout / 视口面板与是否打开 `.rrd`/`.mcap` **无关**；无数据也必须能切换并看到分区视图。
- **原因**：Rerun 的 `BlueprintActivationCommand` 在「该 ApplicationId 下没有任何 recording」时不会切到 `LocalRecording`（`RR-3713`），视口保持空白。
- **处理**：无 active recording 时先 `prepare_store_info` 注入稳定空 recording（`apollo_ad_viewer` / `ad_layout_workspace`），再以 `opened_store_id` 导入 `.rbl`；启动时 `ensure_default_applied` 不再等待数据。
- **验收**：空启动 → 点 Layout（或默认 Perception）→ 中心出现 3D/相机等面板分区（可无实体数据）。

### P28 — 大 bag Browse 不再拒载（去 “too large for the web viewer”）

- **现象**：Browse 选 `apollo_front120_camera_20s.rrd`（~919MiB）弹出拒载 toast。
- **原因**：旧逻辑把文件读进浏览器并硬限 512MiB。
- **处理**：Browse / Open path 一律 `POST /api/open_local`；host 按绝对路径或文件名在 bag 根目录解析后 **native 流式** 推入 gRPC proxy。不再在 wasm 里整包读入。
- **验收**：Browse 或 Open path 打开大 `.rrd` → 无拒载提示 → 视口出现数据。

### P29 — Host 已 accept stream 但前端无数据（receive_set 死锁）

- **现象**：toast 显示 `Host accepted stream`，视口仍空、无法播放。
- **原因**：`LogReceiverSet::recv` 在无界 `select` 期间一直持锁；keepalive 占住后 `/api/open_local` 的 `receive_set.add()` 永远进不去，消息到不了 gRPC proxy。
- **处理**：`recv` 改为短超时循环并释放锁；打开新 recording 时 `ensure_layout_for_active_recording` 把 AD layout 重绑到该 store。
- **验收**：Browse 大 `.rrd` → 无拒载 → 点云/相机出现 → 可 scrub/播放完整时段。
- **附**：`web_monitor_main` 传 `--server-memory-limit 8GiB`，避免 proxy 1GiB 默认丢历史。


### P30 — Convert / Loaded 后仍无法播放（游标错位 + 透传膨胀）

- **现象**：转换 100% 且 `Loaded …mcap`，视口空白；Host 未必有 `Failed to load`。
- **根因**：
  1. 播放头停在 0，而 bag 时间为绝对 ns → LatestAt 空（产品时钟仍只有 Publish / Message 两种）；
  2. 全量 Apollo **raw payload** 透传使 cache 达数 GB，UI 在 stream attach 时即报 Loaded；
  3. 历史：残缺 protobuf FDS 整包失败（P24）——已用 encoding=`raw` 的 topic 注册 + soft-fail。
- **处理**：打开后夹游标到时间轴 range 起点；`v7` 默认 schemas-only（不写 raw payload）；专文 `docs/PLAYBACK_AFTER_CONVERT.md`。
- **验收**：见该专文清单。

### Adaptation — layout apply（无 FileContents）

嵌入的 `layouts/{planner,perception,control}.rbl` 通过 `include_bytes!` 打进 `ad_shell`：

- 统一走 `re_importer::import_from_file_contents` + `SystemCommand::AddReceiver`（内存导入）。
- **必须**设 `ImporterSettings::opened_store_id = active_recording.store_id()`，否则 `.rbl` 不会 remap 到当前 recording 的 `ApplicationId`（见 P26）。

### Adaptation — app_state 绘制顺序

与 0.19 一致、适配 0.37 `Route::LocalRecording` 结构：

1. `apply_ad_theme(ui.ctx())`
2. （可选）blueprint inspect 时间面板
3. **`ad_shell.show`（先于底部时间条）** — 避免播放条钻到左轨下面
4. selection panel（右）
5. `time_panel.show_panel(..., PanelState::Collapsed, …)`，忽略拖拽展开
6. **不**画 stock `left_panel_ui`
7. `CentralPanel` fill = `ad_shell::theme::APP_BG`

### Adaptation — media bar 视觉

保持 0.19 原型行布局：

`[⏮ ▶ ⏭] · [1.0x▾] · [● Live] · [细时间轴+1px刻度] · [10ms▾] · [可编辑时钟]`

薄轴绘制：`paint_prototype_ruler`；拖拽 scrub 发 `SetTimeClamped`。

## 回滚

```bash
cd /apollo_workspace/simulation/web_monitor
# 若新版不可用：
rm -rf rerun
mv rerun_0.19.1_backup rerun
bash scripts/build_viewer.sh
# 再 buildtool / 安装 bin/rerun
```

> 注意：回滚后请勿删除 `upgrade_backup/`；它是定制补丁的安全副本。

## 部署完整教程

### 1. 环境

在 Apollo 开发容器内（示例容器名 `apollo_neo_dev_wangsheng`），以用户 `wangsheng` 运行（勿用 root 跑 cargo）：

```bash
docker exec -e HOME=/home/wangsheng \
  -e CARGO_HOME=/home/wangsheng/.cargo \
  -e RUSTUP_HOME=/home/wangsheng/.rustup \
  -u wangsheng apollo_neo_dev_wangsheng bash -lc '
source "$HOME/.cargo/env"
rustc --version   # 进入 rerun/ 目录后应自动用 1.96.0
rustup target add wasm32-unknown-unknown --toolchain 1.96.0
'
```

依赖：`libudev-dev`、`pkg-config`、常见 GUI 开发库。可选：`wasm-opt`（binaryen）——没有则 `build_viewer.sh` 自动用 debug wasm。

### 2. 编译 Viewer（推荐一键脚本）

```bash
docker exec -e HOME=/home/wangsheng \
  -e CARGO_HOME=/home/wangsheng/.cargo \
  -e RUSTUP_HOME=/home/wangsheng/.rustup \
  -u wangsheng apollo_neo_dev_wangsheng bash -lc '
source "$HOME/.cargo/env"
cd /apollo_workspace/simulation/web_monitor
bash scripts/build_viewer.sh
'
```

等价手工步骤（改 UI 后务必 **先 wasm 再 cli**，否则嵌入的 web 资源是旧的）：

```bash
cd /apollo_workspace/simulation/web_monitor/rerun
cargo run -p re_dev_tools --release -- build-web-viewer --debug
# 若有 wasm-opt：--release -g
cargo build -p rerun-cli --release --no-default-features \
  --features "native_viewer,web_viewer" -j "$(nproc)"
install -m 755 target/release/rerun ../bin/rerun
../bin/rerun --version
# 期望：rerun-cli 0.37.1 (native_viewer web_viewer) …
```

增量自检（改动后迭代）：

```bash
cargo check -p re_time_panel
cargo check -p re_viewer
```

### 3. 编译 / 安装 `web_monitor_main`（C++）

```bash
# 在 /apollo_workspace 下（不要用 -p simulation/web_monitor，见 P19）
buildtool build -p simulation
```

`BUILD` 里的 genrule 会在缺少 `bin/rerun` 时调 `scripts/build_viewer.sh`；若已编过 Viewer，可先保证：

```bash
test -x simulation/web_monitor/bin/rerun && simulation/web_monitor/bin/rerun --version
```

### 4. 运行

```bash
fuser -k 9876/tcp 9090/tcp 2>/dev/null || true
# 9877 已废弃（0.37 无独立 WebSocket 端口）
web_monitor_main
# 可选指定本地录制：
# web_monitor_main --recording=/path/to/file.rrd
# web_monitor_main --recording=/path/to/file.mcap
```

浏览器（建议无痕）：

```
http://localhost:9090/?url=rerun+http://localhost:9876/proxy
```

等价于日志里打印的：

```
http://127.0.0.1:9090?url=rerun%2Bhttp%3A%2F%2Flocalhost%3A9876%2Fproxy
```

验收清单：

- [x] 背景 Carolanne `#1E1A28`，无 Rerun HTML 顶栏（浏览器 2026-09-05 确认）
- [x] 左侧方块导航 Source / Layout / Panel / Sim（浏览器确认）
- [ ] 底部收起态媒体条（打开 `.rrd`/`.mcap` 进入 LocalRecording 后确认；空 welcome 故意不画）
- [x] 无官方 welcome 营销页
- [ ] Source → Open local 在网页上直接弹出系统文件框（无 rfd Ok）（建议人工点验）
- [ ] 中文标签字形正常（CJK fallback）（字体已嵌入；建议人工点验）
- [x] `web_monitor_main` 可启动（无 `--ws-server-port` 报错；连接 URL 见上）

### 5. MCAP

```bash
./bin/rerun /path/to/file.mcap
# 或转换：
./bin/rerun mcap convert input.mcap -o out.rrd
```

### 6. 产物位置

| 产物 | 路径 |
|------|------|
| CLI / 嵌入式 web server | `simulation/web_monitor/bin/rerun` |
| wasm / js / index.html | `rerun/crates/viewer/re_web_viewer_server/web_viewer/` |
| 定制补丁备份 | `upgrade_backup/custom_patches/` |
| 旧树 | `rerun_0.19.1_backup/`（勿删直至验收） |

---

**文档状态**：升级完成。`bin/rerun` = **0.37.1**（含 AD UI）；`web_monitor_main` 已适配 gRPC URL；浏览器已确认空启动 AD 左轨。剩余建议人工：打开录制验播放条、Source 本地选文件、CJK 字形。
