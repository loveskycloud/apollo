# 已归档：V1 合成直线/分模型演示

当前版本见 README.md；以下历史数据不是当前版本的验收结果。

# ML Planning：PPO + WorldSim

本目录实现一个可复现的低速强化学习规划起点：先学习直线行驶，再学习绕过静态障碍物并回到中心线。使用标准 Apollo HDMap、感知障碍物、定位和 `ADCTrajectory`，保留原有 WorldSim 引擎。

**范围**：单条直线车道、最大 3 m/s、`perfect_planning` 主车模型。示例地图是按 Apollo HDMap protobuf 生成的 80 m × 8 m 合成道路，车辆为 2 m × 1.2 m 的演示模型；不是园区实测地图，也不是实车验证。

## 为什么用 PPO

L4 没有统一的“标准 RL 算法”。公开研究中的 PPO/SAC 是常见基线；工程路线通常还包括模仿学习初始化、闭环训练和约束验证。表格 Q-learning 可以完成少量离散状态/动作的演示，但不适合作为连续轨迹、多障碍物扩展的主线。本项目采用 PPO，之后可以替换 actor 或接入 SAC。

- [ApolloRL](https://arxiv.org/abs/2201.12609)：驾驶仿真中的 PPO/SAC 基线。
- [PPO 原论文](https://arxiv.org/abs/1707.06347)：clipped policy objective。
- [Waymax](https://github.com/waymo-research/waymax)：基于对象、地图、运动状态的加速行为仿真。

这里的策略优化的是定义的累计奖励，**不保证全局最优轨迹**。策略产生横向位置目标和速度目标，受限的运动模型展开 4 s 轨迹，碰撞/道路边界检查失败则尝试制动；制动仍不安全会输出 estop，评估判失败。

## 构建（Apollo 容器内）

```bash
cd /apollo_workspace
buildtool build -p simulation --cpu -j 6
```

保留 `simulation/cyberfile.xml`。当前 buildtool 遇到父包就停止向下扫描，因此使用父包路径；`ml_planning/BUILD` 是其 Bazel 子包，定义 `apollo_component`、测试、DAG 和模型资源安装。**不要直接使用 `-p simulation/ml_planning`**：当前 buildtool 找不到这个嵌套包时会退回全工作区构建。

组件安装路径：`/opt/apollo/neo/lib/simulation/ml_planning/libml_planning.so`。没有直接 g++ 构建步骤。安装规则不复制 `.venv` 和 `runs`。

## 在 Web Monitor 中勾选并回放

打开 Web Monitor → **Sim → Simulation Config → WorldSim / JSON**：

1. 点击 **ML straight example**（直行）或 **ML avoidance example**（避障），自动填入配套场景、地图、车辆并勾选 **ROUTING + ML_PLANNING**。
2. **ML policy** 选择 `Straight` 或 `Obstacle avoidance`。保持 `perfect_planning`；ML 与原 `PLANNING` 互斥，直接消费感知，不要求 `PREDICTION`，当前不接 `CONTROL`。
3. 点击 **Start simulation**，进入 **Simulation Tasks**。完成后点击 **Replay bag / run 1**，自动进入 Planning 布局，查看地图、障碍物、车辆与规划轨迹；用播放条播放或跳转。
4. 历史任务的 **View config** 可以恢复策略与全部配置，修改后再次启动会创建新任务。`Runs = 2` 可检查两次输出的精确一致性。

这两套示例使用任务私有地图/车辆配置，不切换当前工作区的车辆 profile。任务会冻结 actor 权重和 DAG；缺少已构建的组件或有效权重会明确报错。UI 使用现有 FIFO 队列；下方命令行运行器提供多实例并行。

修改页面后，在容器内先执行 `JOBS=6 bash simulation/web_monitor/scripts/build_viewer.sh`，再使用上面的 `buildtool build` 打包；Rerun 页面沿用项目已有的 Cargo/Wasm 构建链。

## 训练：先直行，再避障

训练使用独立 Python 环境，避免改动 Apollo protobuf 等运行时依赖：

```bash
cd /apollo_workspace
virtualenv simulation/ml_planning/.venv
simulation/ml_planning/.venv/bin/pip install -r simulation/ml_planning/requirements.txt --index-url https://download.pytorch.org/whl/cpu
simulation/ml_planning/.venv/bin/python simulation/ml_planning/train.py \
  --stage straight --steps 300000 --envs 64 --output simulation/ml_planning/models
simulation/ml_planning/.venv/bin/python simulation/ml_planning/train.py \
  --stage avoid --steps 2500000 --envs 64 \
  --resume simulation/ml_planning/models/straight.pt --output simulation/ml_planning/models
```

`env.py` 是 NumPy 向量化 Frenet **简化训练环境，不是 WorldSim**。64 个独立状态并行采样；`train.py` 使用 PPO、GAE、Gaussian policy、value baseline。训练奖励包括前进量、终点、碰撞、越界、偏离中心线和近障碍惩罚。训练随机化初始横向偏移、朝向、障碍物纵横位置，并混入无障碍场景。

`.pt` 保存训练参数，`.weights` 导出 actor 的 8→64→64→2 MLP，C++ 使用同一权重推理，不需要 Python 或 libtorch。训练日志 `.training.json` 明确记录环境、种子和采样数。训练高成功率不能替代 WorldSim 验收。

## 运行真实 WorldSim 多实例

使用 Apollo 系统 Python 和生成的 protobuf：

```bash
cd /apollo_workspace
export PYTHONPATH=/opt/apollo/neo/python:${PYTHONPATH}
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
python3 simulation/ml_planning/fixtures.py
python3 simulation/ml_planning/run_parallel.py --stage straight --instances 1 --workers 1
python3 simulation/ml_planning/run_parallel.py --stage both --instances 4 --workers 4
```

每个实例是独立的 `simulator_main` 进程，隔离 task、Cyber 配置、地图/车辆配置引用、策略快照、日志、进度文件和 Cyber record。各自使用进程内同步通信与虚拟时间，不依靠 wall-clock sleep 推进；共享输入地图只读。此运行器不切换 `profiles/current` 或写全局 flagfile，也不改变 Web Monitor 的 FIFO 队列。

`--workers` 控制并发上限，`--instances` 控制总任务数，`--timeout` 为单实例超时。`--policy /path/to/model.weights` 可替换策略。任务生成后，可在设置 `ML_PLANNING_WEIGHTS` 的环境下，用标准 `simulator_main --task_dir=...` 单独重跑。

`runs/<时间>/summary.json` 汇总结果；各实例中有：

- `task.pb.txt` / DAG / `scenario.json` / `policy.weights`：本次运行输入。
- `simulation.record.*`：感知、定位、规划等标准 Cyber 数据，可交给 Web Monitor 回放。
- `policy.csv`：每次决策与安全制动次数。
- `result.json` / `trajectory.svg`：独立读取实际 record 得到的指标与路径图。

评估检查真实车身矩形碰撞、越界、到达终点、规划频率、有限数值、曲率/加速度范围及避障后前进量。训练奖励与 WorldSim 验收分开。

## 接入算法的位置

```text
WorldSim → localization/pose ───────┐
         → perception/obstacles ───┤
Apollo HDMap + routing destination ┤
                                  ▼
  component.cc → Observe → PPO actor → 4 s rollout → constraint check
                                  │
                                  ▼
                     /apollo/planning (ADCTrajectory)
                                  │
                                  ▼
                       WorldSim perfect_planning
```

感知以 10 Hz 触发 `MLPlanning::Proc`；定位和路线由 reader 缓存；地图通过 `HDMapUtil` 读取，主车/障碍物投影到车道 Frenet 坐标。DAG 替换原 Planning 模块，直接消费感知，因此此演示只启动 Routing + ML Planning，无需原 Prediction / Planning 插件。

8 维观察：横向偏移、航向误差、车速、最近障碍物纵向距离、相对横向距离、障碍物有效标记、车道半宽、距终点距离。前馈网络当前只编码最近障碍物，约束检查覆盖所有感知障碍物；障碍物尺寸/速度尚未进入 actor。4 s rollout 按策略逐步推理并填充 x/y/theta/kappa/v/a/relative_time。

替换策略可以改 `Policy::Infer` 及 Python actor，但需同步观察/归一化/动作定义。轨迹展开动力学在 `env.py:dynamics` 与 `planner.h:Step` 中保持一致。生产扩展需要加入多障碍物历史/预测、曲线车道/路网参考线、车辆动力学和舒适性约束。

## 并行与下一阶段

目前包含两种不同的并行：向量化简化环境用于 PPO 训练；多进程真实 WorldSim 用于闭环验收和批量评估。**尚未实现从真实 WorldSim 逐步采样直接训练 PPO 的 reset/step 桥接**。下一步应先建立这个同步桥接，使用多进程采集 rollout，再做真实 WorldSim 微调；不要把批量评估称为已完成在线训练。

建议课程顺序：直行 → 静态单障碍物 → 多障碍物/动态横穿 → 真实地图与 Control 闭环。每一步分别观察到达率、碰撞率、间距、越界、舒适性和安全制动次数，而不仅看累计奖励。

## 本次验证（2026-09-28）

容器 `apollo_neo_dev_wangsheng` 中 `buildtool build -p simulation --cpu -j 6` 成功，6 项 `planner_test` 单元测试通过。训练使用 CPU PyTorch 2.14.0、NumPy 2.2.6、seed=7，直行 303,104 steps，避障 2,506,752 steps。

真实 WorldSim 验收：3 个直行任务 + 3 个不同横向位置的静态障碍物任务，各运行 30 s；串行和四并发均 6/6 通过。

| 场景 | 终点位置误差 | 最小车身间距 | 碰撞 / 越界 / 安全制动 |
| --- | --- | --- | --- |
| 直行 | 0.370 m | 无障碍物 | 0 / 0 / 0 |
| 障碍物居中 | 0.329 m | 1.079 m | 0 / 0 / 0 |
| 障碍物偏左 0.35 m | 0.291 m | 1.343 m | 0 / 0 / 0 |
| 障碍物偏右 0.35 m | 0.379 m | 0.794 m | 0 / 0 / 0 |

每个任务记录 3,001 帧定位与 301 条规划轨迹。直行最大横向偏差 0.448 m；这是初始学习基线，尚不是高精度循迹效果。

同一组任务的端到端耗时（包括进程启动、录像、评估）：串行 **3.894 s**，四并发 **1.329 s**，本次实测 **2.93×**；实际同时运行的仿真进程数为 4。这是短任务的一次测量，不代表任意并发数都能线性加速。

串行/并行的所有 record 消息顺序、时间戳、原始 protobuf payload 完全一致。基于 1,806 个实际决策点验证导出 actor 与 Python 计算一致，最大动作差约 1.2e-5；动力学步进差小于 1e-4（CSV 只记录 6 位有效数字）。

- [并行结果](runs/parallel-final/summary.json)
- [串并行对比与耗时](runs/parallel-final/benchmark.json)
- [策略和动力学一致性](runs/parallel-final/policy-parity.json)
- [直行轨迹图](runs/parallel-final/instance-000/trajectory.svg)
- [避障轨迹图](runs/parallel-final/instance-001/trajectory.svg)

复核命令（运行环境同上）：

```bash
python3 simulation/ml_planning/compare_runs.py \
  simulation/ml_planning/runs/serial-final simulation/ml_planning/runs/parallel-final
python3 simulation/ml_planning/verify_policy.py simulation/ml_planning/runs/parallel-final
```
