# Algorithm debug panels

入口：左侧 **Panel**。
每次点击创建一个 Layout 内的停靠窗口（Panel），不再创建浮窗。
拖动标题可停靠/分割/组成标签页；Panel 列表中的 × 删除窗口。
使用 Save as Custom layout 保存窗口组织和各面板的 topic、字段、固定值等配置。
工具直接按需查询 bag 的 MCAP，不需要先勾选对应的 3D playback topic。
所有工具只读，不向车辆或 Cyber 发送控制指令。

## 工具与典型用途

| 工具 | 能力 | 调试用途 |
| --- | --- | --- |
| Topic inspector | 原始 protobuf 字段路径/值、筛选、JSON 复制、前后消息跳转、固定字段 | 检查 control/debug、planning、perception、system_status 的实际消息 |
| Signal plot | 跨 topic 数值字段、重复字段索引、多曲线、窗口长度、游标、点击跳转、min/max/mean/RMS | 比较指令与反馈、检查振荡/尖峰/时延 |
| Value watch | 固定字段表、字段搜索与勾选、缺失值提示 | 同时盯住油门、制动、转角、模式等关键值 |
| Control dashboard | 12 条真实信号、7 组按量纲分开的图 | 速度/加速度跟踪、转向响应、踏板、纵横向/航向误差、控制器耗时 |
| Trajectory XY | 当前规划轨迹 + 最近 10 秒定位轨迹 + 当前位姿 | 检查规划与实际行驶偏差；Apollo map XY 米制，不额外做 TF 变换 |
| State transitions | 枚举/布尔/离散值的变化列表，点击事件精确跳转 | 查挡位、自动驾驶模式、停车状态变化 |
| Topic health | 通道数量、bag 平均频率、当前消息年龄、选中 topic 的间隔 min/median/max、重复时间与长间隔 | 检查数据覆盖、节拍、时间戳异常 |
| Planning profile | 当前规划的 v、a、kappa 对 relative_time 曲线 | 检查速度、加速度、曲率连续性；三种单位分图 |

3D、图像和算法工具全部由同一个 Rerun viewport/blueprint 管理。
默认布局和操作说明见 [AD_LAYOUTS.md](AD_LAYOUTS.md)。
未添加地图瓦片、在线拓扑或车辆指令发布功能。

## 使用约定

- 默认 **Follow playhead**；暂停和时间轴跳转都会更新当前消息。
- Topic 输入框下方的 **Browse topics (数量)** 展示当前 bag 的真实通道目录，可输入关键词筛选后选择。选择器独立占一行，窄停靠窗口不需要横向滚动才能访问；点击搜索框不会关闭列表。
- 清空 Topic 时进入待选择状态，不发送空 topic 查询；输入当前 bag 不存在的路径时明确提示重新选择，不自动替换成其他 topic。筛选无匹配与源目录为空分别提示。
- 取消 Follow 会冻结查询结果；Refresh 只刷新一次，重新勾选 Follow 恢复跟随。
- Inspector 的 **Previous message / Next message** 会暂停并精确定位到该 topic 的消息时间。
- 字段路径保留 protobuf 原名，例如 `debug.simple_lat_debug.lateral_error`、`latency_stats.controller_time_ms[0]`。
- Inspector / Watch 的勾选框固定字段；Inspector 的 **Watch pinned fields** 将当前窗口切为监视表，**Plot pinned numeric fields** 将固定数值字段转成曲线。
- Signal plot 的 Topic/Field 输入用于 **Add signal**；已有曲线的来源会逐条列出，可单独移除。
- 图中的 X 轴是相对 bag header 起点的秒数，竖线是实际播放头。查询窗口围绕播放时间，不表示未来数据已经播放过。
- 状态面板显示窗口左边界前的最后状态及窗口内变化，并非每条重复消息都占一行。
- Topic health 的平均频率是 `消息数 / MCAP 时长`，不是自动判定的额定频率；大于两倍中位间隔的统计也不是自动故障结论。
- 缺消息、缺字段、不支持的类型和超出查询预算均显示明确提示；不会补零或拿未来消息冒充当前帧。

## Control 预设

| 图 | 字段 |
| --- | --- |
| Speed (m/s) | control `debug.simple_lon_debug.speed_reference` / chassis `speed_mps` |
| Acceleration (m/s²) | control `debug.simple_lon_debug.acceleration_reference` / `current_acceleration` |
| Steering (%) | control `steering_target` / chassis `steering_percentage` |
| Pedals (%) | control `throttle` / `brake` |
| Tracking error (m) | control `debug.simple_lon_debug.station_error` / `debug.simple_lat_debug.lateral_error` |
| Heading error (rad) | control `debug.simple_lat_debug.heading_error` |
| Runtime (ms) | control `latency_stats.total_time_ms` |

control 与 chassis 是不同采样源，按各自消息时间绘图，不伪造一对一同步采样。
后端按 topic 分组解码后必须恢复请求顺序，否则会把不同量纲曲线配错；有专门回归测试。

## 查询架构与边界

`ad_debug_view.rs`（注册的 AdDebug ViewClass，配置保存在 blueprint）→ `ad_debug_panels.rs` → `POST /api/debug_query` → CLI 持久 Python worker → MCAP + descriptor 解码。
原 `Add grid / Add lines / Add points` 占位按钮与旧 Topic View UI/前端回调已删除。
旧 `/api/topic_debug` 仅保留兼容，并修正 Python protobuf 没有 `DebugString()` 的错误。

