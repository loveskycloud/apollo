# Unified simulation tasks

## Product workflow

Web Monitor → **Sim**: choose LogSim/bag or WorldSim/JSON, matching map, vehicle
configuration, optional profile, and algorithm modules. The Sim entry opens a
resizable **docked sidebar**, not a floating window. Like scene_editor, one content
area is switched by fixed **Simulation Config / Simulation Tasks** tabs.
WorldSim additionally
selects an ego model and fixed step. **Runs = 2** performs a repeat check; Runs = 1
is explicitly `not_tested`, never a determinism PASS.

- A successful **Start simulation** submission switches to **Simulation Tasks**.
  Validation/submission errors stay on the configuration page.
- Tasks are grouped top-to-bottom as **Running**, **Queued**, **Finished**. Running
  includes preparation and analysis; queue order stays FIFO; finished history is
  newest first. Failed/cancelled/interrupted tasks are finished, not successful:
  their original status, errors and analysis remain visible.
- Click a task's title to open **Simulation detail** inside the same Tasks panel.
  Status, progress, run count, stage history, errors, analysis and outputs update
  from the live service; **Back to tasks** or the Tasks tab restores the list.
- **View config**, available on both a task card and its detail page, copies that
  task's submitted configuration into the normal **editable** Simulation Config
  form. All selections are restored, including modules/model/step/seed/runs; the
  additional configuration section preserves and edits timeout and bag range.
  Starting creates a new task ID, never modifies or restarts the historical task.
  The form indicates the source task ID. Merely viewing details or switching tabs
  does not overwrite the draft; explicitly selecting View config replaces it.
  Invalid snapshots are rejected before changing any draft field. Unknown extra
  configuration fields are retained and displayed, not silently discarded.
- The task filter matches ID, source, kind or status. Stage history/analysis and
  cancellation remain on each task card; replay is offered only after a task ends,
  so a record still being written cannot be opened as a finished result.

The persistent queue runs up to three suite members concurrently. Single submissions
remain serialized with one another; a suite can explicitly select concurrency 1, 2
or 3. At concurrency 1, members run in manifest order. Each member follows:

`queued → data_preparation → map_update → profile_update → model_update → simulation_start → simulation_running → simulation_end → result_analysis → completed`

Start/running/end repeat for each requested run. Errors and cancellation are terminal
states, not completion. Closing the UI does not cancel a task. The queue is stored in
`data/simulation/jobs` (server override: `SIM_TASK_ROOT`). A restarted service marks
unfinished jobs `interrupted`; it does not silently resume from an unverified checkpoint.

Each task retains source/map/config snapshots, a SHA-256 manifest, per-run logs,
`task.pb.txt`, `progress.json`, output Cyber records and `analysis.json`. Configuration
overlays are in `.runtime` so buildtool cannot confuse them with source packages.
The selected profile is copied into the task's `.runtime` directory. Tasks do not
switch `profiles/current`, create workspace profile links, or write the live global
flagfile. The task-local flagfile uses the frozen map and vehicle and derives
`half_vehicle_width` strictly from `vehicle_param.width / 2`.
Only selected algorithm modules and common configuration are frozen; unrelated
installed packages remain read-only references. Missing selected configuration,
invalid widths, broken links and profile/vehicle mismatches fail explicitly.
No module daemon launch or workspace data-root links occur.
`environment_tools.cc` applies the same derived half-width **before the first map
load**, and module override flagfiles retain it after loading profile flags.
After each module initialization, mismatched map/vehicle/half-width flags fail.
`configuration.json` and `analysis.effective_configuration` retain the exact
workspace and task-local paths and dimensions used by the task.
Selected profile plugins must exist in the installed runtime; missing plugins are
reported before module initialization. In particular the supplied legacy Jiyu_01
profile needs plugins absent from this container (`SpeedSetting`,
`ValetParkingParkScenario`). Explicit workspace defaults were used in integration tests;
the service does **not** automatically substitute them for an incompatible profile.

**Replay bag** opens the actual output through the normal record→MCAP workflow,
switches to the corresponding algorithm layout and enables result spatial layers.
The standard Layers checkboxes remain authoritative afterwards. Output records include
complete Cyber ProtoDesc dependency trees; the viewer does not guess schemas.

