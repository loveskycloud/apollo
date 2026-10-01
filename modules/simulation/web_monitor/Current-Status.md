# Current Status

## 当前目标

统一 LogSim / WorldSim 仿真入口、确定性调度、任务队列与仿真结果回放；保留此前暂停回放与算法调试工具。

## 当前任务

- 2026-10-01：新增感知结果阶段质量指标及36种泊车地图/场景，指标随Sim任务保存在Full result analysis。
  泊车36/36、交互扰动24/24、原有场景26/26通过；Lane_67_reverse_mixed两次42.51 s完成且确定性PASS。
  正式API任务353c3c6dc9744e1f两次窄路45°尾入库通过。
  semantic-mcap-v17补充Driveable区域及真实ParkingSpace边框，7项地图测试、真实录包转换、native Clippy与release构建通过，9090服务端已更新。
  此次收益来自规划器改进，V5权重未重新训练；首次泊车搜索最大约32.6 s，未做Control/实车验收。
  设计和证据见[质量与泊车结果](../ml_planning/QUALITY_PARKING_RESULTS.md)。

- 2026-10-01：回放车辆与轨迹升级 semantic-mcap-v16：仿真读取校验过的车辆快照，普通录包读取当前配置；GLB 按长宽高及四边距缩放/平移，保持后轴定位参考点；规划改为实际车宽的 Mesh3D 色带，按规划减速度与 latest-at 实际刹车反馈从绿渐变至红，缺少力度信息显示灰色。缓存键包含车辆配置。Ranger 真实录包 48,681 个横截面均为 0.50 m；33 项 Python 测试、车模尺寸/参考点 Rust 测试、native/wasm Clippy 与发布构建通过，正式 9090 播放/暂停/双向跳转、轨迹可见性和时间戳验证通过。按用户要求删除根目录 `tools/apollo_record_tools`，移除旧路径候选，转换/调试/浏览器测试均使用 `modules/simulation/tools/apollo_record_tools` 的独立 `.venv`；删除后真实转换及新环境浏览器验证通过，10 条历史任务保持不变。证据：容器 `/tmp/wm-tools-tests.log`、`/tmp/wm-vehicle-rust-tests.log`、`/tmp/wm-vehicle-browser/`；当前服务 PID 474697。刷新页面并重新打开录包/任务回放可生成新版本缓存。
- 2026-10-01：将 `1haolou_202608241047qh` 完整资源统一到 `data/map_data/`，清除 `modules/map/data` 和 Scene Editor public 下的同名副本；同步脚本、示例清单和编辑器资源引用。catalog 按文件系统设备号/inode 合并挂载别名，优先展示 workspace data 路径（容器 `/apollo/modules/map/data` 是 data/map_data 的 bind mount，不能作为独立副本删除）。提交前检查 base_map 与 sim_map，缺失时不创建任务。27 项 Python 回归、编辑器构建通过；正式服务空地图覆盖自动匹配唯一资源，重跑 `390b357e71cc47c5` completed：60 秒、6001 帧碰撞检查、601 帧有效规划，碰撞/连续性/到达终点均 PASS。原失败任务保留。证据：容器 `/tmp/wm-map-tests.log`、`/tmp/wm-map-real-result.json`。
- 2026-10-01：按用户要求清空本地仿真记录和包：64 条任务、`data/simulation` 下所有运行产物及 44 份仿真 MCAP/对应缓存已删除；82 个原始输入 record 与 12 份原始录包回放缓存保留，任务页为 0。任务更多菜单新增“删除任务”，可删除排队/结束任务及私有目录和关联缓存，运行中需先取消；文件错误显式返回，SSE 全量快照同步删除到所有页面。排序改为发起时间倒序（新任务 created_at，历史 queued.wall_time），独立任务可穿插场景集，批次同时间保留清单顺序。25 项 Python 回归、Wasm Clippy、发布构建、乱序 ID/状态界面检查及正式双页面真实删除验证通过，最终空任务页无浏览器异常。证据：容器 `/tmp/wm-delete-cleanup-result.json`、`/tmp/wm-delete-real/`、`/tmp/wm-delete-empty.png`。
- 2026-10-01：修复开始仿真时旧工作区路径不存在：移除任务服务、资源扫描、Cyber 配置及 Viewer 工具入口的旧目录候选；按用户要求一次性更新 51 条历史任务可复用配置的 71 处路径，统一使用 `/apollo_workspace/modules/simulation/`，不增加路径兼容或旧目录链接。62 条原任务的 ID、状态和结果保留。22 项回归、native Clippy、发布构建通过；正式 9090 浏览器复用旧任务显示新路径，实际点击开始创建 `f287dfdec21f4918` 并 completed，浏览器异常 0。证据：容器 `/tmp/wm-path-migration-audit.json`、`/tmp/wm-path-browser/`。已有页面需刷新以清除内存中的旧草稿；已删除的历史测试输入不会由路径更新自动恢复。
- 2026-10-01：仿真配置按参考图改为四个分区、资源/版本双列、复选模块卡片和数值加减控件，移除“可选”与说明注释，底部固定“开始仿真”。场景集、ML Planning 等保留在“更多设置”；版本栏按实际目录能力置灰。WorldSim 空资源覆盖读取场景配置，缺失/歧义明确报错。Wasm Clippy、发布构建、21 项任务/场景集测试及隔离/正式浏览器交互通过；已更新本地 9090，59 条历史任务快照未变。界面测试拦截提交，没有启动真实仿真；截图与日志见容器 `/tmp/wm-config-ui/`、`/tmp/wm-config-deployed/`、`/tmp/wm-config-deployed.log`。
- 2026-09-30：按参考图重构仿真任务工作区：状态计数、搜索与来源/状态筛选、场景集折叠、每页 10 条卡片、进度/排队/失败原因、详情/复用/回放菜单和新建任务；窄窗口自适应。保持 SSE 增量推送，不刷新页面、不轮询任务列表。12 项 Rust 测试、native/wasm Clippy、发布构建通过；隔离浏览器覆盖完整交互，正式 9090 部署前后 2247 条任务快照一致，正式任务页完整交互、真实历史任务及 Start/SSE 回归均通过，浏览器异常 0。动态界面测试使用明确注入的数据，不启动真实仿真。证据 `/tmp/wm-task-ui-deployed/`、`/tmp/wm-task-real-deployed/`、`/tmp/wm-task-ui-events-deployed/`。
- 2026-09-30：Sim 任务状态改为 SSE 初始快照与增量推送，移除每秒列表轮询；启动文案仅由 enqueue/enqueue_suite 决定，推送保留草稿、筛选、详情与操作错误。Wasm/native Clippy、发布构建、11 项 Rust 测试及 19 项队列相关 Python 测试（1 项既有数据缺失跳过）完成；隔离实例与正式 9090 浏览器回归通过，网络无 list 轮询，异常 0，2245 条历史任务前后相同。浏览器采用明确注入的任务增量和提交错误验证 UI 状态隔离，未启动真实仿真；证据 `/tmp/wm-sim-events-deployed/`。
- 2026-09-29：部署场景集 1–30 并发及独立结果分析进程；不为压测重新跑全量。增加逐帧规划轨迹与 100 ms 发布连续性硬门禁，取消/错误传播等 41 项测试通过（1 项既有 SDK 跳过），Wasm Clippy 与构建通过。
- 失败场景审核：保留 396 个定义，15 个永久阻塞用例标记 INVALID 并排除；Lane_65_static_right 的真实到达反证原停车预期，修正后有效集合 381 个。仅回归 7 个有效失败用例：1 通过、6 仍无法到达，7 个均无碰撞且轨迹连续。
- 尚未解决：Lane_60_mixed 录包存在 35 帧空规划轨迹（已由新门禁明确判失败）；6 个有效弯道/静态避障用例仍需改进规划。不能宣称全部通过或已修复轨迹缺失。证据 `data/simulation/v4-validation-20260929/FAILED_SCENARIO_REVIEW.md`。

