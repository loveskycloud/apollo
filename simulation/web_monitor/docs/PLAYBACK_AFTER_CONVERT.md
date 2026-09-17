# Convert 完成但仍无法播放 — 根因手册

> 现象：`Loaded …/.wm_mcap_cache/*.mcap` / 转换 100%，视口空白、媒体条无法 scrub 出点云/相机。  
> 本文沉淀反复踩过的根因链，按优先级排查。

## 快速结论（当前默认行为）

| 项 | 正确做法 |
|----|----------|
| 转换器 | `semantic-mcap-v10`：Cyber `ProtoDesc`→完整 FDS + 非大体积 protobuf payload；失败即报错（无 raw 静默降级）。相机/点云仍 schemas-only + Foxglove 语义层 |
| 播放时钟 | **只有两种**：Publish time（`message_publish_time`）与 Message time（`message_log_time`）。窗口播放固定使用 Message time，与 MCAP 查询一致。 |
| 时间范围 | **Cyber header** `begin_ns`/`end_ns` 直接驱动 Properties + 媒体条时长（不扫 EntityDb） |
| 打开后 | 保持暂停；定位到所选通道首个可展示位置（相机需要自包含关键帧）；流式加载时每帧重试直到时钟就绪。后续选通道/跳转不自动播放。 |

### 名词：什么是 raw payload？

转换时对每个 Apollo topic 可以做两件事：

1. **注册 schema（channel）**：MCAP 里出现这个 topic 名 → Topic View 能列出来（即使 0 条消息）。
2. **写入 raw payload**：把 bag 里原始 protobuf 字节再原样写进 MCAP。

`v10` **默认**：

- Topic 列表与 bag 一致。
- Schema = Cyber `GetProtoDesc` 递归树转成的 `FileDescriptorSet`（与 Dreamview/cyber_monitor 同源）；转换失败则整 job 报错。
- **非大体积** topic（pose / chassis / gnss…）写入 protobuf payload → Topic View 经 host DebugString 展示。
- 相机 / 点云默认不写透传字节（用 Foxglove 语义层）；需要全量时加 `--passthrough-payloads`。

## 根因链（按出现频率）

### R1 — 游标不在数据时间范围内 / 流式未就绪就夹一次（最常见）

- **现象**：文件已 Loaded，Host 无 `Failed to load`，但视口空。
- **机制**：Apollo/Cyber 时间戳是绝对纳秒（~1.7e18）。打开后若播放头停在 0，LatestAt 查不到任何帧。  
  且 `open_local` 先 attach 空 store，**MCAP 时间轴晚几帧才出现**；若只夹一次会直接 return，之后不再重试。
- **修复**：默认 Publish time；`SetActiveTimeline` 后 `SetTimeClamped(range.min)`；`playback_clock_ready` 未就绪时每帧重试；UI 若落在 `timestamp`/`log_time` 也强制切回 Publish/Message。

### R2 — Apollo protobuf schema 不完整，整包导入失败

- **现象**：`Loaded …mcap`，Host 有  
  `Failed to load MCAP file: Invalid schema … has not been added`。
- **机制**：Cyber `GetProtoDesc` 只给单文件 `FileDescriptorProto`，缺 import；decoder 遇错即失败。
- **修复**：透传层用 **`raw` encoding**；Decoder 对坏 schema **软跳过**。

### R3 — 全量 raw 透传把 MCAP 撑到数 GB，流式“假完成”

- **现象**：转换很快显示 Loaded，画面长时间空。
- **机制**：`open_local` 在 attach stream 时就返回成功；UI 写成 Loaded。
- **修复**：`v7` 默认 **只注册 schema、不写透传 payload**。

### R4 — Layout 未绑到数据 recording（P26/P27）

- **现象**：有数据 store，但 Perception 分区不显示实体。
- **修复**：`opened_store_id = active_recording.store_id()`；无数据时先 seed workspace recording。

### R5 — receive_set 死锁（P29）

- **现象**：Host accepted stream，前端永远无 chunk。
- **修复**：`LogReceiverSet::recv` 短超时放锁；大文件用 host `open_local`。

### R6 — 缓存 MCAP 被删除后报 Open failed 400