## Scene suites and planners

A `worldsim-suite` version 1 manifest contains `name`, `mapId` and a `scenarios`
array of relative WorldSim JSON filenames. Select WorldSim → Scenario suite in
the UI, choose a matching vehicle and planner, then Run scenario suite.
The included [Beijing suite](../../scene_editor/examples/beijing_zongyuan_1haolou/README.md)
contains eight editor projects and exports. Map selection follows the manifest.
The API action is `enqueue_suite`; config includes `suite` and `concurrency` in
addition to the usual WorldSim settings. All members are validated before any
are published to the queue. Each task carries suite identity and member index;
failure does not stop the remaining members, and each has independent Replay.

Choose exactly one of Planning and ML Planning. The ML DAG occupies the existing
Planning runtime slot, publishes `/apollo/planning`, and reads raw world perception.
Its selected weights are copied and fingerprinted per task. ML currently requires
`perfect_planning`; Routing is required for both planners. New WorldSim forms select
Fake prediction explicitly for classical Planning; real Prediction remains available.
An ML emergency stop or terminal error greater than 0.4 m marks a failed task with
recordings retained. Runs compares repetitions per scene and differs from concurrency.

## Runtime contract

All three executable names (`simulator_main`, `logsim_main`, `worldsim`) call
`RunSimulator`. The task's `input_kind` chooses only the input adapter.

- Bag: selected recorded inputs, in original record order, on integer-nanosecond time.
- World: lazy fixed-step input; the previous real planning/control output drives the
  ego model, followed by actor/trigger updates and chassis/pose/perception publication.
- Both: load the same algorithm DAG components, freeze one Cyber mock clock, execute
  in-process callbacks synchronously, then advance to the next event. Equal-time inputs
  precede TimerComponents; timer names/sequence provide stable tie ordering.
- Recurring timers keep one future occurrence per component, not a full-duration heap.
- Planning reference-line background processing and Prediction worker parallelism are
  disabled through task-local flags. Process timeout/cancellation belongs to the
  supervisor, not simulation time. A Linux parent-death signal prevents orphan jobs.
- Output write failures and event failures are not treated as EOF. The fake
  ModuleReplayService/computational-graph path is no longer initialized by the runner.

World uses real Routing on `/apollo/raw_routing_request` →
`/apollo/raw_routing_response`, then converts that result into a PlanningCommand.
No route is fabricated. The map must match the scenario's ENU coordinates.
The kinematic model is forward-only and consumes acceleration/steering, not a guessed
throttle conversion. It has a stationary startup phase of at most one virtual second
while Control becomes ready; missing/unhealthy commands after startup are errors.
Direct ego trigger actions are not implemented and are rejected by task preflight.

## What “consistent” means

Analysis compares ordered channel names, exact nanosecond record timestamps and exact
protobuf algorithm values. It canonicalizes protobuf map order and excludes only the
listed wall-clock profiling fields (planning/control latency statistics). Raw message
hashes, raw differences and the exclusion list remain in the report and original bags.
There is no floating-point tolerance, trajectory replacement, control substitution or
constant-PASS comparator. Runs that differ remain failed, with retained evidence.

### Inspect exact field differences

Open a finished task's **Simulation detail → Determinism — message differences**.
Choose the run pair and click **View JSON**. The displayed/copyable value is a JSON
list, one differing message per page. **Previous / Next** and **Difference #** can
inspect every mismatch, not just the first 20 in the compact historical summary.
**Include complete messages** adds both original messages as ProtoJSON alongside
the field differences. Topic, per-topic frame index, stream index, publish_time,
message_time, field path, presence and both values are retained. Indexes start at
zero; clock strings are nanoseconds; absent header time is null. `message_time`
expresses the record's double `header.timestamp_sec` in nanoseconds, not an extra
clock or independently measured timestamp.