算法调试工具已迁移为 Layout 内真实停靠 Panel；Planning 四窗、Control 六窗，支持自定义窗口布局，详见 `docs/AD_LAYOUTS.md`。

## 场景预期审核（2026-09-29）

按用户明确反馈，窄弯永久阻塞的合理停车不再统一按“未到终点”失败。396 场景在执行前区分到达（203）、动态让行后通过（177）、永久阻塞停车（16）；空间审核使用原始地图宽度采样和 Ranger 车头弯道扫掠估算。每场景有 SHA-256 绑定的 `.evaluation.json`，运行时冻结判据。停车需在审定等待区稳定至少 5 秒；碰撞、异常 estop、无关位置停车仍失败。修正脚本演员出生重叠、终点占路及穿过阻塞等待区等场景缺陷。详见 scene_editor 北京场景集 `SCENARIO_AUDIT.md`，当前正进行新判据回归，不能宣称完整 396 个场景已通过。

## 全图场景回归进展（2026-09-29）

北京总院一号楼场景集现有 396 个场景，可直接整套提交；界面和服务支持 1–10 并发，默认 10，已实测 10 个独立原生进程。覆盖 76 条有向车道及静态绕行、超车、车队、行人群、连续车辆横穿和混合场景。V4 已重训，完整场景集仍在验收，尚不能标记全部通过。碰撞、estop、未到达目标及驾驶质量失败均保留录包和失败状态。证据：workspace `data/simulation/v4-validation-20260929/`。

