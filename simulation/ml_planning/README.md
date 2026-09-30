# ML Planning：一个模型，实时轨迹

在 Web Monitor 选择 **地图、场景、ML_PLANNING**，不再选择直行/避障策略。
当前 Web Monitor 任务通过 `conf/ml_planning.pb.txt` 中唯一的 `model_version: "v4"` 选择 `models/v4/unified.weights`（V3 保留用于对照）。每次感知更新（10 Hz）结合当前障碍物、定位和 PlanningCommand/HDMap，重新推理并生成未来 8 秒轨迹；没有预存轨迹或按场景名切换模型。
权重离线训练，动作/轨迹在线生成，运行中不会重新训练网络。

## 仿真与实车共用的命令接口（2026-09-30）

ML Planning 统一订阅 `/apollo/planning/command`（`PlanningCommand`），从 `lane_follow_command` 读取路线，不再订阅 raw RoutingResponse。实车由 `external_command` 的 `/apollo/external_command/lane_follow` 服务生成该消息；WorldSim 将独立 Routing 的结果转换为同一种消息。ML 组件没有仿真专用路由分支。

- `/apollo/planning/command_status` 返回原任务 `command_id` 和 RUNNING / FINISHED / ERROR；轨迹 `routing_header` 携带当前 PlanningCommand 的 header，与标准 Planning 一致。
- `target_speed` 限制目标速度，受模型现有 1 m/s 上限约束；减速遵守既有动力学，速度不会瞬间跳变。0 表示停车。
- `/apollo/planning/pad` 支持 STOP、RESUME_CRUISE、CLEAR_PLANNING；FOLLOW 保持现有车道跟随。清除任务会制动，恢复指令或重复旧命令不能重新启动已清除任务，必须下发新任务。
- 自定义 `TemporaryStopCommand` 的 STOP / CANCEL 保留当前路线；普通 RESUME 不会解除独立的临时停车。新路线也不会擅自解除尚未取消的停车请求。
- 重复任务不重置路线进度，完成状态保持到收到新任务；FINISHED 要求当前位置距终点不超过 0.4 m 且速度低于 0.05 m/s。
- 无效路线、非有限/负目标速度、不支持的泊车、自定义命令或 Pad 动作会打印 ERROR、反馈失败并阻止继续旧任务，组件保持运行。定位/感知或轨迹校验失败沿用 estop 错误输出，后续有效输入/新任务可以恢复。模型/地图缺失等初始化错误仍拒绝启动。

当前仍只支持前向、不跨车道变道的地图路线；不实现泊车、PathFollow、自定义参考线偏移、任务模式和主动重路由。参考线偏移请求另在 `/apollo/planning/reference_line_offset_command_status` 返回 ERROR。统一消息接口不代表覆盖标准 Planning 的全部行为能力。ActionCommand STOP 的上层状态查询依赖 external_command 自身的场景状态逻辑，本模块不伪造标准规划器场景。

仿真录包包含 PlanningCommand、Pad 和命令状态。原生接口回归工具 `verify_command_interface.py --fixture-job <历史失败任务目录> --out <新输出目录>` 使用真实组件回放命令序列，检查错误后同一进程继续接收任务，且不注入任何 raw 路由消息；`command_fixture_writer` 仅用于生成测试录包。此检查不替代完整场景行为验收。

## 模型版本配置

模型目录保留各个版本，唯一的选择入口是模块的 `conf/ml_planning.pb.txt`：

```text
ml_planning/
  conf/ml_planning.pb.txt    # model_version: "v4"
  models/v3/unified.weights
  models/v4/unified.weights
```

组件按配置文件所在模块目录加载 `models/<model_version>/unified.weights`。没有 `current/latest` 别名，也不读取 `ML_PLANNING_WEIGHTS`；版本不存在、配置无效或权重损坏时启动失败，不自动切换模型。版本名支持 `v4`、`v4-round4` 这类形式，不能填写路径。

实车构建会安装 DAG、默认配置和各版本的 `unified.weights`。构建完成后，在 `/apollo` 运行时只修改 `/apollo/modules/simulation/ml_planning/conf/ml_planning.pb.txt`，再重启组件即可切换版本；不需要重新编译。源码中的配置是下次构建的安装默认值，重新构建后应核对运行配置。实车命令见 [部署说明](VEHICLE_DEPLOYMENT.md)。