The `differences` API reads the selected task's completed output records only.
It works with historical `analysis.json` files, without editing tasks, rewriting
records, running simulations or substituting a new result. Decoding uses each
record's Cyber ProtoDesc dependency tree and official Protobuf `MessageToDict`
with original field names and no filling of absent fields. Repeated elements are
compared by index, maps by key, absent versus explicit zero are different, and
64-bit integer ProtoJSON values remain strings. The JSON parser enables exact
floating-point round trips; no numerical tolerance is added. Only the existing
listed wall-time profiling exclusions apply. If a byte mismatch has identical
named JSON fields (unknown fields/wire representation), the report explicitly
includes `wire_difference` and both payloads instead of hiding the mismatch.
Missing/corrupt schemas fail explicitly. Message pages contain all field changes,
without truncation; the UI virtualizes lines to keep large trajectories responsive.

API example: `POST /api/sim` with
`{"action":"differences","id":"d3ade1846813436c","comparison":0,"offset":0,"limit":1,"include_messages":true}`.
The response's `differences.diffs` is the JSON list; `has_more` and `next_offset`
describe pagination. Limits are 1–10 messages. CLI equivalent inside the container:

```bash
python3 simulation/logsim/tools/bag_diff.py --left RUN1.record --right RUN2.record --algorithm --details --offset 0 --limit 1 --include-messages --output differences.json
python3 -m unittest discover -s simulation/simulator -p 'test_bag_diff_details.py'
python3 simulation/simulator/verify_determinism_details.py --out /tmp/determinism-evidence
```

For task `d3ade1846813436c`, the first difference is stream index 863, Planning
at 3.600000000 s (2,449 different fields). There are 17,778 differing messages;
the raw hashes and original analysis remain unchanged. This fixes **reporting**,
not the underlying nondeterminism. See
`test-artifacts/determinism-details-20260914/records/` for real-record evidence.
The same task's two runs both change Pose Z from 0.4 to 0 at 9.710000000 s:
Planning's 9.7 s input/initial point remain 0.4, but its output first point is
explicitly zero. The perfect_planning model consumes it on the next 10 ms step.
This independent height-semantic problem is diagnosed, not modified by this change.

The report also contains per-topic counts, algorithm status counts, valid planning
frames and ego displacement. A Planning-selected task with no valid trajectory fails.
These checks do not establish collision safety or algorithm correctness.
`completed` means the simulation and its requested repeat checks completed, not that
every algorithm frame was healthy. The deployed world fixture retains 10 startup
Control status-1002 frames and 291 healthy frames; the bag fixture retains 47 and 252
respectively. Inspect `analysis.json` (`algorithm_status_counts`) and the replay's
debug panels for those actual failures; they are not replaced with fabricated output.

Scope: same installed runtime, input/config snapshots and execution environment.
The selected module list is part of that contract: module initialization loads
process-wide Apollo flags. A comparison with Routing enabled on only one side is
not an equivalent configuration. `world-log-equivalence2/equivalence.json` verifies
all 363 Prediction/Planning/Control messages from WorldSim against replay of its
recorded inputs in LogSim, with the same four modules enabled (exact PASS).
This is **not** a claim of arbitrary profile, GPU or cross-machine bitwise determinism.
Optional internal asynchronous workers/timers in additional algorithm plugins still
need explicit integration and repeat tests before they are supported.

Known stability investigation: `ui5` retained one native bag-process SIGSEGV.
Two GDB runs and twelve subsequent ordinary bag runs did not reproduce it.
Native failure stack logging is now enabled, but the root cause is not confirmed.
Do not describe this as fixed or as a production-wide determinism guarantee.

## Verification commands (inside apollo_neo_dev_wangsheng)

```bash
cd /apollo_workspace
buildtool build -p simulation --cpu -j6
python3 -m unittest discover -s simulation/simulator -p test_task_service.py
python3 simulation/simulator/test_sim_integration.py --help
python3 simulation/simulator/test_supervisor_exit.py --help
python3 simulation/simulator/test_world_log_equivalence.py --help
python3 simulation/web_monitor/scripts/test_sim_tasks_browser.py --help
python3 simulation/web_monitor/scripts/test_sim_tabs_browser.py --help
python3 simulation/web_monitor/scripts/test_sim_config_browser.py --help
```

