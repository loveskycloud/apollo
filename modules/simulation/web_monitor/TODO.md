# TODO

## P0

- [ ] 追踪仿真 `ui5` 的单次原生退出 SIGSEGV；保留失败证据/调用栈，不以重跑成功掩盖失败；当前后续 12 次 bag 运行及 GDB 检查未复现
- [ ] 补齐外部 Jiyu_01 profile 所需的 SpeedSetting / ValetParkingParkScenario 插件后，单独验收该 profile（不得自动切换默认配置）

- [ ] 人工验收：窗口化播放 — Panel 勾选 topic；点云默认关；Play >10s；Host RSS 不再 ~8GB
- [ ] 人工验收：Browse 默认可见 `20260514114049.record.00000.*` / `00001.*`（无需“所有文件”）
- [ ] 人工验收：打开 bag 后媒体条时长 = Cyber header（不随流式变长）
- [ ] 人工验收：Topic View 打开 `/apollo/localization/pose` **不卡死**，可见 host DebugString（或红字错误）
- [ ] 打开 `.rrd`/`.mcap` 后确认底部收起态媒体条可 scrub / 播放

## P1

- [ ] 中文标签 CJK 字形人工确认
- [ ] 明确并固化推荐 `buildtool` 包路径
- [ ] re_mcap protobuf：坏 schema 仍 skip Arrow（已 `error!`）；评估是否改为整包 fail（no-fallback 严格模式）
- [ ] 窗口化 LRU：丢掉远离 playhead 的已加载窗（进一步控内存）

## P2

- [ ] 大文件 / 慢客户端 `server-memory-limit` 调优经验沉淀
- [ ] Obsidian：`40-Problems/` / `50-Solutions/` 链入 R8–R12

## Done

- [x] 二进制包完整 WorldSim：任务服务/生成 schema/录包 decoder/转换 wheel 显式打包；mock 时间拼接、末端投影、OSQP 迭代及标准 PNC 停车语义修复。CPU/all 干净容器共 12 次 PNC/ML/静态避障运行、严格比较、算法状态与回放均通过，交付 v6；原失败证据保留，详见 package/test-artifacts/web-monitor-simulation-20261007。

- [x] 二进制包收集 forked Viewer 和源布局；原启动器按自身位置解析、直接 exec；移动路径/缺依赖回归及干净容器 HTTP/gRPC、JS/WASM、PNC 验收通过。

- [x] scene_editor 地图迁移：任务快照自动加载、Source 手动选图、共享定位原点、静态路面/边界/中心线、三布局图层开关与双时钟浏览器验收
- [x] 俯视交互平面锁定、主车位置/heading 持续跟随、后上方追车与车头朝上俯视、自由/跟随切换及舒适缩放范围；正式 9090 原始 bag 浏览器与 pose 数值回归
- [x] 底盘反馈仪表盘（3D 底中可收起）、Top / Ego 相机快捷键按钮与共享窗口预加载；Dreamview 取值链核对及浏览器回归
- [x] Layers 分层复选框、已缓存实体即时隐藏、黄色高度点云、感知/预测图层（semantic-mcap-v12）及浏览器回归
- [x] Planning/Control 共用点云、真实定位 GLB 与规划 3D 轨迹（semantic-mcap-v11）
- [x] 八类调试工具迁移到停靠 ViewClass；Planning 四窗、Control 六窗；增删、分屏/标签页与 Custom/.rbl 保存
- [x] 初始化项目 AI 上下文（`AGENTS.md`、`.cursor/rules`）
- [x] Rerun 0.19.1 → 0.37.1 升级与 AD UI 迁移（`docs/RERUN_UPGRADE.md`）
- [x] 按本地规则整理并填写 `Project.md` / `Architecture.md` / `Current-Status.md` / `TODO.md`
- [x] host ↔ `/apollo_workspace` 路径映射（R8）
- [x] Browse 去掉 `accept=.record`（R10）；header 驱动媒体条（R9）
- [x] **no-fallback 规则**（`.cursor/rules/no-fallback.mdc`）+ AGENTS
- [x] semantic-mcap-v10：Cyber `ProtoDesc`→FDS，失败即报错（对齐 Dreamview/GetProtoDesc）
- [x] Topic View：去掉 SelectionPanel `data_ui`；`/api/topic_debug` 按需 DebugString（R12）
- [x] 窗口化播放 + Panel topic 勾选（默认无点云）