## 已完成

- [x] 2026-09-29 后续：北京总院一号楼扩展为 128 个不同可编辑场景（同一清单、最多 3 并发），ML V3 完成 6,012,928 步 PPO 重训，并修正远处行人引起的偏移与折返等待过早释放。原生 WorldSim 每步执行真实车身 OBB 接触检测；Sim result 展示碰撞、对象与首次接触时间，任意重复碰撞直接失败，检测缺失/未完成不算通过。旧“到达终点”结果不代表行为合格；新增直路居中/连续横摆检查。证据：workspace `data/simulation/v3-validation-20260929/`。

- [x] 2026-09-29：WorldSim 增加 Planning / ML Planning 互斥选择、北京总院一号楼 8 场景集合与 1–3 并发。配置/权重/录包按任务隔离，不修改共享 profile/global flags；8 场景串行与三并发算法比较全部 PASS。接入时的旧模型 7 场景到达终点，慢速前车误差 0.488 m 保留失败。传统 Planning + Fake prediction 真实运行有有效轨迹；真实 Prediction 因 GPU CUDA kernel 不兼容失败，未掩盖。正式 9090 界面互斥切换规划器、整套提交（每场景两次均确定性 PASS）、主车/轨迹录包回放通过，浏览器异常 0。证据：`data/simulation/integration-20260929/`、`data/simulation/browser-validation-20260929/`（workspace）。

- [x] 2026-09-21：撤回容器桌面选择器，恢复浏览器所在电脑任意目录选文件，面板/layout 不变。File/Blob 每次最多 2 MiB，服务端直接写盘；首个完整 Cyber chunk 由现有 RecordFileReader/Writer 提取并交给现有转换器，首段真实可回放后才继续传剩余数据，完整转换完成后切换全长。客户端独有 /tmp 副本实测 2.15 GB 文件在 22 MiB（1.07%）已到达时回放就绪；无整包 FileReader/旧 upload_recording 请求。Wasm/native Clippy、发布构建、分块/首段背压测试及接口偏移/大小/错误/取消检查通过，全长 2/8/15 秒双雷达跳转及播放推进通过，浏览器异常为 0；正式 9090 已部署。保留 v15 多雷达/原始 Image 适配，不自动重绑相机布局。