`test_sim_tabs_browser.py --exercise` checks real browser navigation and creates
two real WorldSim tasks: the first runs three times, the second is cancelled while
queued. It asserts docked geometry, all three sections, exact snapshots, preserved
drafts, terminal status parity and replay. This **UI** test does not certify
determinism: all three runs must finish, and a comparison failure must remain
explicitly `failed` with `analysis.determinism = FAIL` and retained outputs.
`sim_browser_helpers.py` navigates the tabs/filter via mouse/keyboard; diagnostics
are read only. Browser acceptance must omit `--staging-assets` to test deployment.

`test_sim_config_browser.py --exercise` verifies detail/list navigation, exact
editable configuration copies, edited draft retention across tab/detail switches,
live detail updates and two distinct new submissions (unchanged and modified).
It compares submitted configs against the backend and verifies the original task
is unchanged. Runtime outcomes are recorded separately, not relabeled as a
determinism PASS. Omit `--exercise` for a non-submitting UI regression.

Editable-config/detail acceptance (2026-09-14):
`test-artifacts/sim-detail-20260914/submission-test/` verifies two real submissions
and exact backend configuration equality; the original task remains byte-for-byte
equal as JSON. New task `a077df8576544898` retains the known comparison failure;
`6e27e55c51ee473b` completes one run (determinism not tested for a single run).
`deployed/` checks the final 9090 resources without substitution, including editable
copies and narrow layout; `replay-regression/` verifies detail navigation and output
replay with all Control debug panels and chassis HUD. All browser tests pass.
Six state tests and final Wasm/native Clippy pass; the first native check overlapped
Web asset regeneration and failed on missing include files, then passed after the
assets were generated. Both attempts and the final full build log are retained.

2026-09-14: tasks `6ccdc04498334791` and `d4795fd963684f88` completed three executions
but failed exact repeat comparison (the former's first retained difference is at
1.7 s); outputs and analysis remain under each `data/simulation/jobs/<id>`.
This is an unresolved runtime
determinism issue, **not** a successful simulation or a reason to hide the failure.
The sidebar change does not alter task-service scheduling or comparison rules.

Docked UI evidence: `test-artifacts/sim-tabs-20260913/deployed/` contains real
running + queued + finished cards, auto-navigation, read-only snapshots, a cancelled
queued task, narrow layout and replay on the deployed 9090 service. The saved
`finished-test-job.json` deliberately retains `failed` / determinism `FAIL`.
`playbar-regression/` covers the 29-state transport, clock, speed-unit and Source
regression. `deployed-final/` rechecks the final deployed build, config snapshots,
replay, all five Control debug panels and chassis HUD without creating more jobs.
The three `ui::ad_sim::tests` pass; final Wasm/native Clippy and full build succeed
(unrelated pre-existing warnings remain). Logs are saved in the same artifact root.
The staging directory retains the initial test's incorrect expectation
that the three-run fixture must pass determinism; it is not a simulation PASS.

Fixtures are derived from `data/bag/data_with_map/extracted/20240913144453.record.00014.20240913145854`
with its actual od_hq_map and vehicle configuration. The world test uses real pose/route
coordinates plus a declared static actor enabled at 1 s and disabled at 2 s.
Evidence is under `test-artifacts/simulation-20260912/`; failed development iterations
are intentionally retained and must not be mistaken for successful validation.

The unified build is deployed on port 9090. `deployed2/` contains the actual browser
queue/cancel/repeat/replay verification and screenshots. Refresh the browser after
upgrading, then open **Sim** in the left rail. Module selection and matching source,
map and vehicle are required; the supplied legacy profile is not a working default.
`dashboard-regression2/` additionally reopens the user's original control record and
checks chassis-value parity, paused seeks, planar/chase camera following and 28
buffered playback samples on the deployed 9090 build.


### 128 场景与物理碰撞结果（2026-09-29）

北京总院一号楼清单包含 128 个不同配置的可编辑场景，整套可并发 1–3 个运行。单清单上限 256，待运行/运行任务合计上限 512。所有成员先校验，再原子入队；失败不跳过剩余成员。

WORLD 每个步长从真实 world 状态取启用的物体，使用车辆实际前后左右边界和物体旋转矩形执行 SAT 接触判断，默认 10 ms。`run-N/collision.json` 保存完成状态、检查帧数、首次接触秒数、actor ID 和接触帧数；collision_count 是发生接触的物体个数。此检查为每步离散检测，不宣称连续时间扫掠。

