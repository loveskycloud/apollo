# Ranger Mini V3：Apollo 10 仿真兼容配置

此目录已按容器 `apollo_neo_dev_wangsheng` 的已安装插件和 protobuf 定义适配。
不兼容配置保留为注释，车辆参数未修改；原始 zip 和 demo 场景未修改。
这是仿真兼容版本，不是原扩展算法能力的等价替代，也未经实车验收。

## 如何运行

刷新 Web Monitor 9090 → Sim，选择：

| 设置 | 值 |
|---|---|
| 输入 | WorldSim / JSON |
| Scenario | `/apollo_workspace/data/scenarios/ranger_mini_v3_1haolou_smoke.scenario.json` |
| Map | `/apollo_workspace/modules/map/data/1haolou_202608241047qh` |
| Vehicle config | `/apollo_workspace/profiles/ranger_mini_v3/modules/common/data/vehicle_param.pb.txt` |
| Profile | `/apollo_workspace/profiles/ranger_mini_v3` |
| Modules | PREDICTION、PLANNING、CONTROL、ROUTING |
| Ego model | kinematic_control |
| Step / Seed / Runs | 10 ms / 1 / 2 |

新增场景是从地图 Lane_9 中心线采样的 10 秒无障碍物启动测试，不代表碰撞或复杂场景验收。
原 `demo.scenario.json` 在 `(0,0)`，与本地图约 `(10000,9000000)` 的车道坐标不匹配，
不能仅修复 profile 后继续使用原 demo。缺少的 `sim_map.txt/bin` 已使用 Apollo
`sim_map_generator` 从原 `base_map.txt` 生成，没有更改 base/routing 地图。

已从正式 9090 提交的任务：`53c250d9aa3e4a1d`，可直接点击 Replay bag。
失败任务 `80948fc3f4af4e0d` 保留原始配置快照；修改 profile 不会改写历史任务。

## 兼容修改

- 注释未安装的 PathFollowScenario、PathFollowMap、TemporaryStop。
- 注释 reference_line_offset_config 及对应 topic；本版本没有参考线偏移扩展。
- LaneFollow 恢复标准 LaneChangePath / LaneFollowPath / LaneBorrowPath / FallbackPath，
  不再引用 ReferenceLineAsPath、ReferenceLineOffsetStopDecider。
- LaneFollow 和 EmergencyStop 不再配置缺失的 SmoothStopTrajectoryFallback，
  使用当前 Stage 自带的标准 FastStopTrajectoryFallback。
- Control 改为已安装的 LatController / LonController，使用本 profile 中的标准控制器参数；
  不再加载 LatPlusController、LonPlusController、DebugInfoControlTask。
- 注释 19 个当前源版本未定义的启动 flag，以及 6 个任务配置的未知 protobuf 字段。
- Planning / Control 的 DAG 和 flagfile 中 `/apollo/modules/...` 改为 `modules/...`，
  遵循任务隔离目录，避免读到另外工作区的旧配置；修正三横线 calibration_table_file。
- 未启用的扩展插件目录保留，不能单独重新启用而不安装匹配插件。

预检查器同时修复了“注释中的 type 仍被视为插件依赖”，缺失的有效配置仍然报错。

## 运行中额外定位的边界错误

`modules/map/pnc_map/path.cc` 的 GetProjectionWithHueristicParams 原先包含访问
`segments_[num_segments_]` 的越界循环。路径末端触发时，DEST 障碍物 SL 边界出现
随机百万量级坐标，导致相同输入两次仿真比较失败。现将首尾搜索索引限制在真实线段内，
并拒绝反向/非有限搜索区间；不是通过删掉 debug 数据或放宽比较容差规避问题。

## 验证记录

- 使用实际安装 schema 检查 71 份配置；任务服务 9 项单元测试通过。
- 新增 C++ `TestSuite.HeuristicProjectionAtPathEnd` 通过，覆盖搜索到末端、
  超过末端、末端零长度区间及非法区间；测试构造包含有效车道信息。
- `data/simulation/ranger-compat-check3/`：有效 od_hq 场景，两个 3 秒运行一致。
- `data/simulation/ranger-compat-check4/`：保留修复前投影越界导致的真实重复比较失败。
- `data/simulation/ranger-compat-check5/`：本地图两个 10 秒运行通过，101 帧有效规划，
  主车移动约 6.63 m，3309 条有序消息的算法比较差异为 0。
- 正式任务同样重复两次通过；浏览器回放截图与状态位于
  `simulation/web_monitor/test-artifacts/ranger-profile-20260913/replay1/`。
- 离线检查任务与正式 9090 任务之间再次比较 3309 条消息，算法差异为 0。

比较只排除明确列出的墙钟耗时统计并规范 protobuf map 顺序，不忽略轨迹/控制数值。
Control 启动前 0.1 秒的 10 帧未就绪状态仍保留，随后 991 帧状态正常。
以上结果不构成实车安全或任意环境确定性的承诺。