- [x] 2026-09-15：仿真真正应用 workspace profile/map/vehicle：遵循 AEM 的 current + 逐文件链接，更新实际 global_flagfile；按所选 vehicle_param.width / 2 写 half_vehicle_width（当前 ranger 为 0.5 / 2 = 0.25）。不新增 backup/rollback/recover，预检查与 I/O 错误直接失败；任务配置冻结、原生地图加载前赋值和模块初始化后校验共用工具函数。26 项 Python、6 项 C++ 用例通过；正式 9090 WorldSim `16963375edf64e00` 与 LogSim `81de472c45414326` 均 completed，逐任务核对 260 个 profile 运行文件、四模块 flags、地图与车辆快照。WorldSim 仍有 506 帧 Planning 6000，单次运行不验证确定性；不宣称算法问题已解决。证据 `test-artifacts/simulation-configuration-20260915/`。
- [x] 2026-09-14：补齐确定性字段明细：Simulation detail 提供 JSON 列表、完整消息、运行对选择、前后翻页和差异序号输入；保留 topic、双时间戳、帧序号、字段路径及两次值/存在性。按记录内 ProtoDesc 解码，旧任务无需重跑；不更改比较规则、原始记录或任务状态。修复后台刷新期间点击丢失，并启用 JSON 浮点精确往返。两套 Protobuf 环境各 17 项测试、正式 9090 差异/完整消息/跨 20 条分页及配置草稿隔离浏览器回归通过。`d3ade1846813436c` 仍真实 FAIL（17,778 条），首个差异 3.6 s Planning，含 2,449 个字段；9.71 s 的 Z=0 是 Planning 平面路径高度存在性与 perfect_planning 采样造成，本次仅分析未修改算法。证据 `test-artifacts/determinism-details-20260914/`。
- [x] 2026-09-14：按新交互修改 Sim：任务标题打开同面板实时 detail，列表和详情提供 View config 按钮；恢复为原来的可编辑配置表单，直接启动或修改后启动均创建新 ID，历史任务不变。保留模块/模型/步长/Seed/Runs、timeout、bag 范围及额外字段，切换详情/标签不覆盖编辑内容。六项单元测试、Wasm/native Clippy、构建及正式 9090 浏览器配置/详情/回放回归通过。真实新建 `a077df8576544898`（两次比较 failed）、`6e27e55c51ee473b`（单次 completed）；未把前者算作确定性通过。证据 `test-artifacts/sim-detail-20260914/`。
- [x] 2026-09-14：Sim 改为与其它侧栏一致的停靠面板，Simulation Config / Simulation Tasks 固定标签共用内容区；任务按运行中、排队、已结束分组。提交成功自动切到 Tasks，点击任务标题打开精确只读配置快照，保留独立草稿。正式 9090 真实入队/取消/分区/查看配置/窄屏/回放 UI 测试通过；29 组播放条、双时钟、速度单位及 Source 回归通过，浏览器错误为 0。证据 `test-artifacts/sim-tabs-20260913/deployed/`、`playbar-regression/`。本次三次重复仿真比较实际失败，已如实展示并列入下方待解决事项，不属于确定性验收通过。
- [x] 2026-09-13：播放条统一 Carolanne 紫色，双时钟切换暂停并保持同一绝对纳秒时间戳；Dashboard SPEED 点击切换 km/h / m/s，只换算底盘反馈；Source 三种模式改下拉，仅保留 Open Path，隐藏 Current recording / Application / Recording / Started，长路径换行避免重叠。正式 9090 的 29 组浏览器状态及截图验证通过，含同时间点双时钟往返、真实非零速度换算、三种 Source 模式与原始 record 路径打开；浏览器错误为 0。证据 `test-artifacts/theme-source-hud-20260913/deployed/`。
- [x] 2026-09-13：时间输入框移至播放条最右侧（进度秒数之后），由固定 176 px 改为按 bag 时间范围预留宽度，当前仿真 bag 为 112 px；使用与下拉框一致的比例字体，保留九位小数和编辑跳转。修复 eframe 透明 IME 代理截走鼠标点击的问题。正式 9090 播放条与 Topic inspector 浏览器回归均通过，3 项 Rust 测试、Wasm/native Clippy、完整构建通过；证据 `test-artifacts/compact-timestamp-20260913/deployed/`、`topic-regression/`。
- [x] 2026-09-13：播放条移除 Playback settings / Go to end / Seek 提交按钮，clock 和步长改为与倍速同样式的独立下拉框；时间框实时显示原始秒时间戳（九位小数），Enter/失焦提交、Esc 取消，播放时保护编辑草稿，换来源清除旧草稿；非法/越界不跳转。宽度不足时分两行。正式 9090 验证绝对纳秒跳转、输入/播放/下拉和展开侧栏，Topic inspector 回归通过，浏览器错误为 0；3 项 Rust 测试、Wasm/native Clippy、构建通过。证据 `test-artifacts/inline-playbar-20260913/deployed/`、`topic-regression/`，说明 `docs/PLAYBACK_BAR.md`。
- [x] 2026-09-13：播放条参考 scene_editor 重做为深灰底、24 px 圆角控件、蓝色细进度条与右侧当前/总秒数；按用户追加要求删除停止复位按钮及新增动作，暂停保持当前位置。前后步进、倍速、真实回执缓存保留，双时钟/精确跳转放进设置。正式 9090 实点验证无复位按钮、10/1000 ms 步进、点击/拖动、精确及非法输入、时钟切换、2x 播放/暂停和 900 px 窄窗口通过，Topic inspector 也通过回归。截图及请求证据 `test-artifacts/scene-playbar-20260913/deployed/`、`topic-regression/`；说明见 `docs/PLAYBACK_BAR.md`。
- [x] 2026-09-13：永久修复 Topic inspector 窄窗口通道选择器越界、空 topic 仍发查询的问题。独立全宽 Browse topics + 可搜索真实目录，搜索框点击不关闭弹层；空值进入待选择态，不存在的 topic 明确提示且不替换数据源；消息操作自动换行。正式 9090 实点验收 18 个 topic、空值/无匹配/不存在路径、pose/control 切换、约 215 px 窄面板和暂停 30 秒原始字段通过；双时钟各五次前后 seek 及播放、五个 Control 面板回归通过。证据 `test-artifacts/topic-picker-20260913/deployed/`、`clock-regression/`；7 项 Rust 测试、Wasm/native Clippy、完整构建通过。
- [x] 2026-09-13：永久修复“3D 已显示、Topic inspector/HUD 却没有源文件”——录制内携带静态源文件身份；每页持久保存游标/时钟/图层，恢复校验文件并等待布局激活后的暂停游标提交。未变化文件保持录制 ID，客户端刷新不再破坏其他页面缓存。正式 9090 浏览器验收 15 组状态/截图通过：无需再次 Replay 的刷新、新页面、暂停字段查询/跳转、双页不同 bag、切换后刷新、直接播放、身份不匹配报错与重试；浏览器及控制台错误均为 0。证据 `test-artifacts/session-recovery-20260913/deployed-final/`，协议与回归入口见 `docs/PLAYBACK_SESSION.md`；5 项协议测试、Wasm/native Clippy 与最终构建通过。
- [x] 2026-09-13：迁移 scene_editor 地图渲染到 web_monitor，58 条真实车道路面/白色边界/绿色逻辑中心线与主车、规划共用坐标原点。Sim 回放自动校验并加载地图快照，Source 支持普通 record 手动选图；静态地图和独立/总显隐开关。正式 9090：三布局、暂停首帧、双时钟各三次跳转及播放通过，自动/手动入口证据分别为 `test-artifacts/hdmap-20260913/deployed/`、`manual-deployed/`；35 项 Python、73 项 re_mcap Rust 测试及 Wasm/native Clippy、构建通过，详见 `docs/HD_MAP.md`。
- [x] Ranger Mini V3 按用户授权适配 Apollo 10：注释不兼容扩展、启用标准规划/控制插件，修正任务相对路径；补生成 1haolou sim_map 并新增真实车道测试场景。修复地图路径末端投影越界，两个 10 秒运行与正式 9090 任务重复比较通过；回放见 `test-artifacts/ranger-profile-20260913/replay1/`，配置说明见 `profiles/ranger_mini_v3/APOLLO10_SIMULATION.md`
- [x] Sim 实际任务面板：bag/JSON、地图、车辆、profile、四个算法模块、模型/步长/seed/repeat；持久 FIFO、阶段/进度、取消、结果分析与 Replay
- [x] 三个二进制共用 RunSimulator；同一 nanosecond mock clock 和真实同步组件调用，Control TimerComponent 实际执行，周期事件只保留下一次触发
- [x] 真实 bag 三次重复 + 额外九次运行通过；WorldSim planning/kinematic 两种模型实测有效规划与主车运动，定时障碍物出现/消失及重复比较通过
- [x] `simulation-20260912/ui6/` 实际鼠标/键盘入队、FIFO、取消、两种输入重复运行通过；`ui6-replay/` 结果 bag 的 GLB/轨迹、底盘 HUD、五个 Control 调试 Panel 全部就绪并截图验证
- [x] 正式 9090 已部署统一 Sim 版本；`simulation-20260912/deployed2/` 鼠标/键盘提交 bag + world（各两次）、FIFO、取消、结果回放与五个调试 Panel 验收通过
- [x] `simulation-20260912/dashboard-regression2/` 正式 9090 原始 control bag 回归：底盘数值核对、三次暂停跳转、3D/俯视跟随与自由操作、28 个缓冲播放采样通过
- [x] `world-log-equivalence2/equivalence.json`：相同四模块配置下 World 输入录包再由 LogSim 重放，363 条 Prediction/Planning/Control 消息顺序、纳秒时间戳和算法值完全一致；首次少选 Routing 的不等价对比保留为失败证据
- [x] 输出 record 写完整 Cyber ProtoDesc；比较器不再返回常量 PASS；报告保留原始差异，算法值只排除明确列出的耗时统计并规范 protobuf map 顺序
- [x] 8 项 Python 测试、3 个 C++ 测试目标、Wasm/native Clippy 与构建通过；真实监督进程崩溃测试确认仿真子进程退出、重启记录 interrupted
- [x] 配置快照与模型/库指纹、缺失 profile 插件预检查、源文件变化检测；工作目录隐藏隔离，修复 buildtool 递归扫描及复制循环；清理本轮 /opt 构建副本约 62 GB（原始数据及结果保留）