Web Monitor 从源码模块配置读取版本，任务启动时冻结配置与对应权重，在 `configuration.json` 的 `ml_planning_model` 中记录版本、路径和 SHA256。已启动任务保持其快照，新启动任务采用更新后的配置。离线训练/实验入口将候选权重冻结到各自任务的 `models/v0-candidate/` 并生成任务配置，不修改全局选择。组件启动日志打印实际版本及权重路径。

2026-09-30 已完成本机编译、配置/快照测试和真实 WorldSim 组件加载检查：在历史失败场景的 2 秒副本中，V3、V4 各产生 21 帧有效轨迹；不存在的 V5 明确启动失败，残留权重环境变量不影响选择。验证记录位于 `data/simulation/model-config-20260930/result.json`。这些检查只验证模型配置链路，不代表失败场景的完整规划验收通过。

## 当前北京总院全图回归（2026-09-29）

地图 `beijing_zongyuan_1haolou` 的同一场景集已扩展至 **396 个场景**，包括原有 128 个变化场景、76 条有向车道覆盖，以及 24 处位置上的静态障碍、交错绕行、超车、连续前车、行人群、连续横穿车辆和混合场景。每个场景同时提供 Scene Editor 工程和 WorldSim 文件。界面默认 30 并发。仿真与结果分析均按任务使用独立进程，碰撞和行为判据不因并发调整而改变。

**验收尚未通过**：V4 正在进行全图回归与窄弯修复。不能将早期 V3 的 128/128 通过或无碰撞停车视为 396 个场景通过。当前证据保存在 workspace `data/simulation/v4-validation-20260929/`；各轮失败、碰撞和录包均保留。

V4 训练增加多人/多车环境，当前权重继承 V3 → V4 round2（4,005,888 步）→ round3（2,007,040 步）→ round4（4,005,888 步）；round1 因确定性评估退化被拒绝；round5 为未部署候选。运行时仍是 PPO 提案与显式几何安全约束组合，平滑候选轨迹也必须通过车身道路边界、碰撞和转弯半径检查，不能把安全约束带来的改善归因于网络本身。

Sim result 的碰撞结果来自 WorldSim 原生真值、每个仿真步的完整车身 OBB 检测。任意一次重复发生接触即失败，报告缺失/未完成也不算通过。ML 轨迹 estop、未满足场景预期（到达目标 0.4 m 内，或永久阻塞时在指定区域稳定停车）、直道远处障碍引起的明显偏移或连续横摆，同样保留失败。

## 最初接入记录（2026-09-29，历史）

新增 [beijing_zongyuan_1haolou 场景集](../scene_editor/examples/beijing_zongyuan_1haolou/README.md)，可在 Web Monitor → WorldSim → Scenario suite 直接选择。Planner 下拉互斥选择 Planning / ML Planning，并发数可设 1–30，默认 30。两个入口共用同一个任务队列，不修改当前 workspace profile 或全局 flags。

三并发与串行的 8 个场景算法消息逐一比较均 PASS。当前模型 7 个到达终点，慢速前车场景误差约 0.488 m，超过 0.4 m 阈值，页面保留失败和录包。此接入不包含重新训练模型。

## 页面使用

1. 打开 Web Monitor → **Sim → Simulation Config → WorldSim / JSON**。
2. Scenario suite 选择 `beijing_zongyuan_1haolou.suite.json`，地图自动匹配 `beijing_zongyuan_1haolou`；也可单独选择其中的场景。
3. Planner 下拉选择 **ML Planning**，Vehicle 选择配套 Ranger。自动启用 Routing、关闭额外 Prediction/Control，并选择 `perfect_planning`。
4. **Start simulation**，完成后 **Replay bag / run 1**。Planning 布局展示地图、车身、障碍物、轨迹 XY、速度/加速度/曲率及原始规划消息。

首次选择场景会填入配套地图，之后仍可手动选择；场景 mapId 与地图不匹配会明确报错。
任务独立冻结地图、车辆、DAG、权重，不修改工作区 profile 或全局 flagfile。
无有效轨迹、estop 或不满足场景行为预期都会显示失败。可通行场景要求到达终点 0.4 m 范围；已审定的永久阻塞场景要求在障碍前等待区稳定停车至少 5 秒，详见 [场景合理性审核](../scene_editor/examples/beijing_zongyuan_1haolou/SCENARIO_AUDIT.md)。历史 View config 可重新编辑场景；旧 `ml_policy` 不再传入新任务。