- 产品查询时钟仅为 `publish_time` / `message_time`，当前消息采用 latest-at，纳秒以字符串穿过 JSON/JS，避免整数精度丢失。
- 每个面板独立接收异步结果；切换 topic/文件后不会混入旧响应。
- 首次没有结果时显示加载圈；同一查询配置的后台刷新保留上一批结果并原位替换，不插入加载行。固定高度状态栏显示真实样本时间/查询窗口，Sample age 相对实时游标计算。
- 超过 750 ms 的后台请求明确提示正在保留旧结果；后退跳转遇到旧样本晚于游标时标记 Previous sample。切换文件、时钟、工具或信号配置会丢弃旧查询结果；错误/超时仍显式显示，不用旧图表掩盖失败。
- 界面查询最多约 4 Hz；曲线按两秒分桶更新查询窗口，绘制/游标不以此代替播放时钟。
- 索引按文件路径、大小和 mtime 作废；原始 topic 索引单项最多 128 MiB，总 LRU 预算 256 MiB（不含 Python 对象等额外开销）。
- 最多 24 条信号、120 秒窗口、每信号 12000 点；不静默降采样。单消息 1 MB / 12000 叶字段、响应 4 MiB。
- 大点云/图像应使用已有 3D/Image 视图，不能在字段检查器里无限展开二进制传感器数据。
- worker 超时 45 秒、前端超时 50 秒；失败后显示错误，用户 Refresh 重试。
- 当前 host 查询工具面向 Web Viewer；native 模式显示明确说明。

## 可重复验收

Topic 选择器回归：`scripts/test_topic_picker_browser.py --out <证据目录>`（默认正式 9090、任务 `d897596d6b454fe1`，可用 `--job` 指定兼容数据）。真实鼠标打开 Sim 回放和 Inspector，核对目录接口与选择器，清空输入、搜索无匹配、选择 pose/control、输入不存在的 topic、缩小窗口并暂停跳转到 30 秒。记录截图、字段、实际查询请求和浏览器错误；`--staging-assets` 仅用于构建期间预检，正式验收不得使用。

2026-09-12 后台刷新回归：`scripts/test_panel_refresh_browser.py`，产物 `test-artifacts/panel-refresh-20260912/run3/`。
真实 Chromium 在 Planning 播放 7 秒、Control 播放 9 秒，并注入每次 HTTP 查询 350 ms 延迟。
共 240 个 pending Panel 采样保留数据，首次加载后 spinner 为 0、内容区域顶部位移为 0；Planning 各工具更新 15–16 个版本，Control 更新 5–6 个版本。
同时验证暂停 seek 的查询时间收敛到精确游标，以及注入 HTTP 500 后明确显示错误、不会用旧结果掩盖失败。
Wasm/native Clippy、完整构建和 18 项 Python 回归测试通过。

真实数据：`20260729201441.record.00000.20260729201441`，缓存 `015f40ec5fab311b.mcap`。
该 bag 含 37 个通道，control 5994 条、control/debug 5995 条、chassis 5997 条、planning 598 条。

```bash
docker exec -u wangsheng -w /apollo_workspace/modules/simulation/tools/apollo_record_tools \
  apollo_neo_dev_wangsheng python3 -m unittest -v test_mcap_debug_query test_mcap_playback_plan

# 独立、干净的测试服务：HTTP 9091，gRPC 9877
docker exec -u wangsheng apollo_neo_dev_wangsheng python3 \
  /apollo_workspace/modules/simulation/web_monitor/scripts/test_debug_panels_browser.py \
  --out /apollo_workspace/modules/simulation/web_monitor/test-artifacts/debug-panels-20260912/recheck
```

浏览器脚本只通过真实鼠标/键盘改变 UI，JS API 只读取诊断状态。
截图、API 响应、浏览器日志和逐项证据保存在输出目录。
测试使用独立 Chromium profile 和 SwiftShader 软件 WebGL；不能据此宣称硬件实时帧率。

### 2026-09-12 早期浮窗版本验收记录（现已迁移为停靠窗口）

`test-artifacts/debug-panels-20260912/run8/` 完整通过，七类面板均有真实浏览器截图。
验证了暂停跳转后 control 消息时间戳、转向值、制动值与 MCAP 完全一致，上一条/下一条精确定位，字段筛选、冻结/恢复、JSON 复制、固定字段转 Watch/Plot、自定义曲线、图表点击、挡位事件跳转与双面板同步。
Control 看板的 12 条信号各取到 1000 个窗口样本；跨 topic 顺序正确，转向/制动的样本数和 min/max/mean 与独立 MCAP 读取对照一致。
轨迹在两个暂停位置分别显示 147 / 206 个规划点和 999 / 1000 个定位历史点。
健康面板正确显示 37 个通道及 control 的 5994 条消息；不存在的 topic 显示明确错误。
浏览器无 error/pageerror/panic；15 项 Python 测试、Wasm/native clippy、Wasm/CLI 构建通过（保留原有警告）。
旧 bag 的暂停点云回归同样通过，产物在 `test-artifacts/debug-panels-20260912/lidar-regression/`：勾选点云约 0.425 秒后可见，三次暂停跳转与 MCAP 对齐，缓存内播放采样到 17 个不同点云帧。
