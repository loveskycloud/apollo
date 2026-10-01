# Project

## 项目名称

`simulation/web_monitor` — Apollo 仿真可视化监视器（基于 vendored Rerun Viewer）

## 项目目标

为 Apollo 仿真 / 录制回放提供 **AD 定制 Viewer**：

- 左侧方块导航：Source / Layout / Panel / Sim（Carolanne 主题）
- Layout 一键切换 Planning / Perception / Control（嵌入 `.rbl`）
- 支持 `.rrd` / `.mcap` / `.rbl`；大文件走本机读盘 + gRPC 流推，避免整包进浏览器
- C++ 启动器 `web_monitor_main` 包装 forked `rerun` CLI，接入 Apollo buildtool 安装

本包拥有 **Viewer / UI**；数据侧由 C++/Python SDK 或 bag→MCAP 转换链路写入。

## 技术栈

| 层 | 技术 |
|----|------|
| Viewer | Rust，vendored **Rerun 0.37.1**（`rerun/`） |
| 工具链 | Rust **1.96.0**，`wasm32-unknown-unknown`；可选 `wasm-opt` |
| 启动器 | C++17 + gflags（`web_monitor_main.cc`） |
| 构建 | Cargo 编 Viewer；Bazel/`buildtool` 编 launcher + 打包 `bin/rerun`、layouts |
| 布局 | Python `rerun-sdk` + `blueprints/ad_blueprint.py` → `.rbl` |
| 传输 | Rerun 0.37+：**gRPC**（`--port`）；Web UI：`--web-viewer` / HTTP |

## 关键目录

```text
simulation/web_monitor/
├── AGENTS.md                 # Agent 入口与工程策略
├── Project.md / Architecture.md / Current-Status.md / TODO.md
├── README.md                 # 人类向快速说明
├── BUILD / cyberfile.xml     # Apollo 包与 Bazel
├── web_monitor_main.cc       # C++ 启动器
├── bin/rerun                 # 编好的 forked CLI（产物）
├── layouts/*.rbl             # Planning / Perception / Control 布局
├── blueprints/               # 生成 .rbl 的 Python blueprint
├── scripts/
│   ├── build_viewer.sh       # cargo 编 wasm + rerun-cli → bin/rerun
│   └── generate_layouts.py
├── docs/
│   ├── RERUN_UPGRADE.md      # 0.19.1→0.37.1 升级与问题手册（权威）
│   └── PLAYBACK_AFTER_CONVERT.md  # 转换后无法播放排查
├── .cursor/rules/            # 本地 Agent 规则
└── rerun/                    # 一等产品源码（fork，非依赖）
    └── crates/
        ├── viewer/           # AD UI：ad_shell、time_panel、web assets
        ├── store/            # 数据源 / gRPC / importer
        └── top/              # rerun CLI
```

## 关键依赖

- Apollo 容器环境（`aem` / docker）+ `buildtool`
- `rustup` 工具链 **1.96.0**（含 wasm target）
- 系统库：`libudev-dev` 等（见 `docs/RERUN_UPGRADE.md` P5）；`getifaddrs`（glibc / macOS 均内置）
- 生成布局：`rerun-sdk==0.37.1`，与当前 Viewer 编码一致；入口为 `scripts/generate_layouts.py`
- 可选：`binaryen`/`wasm-opt`（release wasm）；无则 debug assets

## 不可破坏的约束

1. **改行为只改原文件**：在 `rerun/`、`scripts/`、`web_monitor_main.cc` 等规范路径原地修改；禁止平行 patch/sidecar 作为真相源（见 `.cursor/rules/edit-original-files.mdc`）。
2. **UI 产品约定**：Carolanne 主题、左轨 Source/Layout/Panel/Sim、收起态媒体播放条；无官方 welcome 营销页。
3. **构建模型**：Bazel **不**用 `rules_rust` 全量编 Rerun；Cargo 出 `bin/rerun`，Bazel 编 C++ 并打包。
4. **0.37 连接模型**：无独立 `--ws-server-port`；浏览器用 `http://HOST:9090/?url=rerun+http://HOST:9876/proxy`。
5. **布局嵌入**：`ad_shell` 用 `include_bytes!` 打进 Viewer；改 `.rbl` 后须拷到 `rerun/.../layouts/` 并 **重编** Viewer。
6. **大文件路径**：网页端禁止依赖整文件读入 JS/wasm；用 host `open_local` + 流推。

## 相关文档

- 升级与部署权威：`docs/RERUN_UPGRADE.md`
- 播放排查：`docs/PLAYBACK_AFTER_CONVERT.md`
- 上游 Rerun：`rerun/ARCHITECTURE.md`、`rerun/AGENTS.md`
- Obsidian（若有）：项目文档 / 架构 / 当前状态 / 技术知识 — 需用户提供路径时再链入