- [x] Panel 抽屉保持 Edit current Layout
- [x] 3D 视口左上角 Topics 可展开勾选，避开通知遮挡
- [x] 勾选/取消按当前时间请求 `playback_window`，同 recording 增量补齐通道数据
- [x] 保留 playhead / 播放状态；去掉 Reload 按钮依赖
- [x] 单端口入口：bare `http://HOST:9090/` 自动连 gRPC proxy，无需 `?url=`
- [x] 多机 / LB 支持：`--grpc_host` + `--web_port` gflag；`WEB_MONITOR_GRPC_HOST` / `WEB_MONITOR_WEB_PORT` env；`?grpc_host=` / `?grpc_port=` URL 参数
- [x] 2026-09-12：容器 Chromium 实测指定 bag，勾选 `/lidar/up/points` 后无需播放即可见，三次暂停跳转的数据时间戳/点数与 MCAP 一致
- [x] 数据流回执驱动缓存条；按 topic 去重导入；缓存内播放测试无缓冲暂停（软件 WebGL，不代表硬件实时帧率验收）
- [x] 回归脚本与截图：`scripts/test_paused_playback_browser.py`、`test-artifacts/paused-seek-20260912/run4/`；详见 `docs/PLAYBACK_AFTER_CONVERT.md`
- [x] Topic inspector / Signal plot / Value watch / Control dashboard / Trajectory XY / State transitions / Topic health
- [x] 修复 Python protobuf DebugString 调用；新增有界 LRU 索引和持久 debug query worker，删除 Panel 占位按钮及旧 Topic View UI
- [x] 新 control bag 浏览器验收：暂停跳转、逐消息、筛选、复制、冻结、固定字段、自定义曲线、控制统计、轨迹、状态事件、多面板同步（`test-artifacts/debug-panels-20260912/run8/`）
- [x] 所有 3D 布局包含 lidar/vehicle/planning；GLB 主车、规划曲线采用同一真实坐标系，暂停跳转显示 latest-at 数据
- [x] semantic-mcap-v11：真实定位/雷达静态外参组合，大坐标 float64 重定位；缺失 TF 明确告警而非错误叠加
- [x] Planning profile（v/a/kappa）、Control 独立速度/转向/误差/踏板图；相机改为可选添加
- [x] `layouts-20260912/run7/`：布局切换保持游标、MCAP 几何数值核对、窗口增删/拖拽、Custom 恢复/刷新/重开数据源、.rbl 下载通过
- [x] 修复 AppState 跳过整个 AdShell 导致 Custom 不持久化；旧 bag 暂停/缓存播放回归通过（17 个点云样本）
- [x] Panel 后台刷新与首次加载分离，保留当前结果且固定状态栏高度；`panel-refresh-20260912/run3/` 注入 350 ms 查询延迟验证播放中无加载圈/垂直跳动、数据持续更新、seek 收敛和显式错误
- [x] Layers 分层显示开关：父子复选框、隐藏已缓存实体、布局切换保持；移除重复原始 planning 开关；黄色基调固定高度点云配色
- [x] semantic-mcap-v12 新增感知障碍物线框、预测多轨迹语义通道；真实空预测不伪造轨迹
- [x] `layers-20260912/deployed/` 原始 record + 9090 验收通过；相机父子开关、缓存显隐及旧 bag 暂停/缓存播放回归（16 帧）通过
- [x] 3D 底部居中可收起仪表盘：仅显示 chassis 反馈，缺失显示 —、不混用 control；共享标量窗口预取，Top / Ego 相机按钮；`dashboard-20260912/run3/` 完整鼠标/截图验收通过，包含窄窗口与延迟注入；24 项 Python 回归通过
- [x] `dashboard-20260912/deployed/` 正式 9090 冷启动后打开用户原始 record，全流程复测通过；Wasm/native Clippy、完整构建通过
- [x] 侧边相机按钮参考 Dreamview Plus 重做为 32 px 圆角无描边图标，灰蓝默认/蓝色激活，提示向左展开；`camera-icons-20260912/run2/` 截图与相机/仪表盘回归通过
- [x] `camera-icons-20260912/deployed/` 正式 9090 原始 record 全流程回归通过，图标按钮版已部署
- [x] 俯视锁定平面拖动；定位改为持续跟随位置和水平车头朝向，3D 后上方追车 / 俯视车头朝上，关闭跟随后恢复自由操作；默认追车距离 50 m、俯视高度 70 m，可滚轮缩放
- [x] `camera-follow-20260912/run1/` 与 `deployed/`（正式 9090、原始 control record）通过 `--camera-modes` 浏览器验收：原始 pose 数值核对、三键/修饰键/双击锁定、暂停跳转、两种模式各 16 个播放采样、退出跟随后视角保持，以及 28 个缓冲底盘样本；Wasm/native Clippy 与完整构建通过

