# Web Monitor / WorldSim 二进制包验收（2026-10-07，v6）

两种正式包均通过：解压、source、Web Monitor 启动、`/api/sim` 配置/入队、
原生仿真、自动结果分析、真实录包转换、Topic DebugString 和 gRPC 回放窗口。
每个包分别运行标准 PNC、ML 无障碍、ML 静态避障，各重复两次，共 12 次正式任务运行；
另外每包各完成一次 3 秒直接 PNC 检查。
两次算法比较要求消息顺序、纳秒时间戳、protobuf 值完全一致；仅排除原有明确列出的
耗时统计字段，原始字节差异保留。没有浮点容差或忽略失败重跑。

## 交付与环境

| 类型 | 压缩大小 | 目标 | ELF | 校验文件 | 包内运行库 | 镜像库 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| simulation | 711.8 MiB | 147 | 687 | 3556 | 399 | 94 |
| all | 3744.2 MiB | 467 | 9920 | 40276 | 931 | 127 |

正式交付：`/home/wangsheng/workspace/binary-v6.tar.gz`、`binary-all-v6.tar.gz`。
每个交付副本的 SHA-256 与对应 `archive.sha256` 一致。
源工作区产物仍叫 `binary.tar.gz`、`binary-all.tar.gz`；旧交付版本保留。

镜像：`registry.baidubce.com/apollo/apollo-env-gpu:10.0-u22`，
ID：`sha256:7c33b27bf30bccf5c13f4d1058c72437930681b646ee0b7bfece979f482a2530`。
两个容器均无挂载、`--network none`、由 UID 1000 执行；仅全量包提供 NVIDIA GPU。
容器仅接收压缩包及验收脚本，没有源码、Bazel 缓存、开发 SDK 依赖卷或 pip 安装。
镜像自带 NumPy 2.1.3 / Shapely 2.0.6 / protobuf 3.19.6 保持原样。
转换器通过包内隔离目录使用固定版本 NumPy 2.2.6 / protobuf 4.25.9 等 wheel。
全部镜像库检查、SHA-256、包内链接、ELF 依赖和 Cyber C++/Python 工具通过。
只有顶层 `bin/`；没有 `bazel-bin`、运行库子目录或空目录。
全量包的 16 个私有库同进程 RTLD_NOW / RTLD_GLOBAL 加载通过。

## 算法及回放结果

| 类型 / 任务 | 任务 ID | 每次有效规划帧 | 最终目标误差 | 行为 / 质量 / 确定性 | 回放 | ML 推理最大误差 |
| --- | --- | ---: | ---: | --- | --- | ---: |
| simulation / pnc | `2cdd907234cc44e0` | 354 | 0.304828 m | PASS | PASS | — |
| simulation / ml | `0bff47700172422e` | 388 | 0.235757 m | PASS | PASS | 6e-15 |
| simulation / ml-obstacle | `5ac0f2ef4b7a4fbc` | 403 | 0.238289 m | PASS | PASS | 9.33e-15 |
| all / pnc | `7a93345c13e34554` | 354 | 0.304828 m | PASS | PASS | — |
| all / ml | `130d1a91b1c34694` | 388 | 0.235757 m | PASS | PASS | 6e-15 |
| all / ml-obstacle | `dbd77fee914a4757` | 403 | 0.238289 m | PASS | PASS | 9.33e-15 |

- PNC：Prediction / Planning / Control / Routing，`kinematic_control`，约 32 m 路线。
  Planning 全程状态 0；Control 最初 100 ms 的 10 帧状态 1002，原始消息为
  `planning has no trajectory point. planning_seq_num:0`，表示首个观察周期等待轨迹。
  之后所有 Control 帧状态 0；逐条检查两次录包，任何后续错误都会验收失败。
  这些初始消息在 `verification/algorithm-health-pnc.json` 中完整保留。
- ML：ML Planning / Routing，`perfect_planning`。无障碍与 5 秒出现的静态障碍场景均
  无碰撞、无 estop、无断轨、无车身越界/运动学违规，到达目标并停车。
  每次录包的真实观测与冻结权重用于独立 NumPy actor 推理，误差要求 < 1e-8。
  ML 本轮覆盖理想轨迹跟随，不作为 ML + Control / 动力学模型验收。
- 标准 PNC 依据冻结 Destination/车辆/地图/Routing 独立计算车头停车目标及后轴位置，
  要求目标误差 ≤ 0.4 m、速度 < 0.05 m/s、DEST 停车点匹配、mission_complete。
  原请求 route endpoint 距离也保留（约 1.475 m）；不会拿该距离冒充实际停车目标误差。
  ML 仍以场景后轴目标检查 0.4 m，碰撞/质量阈值没有放宽。