- **现象**：`Open failed (HTTP 400): file not found: …/.wm_mcap_cache/*.mcap`
- **原因**：上次转换的 progress / 内存 job 仍报 `done`/`ready`，UI 直接 open 已删文件。
- **处理**：`start_or_cached` 发现 MCAP 缺失则作废 progress 并重新排队；前端 open 400 时对 `.record` 自动再转。

### R7 — Spatial3D 误含相机 / 坐标系解析失败（红感叹号、视口空）

- **现象**：Publish time 已选中、Host 无 Invalid schema，Perception 3D 有红 `!`，点云不画；点开 `!` 可见  
  `2D visualizers require a pinhole ancestor to be shown in a 3D view`（`/camera/...`）。
- **机制**：
  1. Perception 3D `contents=/**` 把 `/camera/*` 的 VideoStream 拉进 3D 视图 → 报红叉。
  2. 命名 `CoordinateFrame`（map/vehicle/lidar）与 `origin=/` 组合时，视图空间也可能解析失败。
- **处理**（v8 + 新布局）：
  - 3D contents 只用 `+/lidar/**`、`+/vehicle/**`、`+/tf/**` 等，**排除** `/camera/**`。
  - 3D `origin=/lidar/up/points`；Pose/PointCloud/Video 用空 `frame_id`。
  - 打开后仍将游标夹到 Publish time 的 `range.min`，时间轴未就绪时重试。

### R8 — Source 粘贴 host 路径 `/home/.../apollo/...` 报 file not found

- **现象**：Open path 使用 `/home/wangsheng/code/apollo/data/bag/*.record…`，convert/open 失败；Properties 空。
- **机制**：aem 容器内只有 `/apollo_workspace`（`APOLLO_ENV_WORKROOT`），host checkout 路径不可见。
- **修复**：`rewrite_apollo_workspace_aliases` 在 resolve 时把 `APOLLO_ENV_WORKSPACE` ↔ `WORKROOT` 互映；host 与 workspace 路径均可。

### R9 — Properties / 媒体条 Start·End 像“算出来的”

- **现象**：转换完成后时间轴时长随流式加载变长；Properties 曾为 `—`。
- **机制**：原先用 EntityDb `time_range_for`（随 chunk 增长）。
- **修复**：convert `meta` 带 Cyber header `begin_ns`/`end_ns`；AD shell 写入 egui temp；Properties + 收起态媒体条 **直接用 header**。

### R10 — Browse 文件管理器看不到 `*.record.00000.*`

- **现象**：自定义文件只显示 `.rrd`/`.mcap`；需“所有文件”才见 bag。
- **机制**：`<input accept=".record">` 按**扩展名**过滤；Apollo 名是 `….record.00000.…`，扩展名是时间戳数字。
- **修复**：去掉 `accept`；在 change 回调里用 `name.contains(".record")` 校验。

### R11 — Topic View 看不到 `/apollo/localization/pose` 等内容

- **现象**：topic 列表有名字，点开无字段 / “Entity not in recording”。
- **机制**：v8 默认 schemas-only + `raw` encoding（Cyber 单文件 FDS 不完整）。
- **修复（演进）**：v9 曾用 Apollo pb2 拼 FDS；**v10** 改为 Cyber `GetProtoDesc` 递归 `ProtoDesc`→`FileDescriptorSet`（与 Dreamview 同源），失败即报错（见 no-fallback）。

### R12 — Topic View 打开 pose 卡死 UI

- **现象**：点选 `/apollo/localization/pose` 后浏览器/wasm 长时间无响应。
- **机制**：`data_ui(..., SelectionPanel)` 每帧把 protobuf→Arrow 嵌套树画进 egui，字段多时爆炸。
- **修复**：Topic View **禁止** SelectionPanel `data_ui`；改为 Dreamview 式按需解码：`POST /api/topic_debug` → host `mcap_topic_debug.py` → `DebugString`；错误红字展示；超 200k 字符拒绝截断。规则见 `.cursor/rules/no-fallback.mdc`。

## 验收清单

### 暂停预览与数据缓存（2026-09-11）

