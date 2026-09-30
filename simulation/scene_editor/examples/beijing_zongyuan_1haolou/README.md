# 北京总院一号楼场景集

场景预期与合理性审核见 [SCENARIO_AUDIT.md](SCENARIO_AUDIT.md)。

地图：`beijing_zongyuan_1haolou`，保留 396 个场景文件，当前有效集合 381 个；15 个永久阻塞用例标记 INVALID 并移出集合。组成：原 128 个直路用例，加上覆盖全图 76 条有方向车道的 76 个通行用例，以及 24 个路段/方向 × 8 类障碍交互用例。路线由真实地图前后继车道连接。
适配当前 Ranger Mini V3 和 `perfect_planning`；原 8 个回归场景默认 70 秒，原 120 个变体默认 90 秒，全图复杂场景默认 180 秒。

在 Web Monitor → Sim → WorldSim → **Scenario suite** 选择
`beijing_zongyuan_1haolou.suite.json`，地图会自动匹配。
Vehicle 选择 `ranger_mini_v3`，Planner 选择 **Planning** 或 **ML Planning**。
Concurrent scenarios 选择 1–30，然后点击 **Run scenario suite**。
每个成员独立显示进度、错误和 Replay；成员失败不会停止剩余成员。
Runs 是每个场景的重复次数，用于确定性比较，不是并发数。

| 文件前缀 | 场景 | 位置触发 |
| --- | --- | --- |
| straight | 无障碍直行 | 无 |
| moving_lead | 慢速前车 | 前车初始可见，以 0.55 m/s 行驶 |
| crossing_vehicle | 车辆横穿 | 主车接近 s=11 m 时激活 |
| pedestrian_sudden | 近距离突发行人 | 主车接近 s=14 m 时在 s=18 m 激活 |
| pedestrian_left | 左侧行人横穿 | 主车接近 s=11 m 时激活 |
| pedestrian_right | 右侧行人横穿 | 主车接近 s=12 m 时激活 |
| pedestrian_wandering | 行人多次折返游走 | 主车接近 s=9 m 时激活 |
| multi_trigger | 两次横穿和变速 | s=7 / 19 / 22 m 三个区域依次触发 |

上述基础场景的触发区域长 1.5 m、宽 3 m；全图扩展场景使用长 0.8 m、宽 1.0 m 的触发区。区域均按车道朝向旋转；进入区域就触发，不是精确到达中心才触发。
游走使用可编辑、可复现的折线路径，未引入运行时随机采样。

每个 `.mineproj.json` 都内嵌地图，可在 scene editor 的「项目 → 打开」继续编辑。
同名 `.worldsim.scenario.json` 由编辑器共用导出器生成，供 WorldSim 使用。
编辑后重新导出覆盖对应场景文件；清单使用相对路径，无需改绝对路径。修改后必须重新审核同名 `.evaluation.json` 中的预期、等待区和源文件 SHA-256；服务会拒绝过期判据，不能只更新哈希而跳过合理性审核。

重新生成示例（会覆盖本目录生成文件；先另存自定义修改）：

```bash
node modules/simulation/scene_editor/tools/create-beijing-suite.mjs \
  data/map_data/beijing_zongyuan_1haolou/base_map.txt
```

新增 120 个场景由 12 个行为类别 × 10 种参数组合构成：无障碍直行、左右横穿、突发出现、远处横穿、折返游走、斜向穿行、连续两次横穿、对向行人、慢速前车、横穿车辆、路侧障碍与离开。变化包括纵横位置、速度、出现距离、方向、路径和位置触发区，不是只替换名称。

Sim result 对每个运行检查真实 WorldSim 车身碰撞（旋转矩形 SAT，使用车辆实际前后左右边界，默认每 10 ms 检查）。任意接触或任何重复运行碰撞均判失败。检测缺失/未完成不会判通过。ML 任务按场景预期检查到达或安全停车，同时检查 estop、直路居中和连续横摆；完整行为仍需回放审阅。

旧的 8 场景“7 个到达终点”的记录是历史执行结果，不是用户认可的行为验收。新旧对比、128 场景运行和页面证据位于 workspace 的 `data/simulation/v3-validation-20260929/`。


全图扩展复现：先运行上述基础生成器，再运行：

```bash
node modules/simulation/scene_editor/tools/create-beijing-coverage.mjs \
  data/map_data/beijing_zongyuan_1haolou/base_map.txt
```

`coverage.json` 列出每个新增场景的锚点车道、连接路线、起终点里程与障碍物数量。复杂类别包括左/右侧静态物体、交错静态障碍、超车、三辆连续小车、六名行人、四辆连续横穿小车以及静态物体与行人混合。左右弯、掉头和正反行驶方向均通过地图真实连接生成。车辆/行人路线和位置触发器都保存在可编辑项目中。

验收要求：有效场景全部无碰撞且规划轨迹连续可用；204 个场景要求到达、177 个要求动态让行后到达。当前按用户要求只回归失败用例，不为并发测试重跑全量。`beijing_zongyuan_1haolou.failed.suite.json` 更新为本轮复测后仍失败的 6 个有效用例（本轮实际执行 7 个）；`excluded` 保存 15 个无效场景及审核依据。`passability-evidence.json` 保存 Lane_65_static_right 的真实到达证据，纠正局部扫掠估计误判。可通行场景不能用持续停车冒充通过。诊断阶段提前终止的队列不是最终验收。后续证据位于 workspace `data/simulation/v4-validation-20260929/`。