## WorldSim 场景

使用现有 `modules/map/data/1haolou_202608241047qh`，不修改原地图：

| 场景文件前缀 | 内容 |
|---|---|
| straight_clear / turn_clear | 直行；Lane_45 → Lane_48 → Lane_47 的约 90° 转弯 |
| straight_obstacle / straight_centered | 直道偏侧、居中静态障碍物，5 s 后出现 |
| turn_obstacle / turn_obstacle_right | 转弯后左右两侧障碍物，5 s 后出现 |
| static_slalom | 连续绕过两个分处两侧的静态障碍物 |
| pedestrian_left / pedestrian_right | 主车接近后行人从左右两侧横穿 |
| pedestrian_sudden | 距离更近时突然出现并横穿的行人 |
| crossing_vehicle | 动态车辆横穿 |
| moving_lead | 慢速前车、超车后的终点安全停车 |

动态演员使用 WorldSim 的原生路线、类型及距离/时间触发器；规划器只读感知，不读取场景脚本、触发时间或演员的未来路线。

## 构建与运行（Apollo 容器）

```bash
cd /apollo_workspace
buildtool build -p modules/simulation --cpu -j 6
export PYTHONPATH=/opt/apollo/neo/python:${PYTHONPATH}
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
python3 simulation/ml_planning/fixtures_1haolou.py
python3 simulation/ml_planning/run_1haolou.py --workers 4 --out simulation/ml_planning/runs/my-test
python3 simulation/ml_planning/verify_policy.py simulation/ml_planning/runs/my-test
python3 simulation/ml_planning/verify_realtime.py simulation/ml_planning/runs/my-test
```

保留 `simulation/cyberfile.xml`；ML 是父包下的 Bazel 子包，BUILD 使用 Apollo component/library/test/install 规则。
当前 buildtool 的包发现停在父包，不要使用 `-p simulation/ml_planning`，否则可能退回全工作区构建。
页面修改先用 `JOBS=6 bash modules/simulation/web_monitor/scripts/build_viewer.sh` 编译 Cargo/Wasm，再用 buildtool 打包。

每个并行实例运行独立的真实 simulator_main，隔离配置、权重、日志和 Cyber record。页面场景集支持 1–30 个场景并发，各场景的重复运行仍然顺序执行。
summary/metrics 来自实际录包，独立检查车身矩形碰撞、地图边界、终点误差、转角和动力学限制；动态障碍物按相邻感知采样对齐到每个定位时刻。
verify_realtime 检查同一模型在障碍物出现前的规划消息完全一致，出现后才改变；verify_policy 核对 C++ 和 Python 的导出网络推理一致。
`run_parallel.py` 是同一套统一模型回归的兼容入口，参数同 `run_1haolou.py`，不再接受旧的 `--stage/--policy`。

## 算法

`component.cc` 读取真实 RoutingResponse 的多段车道，`reference_line.h` 对地图折线按弧长平滑并构建连续参考线。
16→64→64→2 PPO actor 输出横向目标与速度目标，输入包含道路宽度、曲率预览、目的地距离、相关障碍物的位置/尺寸/速度。
地图曲率前馈提供转向几何；所有感知障碍物参加候选轨迹的车身碰撞检查。
若原始 actor 轨迹不可行，会在相同动作空间中重新计算候选轨迹，依据可行性、前进量和偏离 actor 的程度选择；无可行轨迹则制动，再不安全则 estop。
终点另检查运动障碍物的长期匀速预测，防止停车后被后方车辆追上。
这属于 PPO 与几何约束结合的规划器，不是端到端实车控制器，不保证全局最优。

## 训练

```bash
simulation/ml_planning/.venv/bin/python simulation/ml_planning/train.py --stage unified --steps 12000000 --envs 128 --output simulation/ml_planning/runs/new-ppo
python3 simulation/ml_planning/finetune_worldsim.py --rounds 3 --population 8 --workers 4 --out simulation/ml_planning/runs/real-training
python3 simulation/ml_planning/stress_worldsim.py --instances 32 --workers 4 --out simulation/ml_planning/runs/heldout-test
```