浏览器端超时计时必须使用 `web_time::Instant`，与 Viewer 其他计时逻辑一致。
`std::time::Instant::now()` 在 `wasm32-unknown-unknown` 上虽然可以编译，但运行时会触发 `time not implemented on this platform` panic；2026-09-12 已修正窗口请求计时。

- 勾选 Apollo 相机通道时，同时请求其 `/camera/<name>` 语义视频通道。
- Host 按文件身份缓存 H.264 关键帧时间索引；跳转窗口回溯到自包含 SPS/PPS/IDR，而非固定回看一秒。
- 初次打开时定位到所选相机均有可解码关键帧的位置；后续勾选、拖动保持当前时间。
- 当前时间早于关键帧时明确提示没有可解码图像，不把后面的画面当成当前帧。
- 暂停也预加载前方约五秒，每次请求两秒；拖到未缓存位置主动请求对应窗口。
- 底部半透明色条表示当前通道集合已接收的数据范围。通道变化使缓存范围失效，重新确认后显示。
- Host 导入结束后在同一数据流追加 temporal receipt；浏览器 EntityDb 收到回执才扩展缓存条。不能使用 static receipt，gRPC 给新订阅者优先发送 static 数据会破坏确认顺序。
- 播放临近缓存边界时暂停等待，足够数据到达后恢复。色条确认的是数据和解码依赖已到达，并非所有图片都已预解码到 GPU；实际帧率仍取决于设备解码和绘制能力。
- H.264 网页预览依赖 WebCodecs：本机使用 `http://127.0.0.1:9090/`；远程部署需要 HTTPS 安全上下文。

验证：`python3 -m unittest -v test_mcap_playback_plan`（在 `tools/apollo_record_tools`），容器内构建 Wasm/CLI，临时端口请求首帧及第十二秒窗口，并通过 gRPC 录制检查视频与回执顺序。

1. Browse 默认列表可见 `*.record.00000.*`（无需“所有文件”）。
2. 打开 `.record` → 转换 `semantic-mcap-v10` → 自动打开 cache MCAP。
3. Host 日志：**无** `Failed to load MCAP`；坏 schema 应见明确 `error!`（非静默成功）。
4. 媒体条时长 = header end−begin（一打开即完整，不随流式变长）。
5. Topic View：`/apollo/localization/pose` **不卡死**，可见 DebugString 或红字错误。
6. Perception：点云 + 相机可见，可 scrub。

## 复现实验（容器内）

### 2026-09-12：暂停首帧/跳转修正（真实 Chromium 验收通过）

- `reset` 是 JSON boolean，旧 `json_field` 仅处理字符串，始终把首窗当作 merge。
  现在由 `ad_playback::WindowReceipt` 强类型解析，非法响应明确报错。
- 删除摄像头 `ready_ns` 对所有通道预加载的全局阻断。
  跳转到视频首关键帧之前仍须请求点云/位姿；不能用摄像头缺帧代替整窗未加载。
- Windowed playback 使用 `message_log_time`，与 MCAP 时间过滤一致。
  初始化索引包含非视频通道首条消息，只有点云时不再停在其首条消息之前。
- HTTP 回执改用标签页独立 `sessionStorage`，避免其他 Viewer 标签页取走响应。
- 删除已无请求入口的 camera bootstrap pending-add 状态与处理分支。
- 初始化时钟尚未就绪时不能把旧 workspace 的 Playing 状态当作缓冲恢复意图；用户暂停或跳转会清除自动恢复意图。
- Host 按 topic 记录已导入区间，仅补缺失区间；避免重叠窗口重复导入同一 H.264 GOP 导致重复采样、解码异常。
- 每个相机独立从其首个自包含关键帧开始导入；点云和位姿不受该相机起点限制。
- Topics 移到 3D 区域左上角，避开右侧通知，防止通知拦截勾选。

测试使用原始指定 bag 的缓存 `2b0b1ee6de553d65.mcap`。
`/lidar/up/points` 共 198 帧，首条 log time 为 `1778730051099898624`（bag 起点后 125.556399 ms）。
独立测试服务首窗响应的 seek time 与之严格相等；gRPC 实际输出 19 个带 `Points3D:positions` 的首窗数据块。
窗口规划 7 项 Python 测试、生产 Rust 协议断言、Wasm clippy、Wasm/CLI 构建通过（有原有警告）。
容器未安装 cargo-nextest，协议断言由 `scripts/playback_protocol_tests.rs` 直接链接现有 serde 构建产物执行；同一生产模块也包含标准 Rust 单元测试。

