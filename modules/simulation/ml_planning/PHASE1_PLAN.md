# 第一期：使用感知结果学习会车与 nudge

## 范围与数据

用户确认的顺序：第一期感知结果；第二期点云；第三期结合图像。
本期只读取定位、地图/路线、车辆状态及 PerceptionObstacles 的位置、朝向、尺寸、速度。
不订阅点云/图像，不向策略泄露场景名称、演员脚本或未来真值轨迹。
目标车辆为 Ranger Mini V3，最高 1 m/s，保持原车身尺寸和道路边界。

强化学习需要交互样本，但不要求先购买、下载或人工标注外部数据集。
本期由随机仿真环境产生训练样本，用独立 WorldSim 场景验收。
有高质量遥控/人工驾驶记录后再增加行为克隆，不将当前失败模型的输出直接视作专家标签。

## 实施顺序

1. **可测量的基线**：固定随机种子，冻结 V4 权重；分别统计会车、nudge、组合场景和旧任务。
   保存每个真实仿真录包、碰撞、道路边界、规划连续性、到达时间、最小间距及平顺性指标。
2. **多目标策略**：保留旧 16 维输入，添加 4 个目标共 32 维输入；按纵向距离/接近速度排序。
   使用位置、尺寸、速度及相对朝向，不使用传感器原始数据。四个槽之外的目标仍参加全部几何安全检查。
   新格式 MLP_V3/48 维，与旧 MLP_V2/16 维同时支持；新增输入权重从零开始，可精确继承 V4 actor。
3. **专项训练**：25% 旧混合交通、25% nudge、30% 对向会车、20% 会车+nudge；随机宽度、速度、位置和弯曲程度。
   加入加速度、jerk 和侧向动作变化软惩罚，保留强碰撞/越界惩罚。
   会车奖励允许提前准备侧向空间；这属于训练设计，不能当作运行时已学会的证据。
   `--runtime-nudge` 将现有静态障碍附近 0.20 m/s 限速纳入训练动力学，并加入多目标接近风险软惩罚。
   可用 `--anchor-weights` 约束无对向车状态下的新旧策略差异，但是否保留旧能力必须看回归，不能只看约束损失。
4. **独立验收和版本选择**：用未参与梯度训练的固定种子做确定性 actor 比较，再用原生 WorldSim 测完整规划器。
   只有安全和任务完成不退化、目标能力及质量改善的候选才可部署。候选及失败结果全部保留。
   动态场景额外检验规划末端的制动及停车位置，避免安全前缀把车辆带入对向车必经的狭窄位置。
   既有安全候选允许完全停车，并在碰撞约束下评估既有平滑轨迹，以恢复等待后的 nudge。
   延续已验证轨迹时同时保留其制动路径，不能只用新的网络动作补齐末端后把安全停止方案丢弃。
   等待候选仅在没有安全运动候选时参与，避免静态障碍前无限停车；平滑候选横向范围覆盖完整车身需要的避让距离。
   直路静态避障的平滑候选同样遵守既有近距离侧移时机，不因规划视野内存在远处静态物体就立刻偏离中心线。
   接近终点的回正服从“车尾完全超过障碍后再回正”，不能覆盖会车/超车过程中的车身间距约束。
   这些是显式轨迹约束/求解器改进，必须与网络训练的收益分别记录；匀速外推仍不能预测对方意图。
5. **后续增量**：基于失败证据增加窄路等待/让行、遮挡与漏检、感知延迟/噪声、目标历史和更多复杂地图。
   当前 perfect_planning 直接跟随规划轨迹；真实 Control 闭环、标定车辆模型及轨迹优化器需另设验证里程碑，不能用本期结果代替实车验证。

## 验收约定

- 安全硬门槛：原生真值零碰撞、实际车身不越界、零 estop/空轨迹/发布中断；沿用静态物体 0.05 m 和其他物体 0.12 m 规划余量。
- 可通行场景：在时限内到达目标 0.4 m 内且速度低于 0.05 m/s；安全停车但不通过不算完成。
- 阻塞/必须等待场景需明确单独的预期；不能为不可通行场景强制奖励穿过障碍物。
- 质量报告：实际速度、加速度、jerk（注明采样周期）、侧向偏移、曲率变化、回正、耗时及安全层介入率。
  jerk 初期先做与 V4 的同场景比较，不把任意阈值伪装成已标定的乘坐舒适标准。
- 独立脚本与 Web Monitor 使用同一行驶质量检查。空路居中统计考虑所有感知目标的未截断来车 TTC：8 s 规划 + 最多 1 s 制动内的会车准备不属于空路。
  空路偏移 0.15 m 和 10 s 内 3 次显著横摆失败的界面阈值保持不变；很远的来车和静态远处障碍仍不能豁免偏移。
- 原始 actor 与完整规划器分别报告；几何候选带来的成功不能归因于网络学习。
- 替身环境、理想轨迹跟踪、车辆 Control 闭环三个层级分别记录，不互相替代。

## 可复现入口

在 Apollo 容器 `/apollo_workspace` 内使用本模块 `.venv/bin/python` 训练。
`train.py --stage interaction --runtime-nudge --init-weights models/v4/unified.weights --steps 1000000 --envs 64 --seed 20261001 --output <新目录>`。
所有相对模型路径均相对于命令工作目录；建议进入 `modules/simulation/ml_planning` 执行。
训练输出 `interaction.weights`、`interaction.pt`、带源代码/输入权重哈希的 `interaction.training.json`。
`--resume` 用于同维度训练检查点；`--init-weights` 只继承 actor，critic 和优化器重新初始化。

`phase1_eval.py` 比较未训练种子的原始 actor；`phase1_native.py` 生成并比较真实地图上的原生场景。
实验脚本不覆盖默认 `conf/ml_planning.pb.txt`；验收后才显式选择版本。部署决定及本轮结果见 `PHASE1_RESULTS.md`。

原生验证使用容器系统 `python3`（需 `/opt/apollo/neo/python` 的 protobuf），训练使用本模块 `.venv/bin/python`（PyTorch）。
例如：

```bash
cd /apollo_workspace/modules/simulation/ml_planning
OPENBLAS_NUM_THREADS=1 PYTHONPATH=/opt/apollo/neo/python python3 phase1_native.py \
  --scenes ../scene_editor/examples/phase1_interaction \
  --weights /绝对路径/interaction.weights --out /绝对路径/新验证目录 --workers 3
OPENBLAS_NUM_THREADS=1 .venv/bin/python phase1_eval.py \
  --weights models/v4/unified.weights --weights /绝对路径/interaction.weights \
  --runtime-nudge --seed 20261021 --episodes 128 --out /绝对路径/对比结果.json
```

Web Monitor 可选择 `phase1_interaction/phase1-interaction.suite.json`，地图为 `1haolou_202608241047qh`，车辆使用 Ranger，时限设为 90 s。
固定场景用于发现并定位缺陷；用于选择候选的种子是验证集，最终测试必须使用另外的种子/场景，避免反复挑模型污染测试集。