第一阶段用 NumPy 向量化曲线路段替身训练 PPO + GAE，随机化静态障碍物、运动速度、横穿方向和出现时机；这不是 WorldSim。
居中修订课程加入连续弯道接直道、真实曲率预览、较大的初始横向偏差。奖励使用物理前进距离，避免仅按 Frenet 进度奖励内侧切弯；道路畅通时加强横向偏差惩罚。
第二阶段使用 **真实 WorldSim** 的完整回合奖励进行 CEM 策略搜索，微调导出 actor 的横向增益与速度偏置，保留每个候选的录包及评估。
微调脚本输出 selected.weights，不自动替换线上模型；完成整套回归后再部署。训练奖励或训练成功率不能代替仿真验收。
上述两个训练命令分别演示新 PPO 训练和当前部署 actor 的 WorldSim 微调；若要微调新检查点，可向微调脚本传入 `--weights runs/new-ppo/unified.weights`（使用实际完整路径）。

## 超车 / near-miss 修复（2026-09-29，当前版本）

在居中模型基础上新增 **16,023,552 个 PPO 替身环境训练步**：碰撞惩罚 -200、越界 -100；按实际非对称车身包络计算间距，小于 0.25 m 的当前/未来 1 秒近距离风险分别施加权重 4/2 的平方惩罚；未完全超过时回正的横向距离惩罚权重 200。课程扩充到车长 0.6–1.4 m、不同宽度和速度的前车。

在线规划增加通用几何约束：主车车尾未超过前车车头 0.5 m 时，保持当前超车侧偏移；通过后允许恢复居中。依据实时障碍物尺寸/位置计算，车辆停止后仍有效，不读取场景 ID。`policy.csv` 的 `pass_hold` 标识原始 actor 预测轨迹中触发了这一约束。

原超车场景 `chaoche.json`：最小车身间距 **0.122 → 0.378 m**；near-miss 采样 **456 → 0**；过早回正采样 **179 → 0**。14 个固定场景 + 36 个随机变体全部到达终点、无碰撞/越界/过早回正；4 个不同尺寸/速度的超车变体也均为 0 near miss。8 项 C++ 规划测试、3 项安全奖励测试通过。

near miss 是训练惩罚和独立报告项，不是所有场景均为零的保证；部分突发行人及狭窄道路测试仍出现小于 0.25 m 的间距，原有硬碰撞约束继续生效。验收记录位于 `runs/nearmiss-retrain/`；默认权重和模型来源位于 `models/v2/`。`user_overtake.worldsim.scenario.json` 是用户原始超车输入的冻结副本。

## 居中修复与重新训练（2026-09-29）

用户任务 `2ec4cadb79024256` 暴露了旧 actor 的问题：没有可见障碍物，最后出弯后仍持续向右输出横向目标，直道偏差最大 0.392 m。旧课程仅从接近中心的位置开始、曲率固定，居中惩罚过弱；仅按 Frenet 进度计奖也会鼓励曲线内侧切弯。到达终点/不碰撞不能证明行驶质量合格。

修订上述奖励和课程后，从部署检查点继续训练 **8,011,776 个 PPO 替身环境步**。运行时 C++ 规划逻辑未修改，仍是统一网络在线推理与原有安全约束，不包含针对这条路线的规则或轨迹。

- 将用户场景原样冻结为 `examples/1haolou/user_multibend.worldsim.scenario.json`，原场景文件未修改。
- **13 个固定场景 + 32 个随机变体全部通过**：到达终点、无碰撞/越界/estop，符合新的居中验收。
- 同一路线出弯后直道偏差从 **39.2 cm 降至 1 cm 以下**（perfect_planning 下的实际日志；按新指标统计）。
- 13 条轨迹的 C++/Python actor 推理核对、8 组感知出现前后因果检查通过；2 项居中指标回归测试通过。
- `moving_lead` 在附近仍有相关运动障碍物时允许安全侧移；没有可评估的无障碍直道采样会明确记录 null，不以此证明居中。