- 每个任务实际输出 record 经转换器产生非空 MCAP；规划与定位 Topic DebugString
  解码通过，真实 gRPC 回放窗口服务返回完成回执。网页 JS/WASM/图标和 HTTP/gRPC 通过。
  本轮通过服务 API 驱动完整流程；没有宣称鼠标操作浏览器 UI 或验收全部感知/设备算法。

## 问题、根因和原文件修复

1. 旧包漏收 `task_service.py`、分析辅助模块、生成 schema 的导入闭包及同构建的
   Cyber decoder；服务路径指向原 `/apollo_workspace`。显式声明 Bazel filegroup 和
   运行依赖，规范 Python schema 目录，setup 指向当前包；任务进程仅使用当前包的库/插件。
2. 回放转换需要镜像没有的 MCAP/foxglove 等 Python 依赖。打包阶段收集固定版本 wheel，
   转换器进程单独启用该目录，目标容器不安装依赖，不覆盖任务分析所用镜像 Python。
3. Planning 重新初始化按真实计算耗时预测到未来 0.1 s，同步 mock 时钟的当前步缺轨迹。
   mock 模式从测量状态和时间 0 起步；真实时钟保持原行为。
4. 地图 `GetProjectionWithHueristicParams` 的闭区间可以访问不存在的末端 segment，
   导致 DEST 投影异常及重复运行不一致。端点索引限制到最后实际线段；无效窗口显式拒绝。
   新端点测试和已有真实 DEST fixture（接入 Bazel）覆盖此问题。
5. OSQP 自动 rho 更新间隔依据宿主机实测耗时，导致速度优化在 21.2 s 首次出现数值差异。
   仅 mock 时钟且未显式配置间隔时固定为 25 次迭代；精度、约束、polish、显式间隔和
   真实时钟均保留。参考 [OSQP 0.6.3 官方配置说明](https://osqp.org/docs/release-0.6.3/interfaces/solver_settings.html)。
6. 原到达检查直接使用 route endpoint，忽略 Apollo 标准 Destination 车头停车语义。
   新检查独立计算配置目标，并额外核对真实停车决策和完成状态；保留原始请求距离。
7. Ctrl-C 终止打包器时 Bazel 客户端曾继续运行。构建子进程现在接收 SIGINT 并等待退出，
   真实子进程回归通过；已有包仍按原子替换策略保护。

错误处理：没有跳过构建失败、忽略缺包 schema、修改录包、降低比较精度、
延长场景自动接受远处停车或给目标容器安装开发依赖来遮盖缺陷。

## 回归和证据

- 29 项任务服务、4 项轨迹连续性、5 项质量、13 项场景/停车语义测试通过：
  `final-python-tests.log`。最新 29 项打包回归通过：`final-packager-tests.log`。
- 四个相关 C++ 测试目标通过，共六个 GTest 用例；末端投影、真实 DEST、模拟/真实时钟
  拼接、OSQP 配置及实际求解重复一致性。见 `pnc-final-native-regression.log` 及各目标日志。
- 额外运行旧 `pnc_path_test` 时，其原有 `hdmap_curvy_path` fixture 无 lane waypoint，
  在 `InitWidth` 中 SIGSEGV。保留 `legacy-pnc-path-tests-failed.log`；本轮未改该旧 fixture，
  新端点测试使用完整 lane 几何。此处不宣称全仓库单元测试通过。
- `simulation/summary.json`、`all/summary.json` 含包指纹、完整任务分析、模型推理和回放结果。
  各 `container.json` 保存无挂载/网络/GPU 条件；`manifest.json.gz` 与 `verification/ldd.log.gz`
  保存依赖清单。`*-task-evidence.tar.gz` 保存真实任务配置、冻结地图/车辆、日志和两次录包，
  排除可重建的 `.runtime` 树且不解引用其中链接；`*-frozen-runtime.tar.gz` 单独保存
  冻结配置文件、独立停车检查的 Destination/flagfile 和实际推理权重，权重 SHA-256 已核对。
- 首次缺 schema、连续性/停车语义、地图越界确定性、求解器耗时确定性失败均保留；
  `failed-*-evidence.tar.gz` 是原始记录，最终通过不会覆盖这些证据。

复验命令和打包用法见 [BINARY_PACKAGING.md](../../BINARY_PACKAGING.md)。
可复用经验：构建依赖闭包之外还需 exec/Python/plugin 运行依赖，验收必须真正执行任务和
结果回放；mock 时钟下算法仍可能依赖真实耗时。建议整理到 Obsidian
`40-Problems/Apollo 二进制包仿真依赖与确定性.md` 和 `50-Solutions/干净容器 WorldSim 验收.md`。