Sim result 展示 Collision PASS / FAIL / INCOMPLETE / NOT_EVALUATED。任意重复运行碰撞即失败；缺失或未完成检测不算 PASS。LogSim 尚未接入真实 world 碰撞检查，显示 NOT_EVALUATED。ML 另检查 estop、终点 0.4 m、直路偏移（3 m 恢复距离后 0.15 m）和 10 s 内显著横向反向次数。碰撞、质量检查和确定性是独立指标。

ML 默认权重改为 `ml_planning/models/v3/unified.weights`；旧任务仍使用其冻结模型与当时的结果，新检测不会悄悄改写旧任务。原 8 场景按到达终点给出的历史结果不能作为行为质量验收。


## 场景预期与合理停车

WorldSim 场景可配同名 `.evaluation.json`，例如 `case.worldsim.scenario.json` 对应 `case.evaluation.json`。该文件通过场景内容 SHA-256 绑定，修改场景后必须重新审核，不能继续使用过期判据。配置入队时记录预期，执行时快照冻结并校验。

- `reach_goal`：终点距离不超过 0.4 m。
- `yield_then_proceed`：动态障碍会清空，仍需到达；碰撞与驾驶质量独立判定。
- `safe_stop`：永久静态阻塞，在预先审定的障碍前等待区域内稳定停车至少 5 秒（速度 ≤ 0.05 m/s、位移 ≤ 0.10 m）。停在无关位置或起点不自动通过。

Sim result 显示 Expected、对应状态和审核理由。每次重复均检查；碰撞、规划器 estop、缺失检测和非预期停车仍失败。历史任务判据与结果不会被重新改写。无显式元数据的旧场景保留到达要求。


## 30 路并发与结果分析

场景集并发范围为 1–30，默认 30；每个任务的重复运行仍顺序执行。任务槽覆盖准备、仿真和分析整个生命周期，避免无界堆积分析进程。结果分析由独立 Python 子进程执行，避免多个线程争用同一解释器；取消任务和关闭服务均会终止对应子进程。失败会显示真实错误，并保留 `analysis.log`。

`analysis.json` 的 `analysis_resources` 记录分析进程的 wall_s、cpu_s 和 peak_rss_kib。任务 history 可计算准备、仿真、分析各阶段耗时。非终态状态在内存中立即更新、落盘最多每秒一次；终态始终立即持久化。碰撞、estop、场景预期和确定性比较的判据不变。


## 规划轨迹持续性与无效场景（2026-09-29）

WorldSim 选择任一规划器后，每次重复运行都检查 `planning_continuity`：空轨迹、非有限数值、非递增轨迹时间、estop/not_ready/错误状态、少于两个点、不能覆盖下一个 100 ms 周期，以及发布间隔大于当前固定 100 ms 周期（1 微秒数值容差）均失败。首尾按定位时间检查覆盖，允许一个启动周期；正常停车仍须发布有效零速轨迹。结果保留计数及前 20 条时间/原因，超出部分显示省略数量。Sim result 单独显示 Planning continuity；历史未检查任务显示 NOT_EVALUATED，不能据此认定通过。LogSim 输入频率由录包决定，此固定频率检查仅用于 WorldSim。

场景 `.evaluation.json` 可携带 `validity.status=INVALID` 与原因；提交时明确拒绝，不把无效场景当作算法通过。北京总院有效集合当前为 381 个，清单 `excluded` 保存另外 15 个永久阻塞用例的哈希及几何审核依据。文件和历史结果保留。局部扫掠宽度只是当前安全余量下的保守审核，并非全姿态不可通行证明；`passability-evidence.json` 记录 Lane_65_static_right 的真实无碰撞、连续轨迹到达证据，已纠正其停车误判。

按用户要求不为 30 并发重跑全量：本轮只运行 7 个有效失败用例，1 个纠正预期后通过，6 个仍因不能到达失败，全部碰撞检查及轨迹连续性通过。旧 396 录包只读复核找到 Lane_60_mixed 的 35 帧空轨迹，其失败及根因证据保留，不能因为场景从集合剔除就宣称该规划缺陷已修复。详见 workspace `data/simulation/v4-validation-20260929/FAILED_SCENARIO_REVIEW.md`。