## 2026-09-13 双时钟回放验收

- [x] 产品时间轴、面板查询、HUD 统一为精确的 `publish_time` / `message_time`；初始化期间不再向查询接口发送 Rerun 默认 `log_time`。
- [x] semantic-mcap-v13 从真实消息提取生成时间；缺失生成时间的数据保留为 publish-only，不伪造时间值；窗口缓存通过真实时间戳对同时覆盖两轴。
- [x] 指定任务 `efcce7f9cf4c49c1` 在正式 9090 通过浏览器验收：两轴各五次暂停跳转（含未缓存的 30 s / 58 s）及播放、主车/HUD/五个 Control 面板；75 次查询无旧 clock 名、无浏览器错误。证据：`test-artifacts/clock-contract-20260913/deployed4/`。
- [x] 29 项 Python、140 项相关 Rust 测试通过；Wasm/native Clippy 与完整构建通过。构建明确要求 Binaryen >= 130，阻止旧优化器生成无法启动的 Wasm。数据来源与验收边界见 `docs/CLOCK_CONTRACT.md`。

## 进行中

- [x] 2026-09-13 根据用户截图复现“Layers 有目录、空 workspace / log_time、无数据流”：修复空 `url` 禁用订阅、proxy 用旧录制误判仍连接、回执未绑定当前录制，以及首帧暂停命令尚未生效就自动恢复播放的问题。正式 9090 的断流→按钮重试、移除订阅→重新打开、run 切换、刷新复开四次严格验收通过；证据 `test-artifacts/replay-stall-20260913/deployed-recovery/`，说明 `docs/REPLAY_CONNECTION.md`。早期接受旧缓存的宽松测试已明确作废。
- [ ] 用户现有浏览器页面刷新后的实际体验确认；截图已收到，但未获得其完整网址，不能声称直接操控过该浏览器会话。

- [ ] 2026-09-14 Sim 侧栏真实任务测试发现 `6ccdc04498334791`、`d4795fd963684f88` 三次执行后的确定性比较失败（前者首个差异 1.7 s）；原始 outputs / analysis 均保留。未修改调度器或比较规则，不宣称该场景确定性通过，详见 `docs/SIMULATION_TASKS.md`。

- [ ] 联调 `ui5` 曾出现一次 bag 原生进程 SIGSEGV；GDB 两次及后续连续重复运行均未复现。已增加原生故障调用栈记录，尚未确认其根因，不能宣称已解决或任意环境生产级确定性。详见 `docs/SIMULATION_TASKS.md`。

- [ ] 硬件加速浏览器长时间播放性能、多路相机逐帧验收

## 下一步

1. 硬刷新 `http://127.0.0.1:9090/`（无需 `?url=`）
2. 重新打开原始 .record（不要复用旧版 .mcap），等待 v13 转换；勾选 Layers
3. 确认时间戳不跳回开头，画面随勾选变化
4. 多机验证：在另一台机器的浏览器里打开 `http://192.168.31.187:9090/`，确认 gRPC proxy 自动指向 192.168.31.187:9876
