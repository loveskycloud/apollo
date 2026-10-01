# WorldSim input adapter

WorldSim and LogSim now enter the same `RunSimulator` runtime. There is no `em`
module launch, wall-clock Tick loop or Dreamview SimPerfectControl in this path.
`worldsim`, `logsim_main`, and `simulator_main` are compatibility executable names
for the same task runner; `task.pb.txt` selects `input_kind: WORLD` or `BAG`.

Use **Web Monitor → Sim** to select a scenario JSON or scenario suite, matching map, vehicle,
profile, planner, modules, ego model and repeat count. Tasks run in a persistent queue
with isolated configuration snapshots; completion includes output inspection and
repeat comparison. Output bags can be opened with **Replay bag**.

The planner selector chooses exactly one of `PLANNING` and `ML_PLANNING`.
ML uses its frozen `models/v2/unified.weights`, Routing, and `perfect_planning`.
Planning requires Prediction or Fake prediction; new WorldSim configurations use
Fake prediction explicitly, while the real Prediction module remains selectable.
The included [Beijing scene suite](../scene_editor/examples/beijing_zongyuan_1haolou/README.md)
contains eight editable scenes on `beijing_zongyuan_1haolou`.
Suite concurrency can be 1, 2, or 3; repeats of a member remain sequential.
Each process has separate configuration, weights, logs, mock clock and recording.
Tasks do not switch `profiles/current` or write the live global flagfile.

For existing task directories (same command for either input):

```bash
cd /apollo_workspace
buildtool build -p simulation --cpu
python3 simulation/logsim/tools/launch_job.py --task-dir /path/to/task --repeat 2 --compare
```

WORLD requires `world_scenario_path`, a nonzero `world_start_ns` (default 1 s),
`step_ms` (UI: 1/2/5/10 ms), map and vehicle config, and Routing + Prediction +
Planning DAGs, or Routing + ML Planning DAGs. `perfect_planning` follows the previous real planning trajectory;
`kinematic_control` additionally requires Control and integrates its acceleration
and steering. This is a forward kinematic model, not a calibrated vehicle plant.
Scenario coordinates/route must match the selected map. JSON unknown fields are
errors, not silently ignored. Task module selection is authoritative over the
editor's legacy `simConfig` field.

Scheduling: integer nanosecond clock; world inputs at fixed steps; input before
algorithm timers at equal timestamps; synchronous Cyber in-process callbacks finish
before the next event. A world step consumes the preceding algorithm output, then
updates actors and publishes chassis/pose/perception. No real wall-clock sleep is
used to advance simulation time. LogSim feeds recorded inputs through this same
scheduler (open loop); WorldSim closes the loop through its ego model.

Repeat analysis preserves raw output bags and raw differences. Algorithm comparison
requires exact message order, record timestamps and protobuf values; protobuf map
ordering is canonicalized and only explicitly listed wall-time profiling fields
are excluded. This is a same-environment check, not a claim of GPU/cross-platform
bitwise determinism. Failed comparisons remain failed tasks with replayable evidence.

`bridge/` remains as historical, unbuilt code from the scene-editor WebSocket
integration. Its old flags (`--worldsim_scenario_path`, `--sim_bridge_ws_port`)
are not supported by the new task binary; use Sim tasks instead.


### 128 场景与物理碰撞结果（2026-09-29）

北京总院一号楼清单包含 128 个不同配置的可编辑场景，整套可并发 1–3 个运行。单清单上限 256，待运行/运行任务合计上限 512。所有成员先校验，再原子入队；失败不跳过剩余成员。

WORLD 每个步长从真实 world 状态取启用的物体，使用车辆实际前后左右边界和物体旋转矩形执行 SAT 接触判断，默认 10 ms。`run-N/collision.json` 保存完成状态、检查帧数、首次接触秒数、actor ID 和接触帧数；collision_count 是发生接触的物体个数。此检查为每步离散检测，不宣称连续时间扫掠。

Sim result 展示 Collision PASS / FAIL / INCOMPLETE / NOT_EVALUATED。任意重复运行碰撞即失败；缺失或未完成检测不算 PASS。LogSim 尚未接入真实 world 碰撞检查，显示 NOT_EVALUATED。ML 另检查 estop、终点 0.4 m、直路偏移（3 m 恢复距离后 0.15 m）和 10 s 内显著横向反向次数。碰撞、质量检查和确定性是独立指标。

ML 默认权重改为 `ml_planning/models/v3/unified.weights`；旧任务仍使用其冻结模型与当时的结果，新检测不会悄悄改写旧任务。原 8 场景按到达终点给出的历史结果不能作为行为质量验收。