用户授权安装后，在 `apollo_neo_dev_wangsheng` 内使用 Playwright 1.62.0 / Chromium 151（独立 profile、SwiftShader WebGL2）完成真实鼠标操作。
测试没有通过 JavaScript 设置播放状态，仅用只读 `get_point_cloud_state` 查询渲染器同源 LatestAt 的时间戳和点数，并与原始 MCAP 的 198 帧逐项对照。

验收产物：`test-artifacts/paused-seek-20260912/run4/`，含截图、请求响应、浏览器日志、`evidence.json`。

| 操作（前三次跳转前从未点击 Play） | 相对 bag 时间 | 对应点数 | 等待当前点云 |
| --- | --- | --- | --- |
| 加载后勾选 `/lidar/up/points` | 8.114799 s | 102417 | 0.591 s |
| 向前段跳转 | 2.534828 s | 103997 | 1.072 s |
| 跳到未缓存后段 | 15.842675 s | 98671 | 4.348 s |
| 再向前跳转 | 5.163538 s | 102591 | 0.304 s |

每次均保持暂停、游标不漂移，截图确认点云实际可见；浏览器无 error/pageerror/panic。
随后点击 Play，在已缓存区间采样约 3 秒，得到 17 个不同点云帧，全部与 MCAP 对应，没有因缺缓存而暂停。
软件渲染测试中媒体时间推进约 1.879 秒：这证明缓存供给和播放逻辑正常，**不等于已验证硬件加速环境的实时帧率**。
后段跳转截图以点云就绪为准，相机可能仍在异步解码；本次自动化没有断言每路相机的图像就绪时间。

重复执行（先重启测试服务，使用空 recording 会话）：

```bash
docker exec -u wangsheng apollo_neo_dev_wangsheng python3 \
  /apollo_workspace/modules/simulation/web_monitor/scripts/test_paused_playback_browser.py \
  --record /home/wangsheng/code/apollo/data/bag/20260514114049.record.00000.20260514114049 \
  --mcap /apollo_workspace/data/bag/.wm_mcap_cache/2b0b1ee6de553d65.mcap \
  --out /apollo_workspace/modules/simulation/web_monitor/test-artifacts/paused-seek-20260912/recheck
```

设计参考：[Foxglove 的 log-time、lookback 和缓冲行为](https://docs.foxglove.dev/docs/visualization/playback)。

```bash
python3 /apollo_workspace/tools/apollo_record_tools/apollo_record_to_semantic_mcap.py \
  -o /tmp/t.mcap --camera all --gpu off --duration-ms 1000 --begin-ns <NS> <bag>
python3 /apollo_workspace/tools/apollo_record_tools/mcap_topic_debug.py \
  --mcap /tmp/t.mcap --topic /apollo/localization/pose
./bin/rerun mcap convert /tmp/t.mcap -o /tmp/t.rrd
./bin/rerun rrd stats /tmp/t.rrd   # 应见 message_publish_time + Points3D/VideoStream
```

## 相关代码

| 位置 | 作用 |
|------|------|
| `tools/apollo_record_tools/apollo_record_to_semantic_mcap.py` | v10：ProtoDesc→FDS + 非大体积 payload |
| `tools/apollo_record_tools/mcap_topic_debug.py` | 按需 DebugString（fail loud） |
| `rerun/.../web_tools.rs` | Browse / convert / `topic_debug` HTTP |
| `rerun/.../ad_shell.rs` | Topic View DebugString；header 时长 |
| `rerun/.../time_panel.rs` | 媒体条时长优先 header |
| `rerun/.../re_mcap/.../protobuf.rs` | 坏 schema `error!` + skip Arrow |
| `rerun/.../entrypoint.rs` | `open_local` + `/api/topic_debug` |
| `.cursor/rules/no-fallback.mdc` | 禁止静默降级 |