该历史版本的权重及训练来源：`models/v2/unified.weights`、`models/v2/unified.training.json`。
复现证据：`runs/centering-retrain/` 下的 `before-centering.json`、`regression/summary.json`、`heldout/summary.json`、`deployment.json`。之前已完成的任务冻结了旧模型；在页面重新启动任务才会使用新模型。
正式页面用用户原配置（含 Prediction）复测任务：`8611d94076334eb5`，两次运行精确一致，独立录包检查通过；见 `user-retest-metrics.json`。容器内 `buildtool build -p modules/simulation --cpu -j 6` 已通过。

## 初版统一模型验收（2026-09-29，历史指标）

统一 actor 累计训练 **20,029,440 个替身环境 PPO 步**，之后完成 **144 个真实 WorldSim 回合**的 CEM 反馈搜索。
当时的训练来源、参数和权重哈希见 `runs/centering-retrain/previous-unified.training.json`；微调前检查点及所有原始回合保留在 `runs/worldsim-finetune/`。

| 独立验收 | 结果 |
|---|---|
| 12 个固定 1haolou 场景 | 全部到达终点，0 碰撞、0 越界、0 estop |
| 32 个未参与训练的随机变体，seed=20260929 | 全部通过；改变障碍物纵横位置、前车速度、行人触发距离及横穿速度 |
| 最终位置误差 | 固定场景最大 0.369 m；随机变体最大 0.391 m，均在 0.4 m 验收范围内 |
| 推理与实时响应 | C++/Python actor 误差 <1e-8；8 组场景在障碍物可见前轨迹消息完全一致、可见后改变 |
| 不可避免的初始重叠 | 仿真明确失败，不以冻结主车伪造完成 |

上述历史指标没有检验出弯/避障后的持续居中，不能作为新版居中能力的证明。当前 evaluator 已加入：无可见相关障碍物且当前/预览曲率均小于 0.03 时，经过 3 m 恢复距离后，行驶中的横向偏差须 ≤0.10 m；没有符合条件的采样会记录 null，不能据此声称验证了居中。

固定场景历史证据：`runs/deployed-final/summary.json`、`policy-parity.json`、`realtime-evidence.json`。
随机变体：`runs/heldout-32/summary.json`，每次运行目录含场景 JSON、权重、策略日志、record 和指标。
页面验收：`../web_monitor/test-artifacts/ml-unified-20260929/`。

当前范围：Ranger Mini V3（0.72 × 0.5 m）、最高 1 m/s、前向跨车道路线、perfect_planning。
已验证场景之外的任意地图、Routing 变道候选、复杂交通规则、Control 闭环及实车运行尚不构成保证。来不及物理制动的突发障碍物应报失败，而不是伪造到达。

旧的双模型合成直线结果归档在 [HISTORY_V1.md](HISTORY_V1.md)。


## V3 行人行为重训与场景集（2026-09-29）

Web Monitor 默认使用 `models/v3/unified.weights`，V2 保留供复现。先用 V2 检查点训练 2,007,040 步，再加入折返、停下/再走训练 4,005,888 步，共 6,012,928 个 CPU PPO 替身环境步。训练日志、检查点、随机种子与源代码哈希保存在 `models/v3/`。训练环境不等于 WorldSim，训练成功率不作真实仿真验收结论。

奖励增加远处障碍物条件下的居中、横向动作变化惩罚与近距离横穿等待。运行时另有明确的几何约束：约 1.5 m 指车头至障碍物轮廓的纵向间距；远处保持中心行驶，横穿时减速并优先等待，稳定障碍物允许评估 nudge。安全候选增加方向变化成本；紧急制动使用回中目标，避免沿抖动的策略继续转向。这些运行时约束不是声称由神经网络独立学得的能力。

评估数据见 workspace `data/simulation/v3-validation-20260929/`：旧回放质量检查、未参与训练的随机种子替身评估、WorldSim 场景结果及碰撞报告。替身评估的原始策略仍会碰撞，部署依靠完整策略与安全约束，不代表任意环境安全保证。

场景集 128 个成员可在 Scene Editor 编辑；Web Monitor 一次提交、并发最多 3。碰撞、ML estop、未到达以及直路偏移/连续横摆均使任务失败，重复运行中任意一次碰撞也失败。碰撞结果直接使用 WorldSim 原生状态，不能用规划器自报的 safe 或最终到达替代。
