# 泊车途中动态触发场景集 V1

最新进展：已补齐制动、等待与恢复，动态任务由 0/48 提升到 48/48。实现和验证见 [PARKING_DYNAMIC_FIX.md](PARKING_DYNAMIC_FIX.md)。本文末尾保留的是修复前基线。

在 Web Monitor → Sim 选择 **泊车途中动态触发 V1 · all**。
入口为 `../scene_editor/examples/parking_dynamic_triggers_v1/all.suite.json`，共 48 个场景。

## 覆盖范围

3 种车位（平行、垂直、45°斜列）× 2 种停车朝向（原车头/车尾入位对应姿态）× 2 种任务（泊入、泊出）× 4 种干扰。

| 干扰 | 数量 | Trigger 动作 |
|---|---:|---|
| 行人横穿 | 12 | ego 进入泊车过程中的位置区 → 启用行人 → 行人到达出口后禁用 |
| 通道左侧来车 | 12 | ego 进入位置区 → 启用从左向右行驶的车辆 → 出口清场 |
| 通道右侧来车 | 12 | ego 进入位置区 → 启用从右向左行驶的车辆 → 出口清场 |
| 行人后连续来往车辆 | 12 | ego 进入位置区 → 行人横穿 → 行人清场并启用左侧来车 → 左侧车辆清场并启用右侧来车 → 清场 |

另有 `in.suite.json`、`out.suite.json`、四个干扰类型分组和 `smoke.suite.json`，共 8 个集合入口。

本组使用现有地图 `ranger_parking_lab_v1` 的 **0.90 m 车位、2.40 m 通道**。行人 0.40×0.40 m、速度 0.65 m/s；交通车辆为与 Ranger 同尺度的 0.72×0.50 m 小车，速度 0.45 m/s，并非全尺寸乘用车。本组不包含窄通道、连续泊入泊出任务或同时交叉抢行；这些组合应在基础动态制动能力验证后扩展。

## 触发器实现

所有演员初始 `enabled: false`。第一触发器为 `TRIGGER_TYPE_LOCATION`，`targetAgentId: ego`，半径 0.06 m；进入区域后执行 `ACTION_ENABLE`。位置来自已通过静态泊车的实际录包轨迹，不使用固定墙钟时间，因此首次规划慢不会让演员提前出现。评测要求第一次出现动态干扰时，ego 已离开起点至少 0.15 m 且速度不低于 0.05 m/s。

演员终点也使用 `TRIGGER_TYPE_LOCATION`，执行 `ACTION_DISABLE`；组合场景在同一触发器中启用下一名演员。车辆沿折线运动的实现会在离终点约 0.30 m 时停下，因此车辆清场区半径取 0.35 m，行人取 0.03 m。不能把车辆清场区设为 0.03 m，否则后续触发链无法启动。

生成器 `parking_trigger_suite.py` 根据已通过的基准任务生成位置区：演员完整车身轨迹位于可行驶区域；每条演员轨迹均与 ego 后续基准轨迹相交；触发参考姿态的车身周围 0.18 m 缓冲区不与演员路径相交，避免生成“贴脸出现且无停车余量”的场景。演员串行运动，避免组合场景中演员互相碰撞。该几何检查不等于经过动力学验证的制动证书。

生成的 `.reference.json` 保存对应基准轨迹；`.evaluation.json` 绑定场景 SHA256。复现命令：

```bash
cd /apollo_workspace
python3 modules/simulation/ml_planning/parking_trigger_suite.py \
  --baseline /apollo_workspace/data/simulation/parking-missions-20261001/full
```

## 评测与复测

沿用原泊入/泊出目标、全车入位、泊正、完成时间、碰撞、越界、规划连续性指标，不把停车等待视为完成。
新增 `quality_metrics.runs[].parking_dynamic`：

- `activation_during_maneuver`：第一个演员确实在 ego 移动后出现并具有运动速度。
- `all_actors_moved`：每名预期演员均出现并运动，包括组合中的后续车辆。
- `all_actors_cleared`：每名演员接近清场位置后从感知中消失。
- 每名演员的首次运动时间、当时 ego 位移及速度。

基于 WorldSim 稳定演员 ID 与约 100 ms 的感知录包推断触发效果，不冒充原生 trigger 事件日志。清场检查考虑清场区半径和采样间隔。原生进程异常退出时也尽量保留部分录包中的触发证据。

`parking-scorecard.json` 的 `dynamic_traffic` 分别统计干扰激活、清场和任务成功。**触发链通过不等于泊车通过**：即使演员都按脚本走完，只要 ego 没有完成原任务，或发布空/无效轨迹，任务仍失败。

```bash
cd /apollo_workspace/modules/simulation/simulator
python3 run_quality_regression.py \
  --state-dir /apollo_workspace/data/simulation/parking-triggers-20261001/new-run \
  --suite /apollo_workspace/modules/simulation/scene_editor/examples/parking_dynamic_triggers_v1/all.suite.json \
  --workers 4
```

基线与修复记录保存在 `data/simulation/parking-triggers-20261001/`：`full/` 为首次清场区未匹配车辆终点行为的记录，`accepted/` 为修复场景触发链后的记录。修复前规划器尚无完整的泊车途中动态制动、让行后恢复机制，遇到这类演员会输出错误/无效轨迹；这些失败应作为能力缺口保留，不能改为安全停车通过。场景生成阶段仅新增触发场景和相应评测；随后的规划器修复见上方链接，V5 权重未改变。

## 2026-10-01 实测结果（修复前基线）

- 修复清场区后，48/48 个场景均在 ego 移动后触发；首次干扰出现于仿真 1.7–4.3 s。
- 72/72 名演员均运动并完成清场；12 个组合场景的行人及两辆车全部依次触发。
- 48 个任务的碰撞检查和车身边界检查均无违规。
- **泊车任务成功 0/48**：现有规划器报 `Moving actor during parking requires dynamic braking support`，继而发布空/无效轨迹，无法恢复完成任务。任务服务按规划连续性失败保留结论；无碰撞不等于正确制动或泊车完成。
- Web Monitor 新建 4 个代表场景，四条完整触发链全部验证通过，四个模型任务失败如实保留。
- 51 项 Python 指标、场景和任务测试通过；本次仅修改 JSON/Python，无需重新编译原生仿真器。

汇总证据：`data/simulation/parking-triggers-20261001/trigger-validation.json`、`accepted/parking-scorecard.json`、`web-final-validation.json`。这是后续动态制动、让行恢复与重新规划的回归基线。
