# WorldSim input adapter

WorldSim and LogSim now enter the same `RunSimulator` runtime. There is no `em`
module launch, wall-clock Tick loop or Dreamview SimPerfectControl in this path.
`worldsim`, `logsim_main`, and `simulator_main` are compatibility executable names
for the same task runner; `task.pb.txt` selects `input_kind: WORLD` or `BAG`.

Use **Web Monitor → Sim** to select the scenario JSON, matching map, vehicle,
profile, modules, ego model and repeat count. Tasks run in a persistent FIFO queue
with isolated configuration snapshots; completion includes output inspection and
repeat comparison. Output bags can be opened with **Replay bag**.

For existing task directories (same command for either input):

```bash
cd /apollo_workspace
buildtool build -p simulation --cpu
python3 simulation/logsim/tools/launch_job.py --task-dir /path/to/task --repeat 2 --compare
```

WORLD requires `world_scenario_path`, a nonzero `world_start_ns` (default 1 s),
`step_ms` (UI: 1/2/5/10 ms), map and vehicle config, and Routing + Prediction +
Planning DAGs. `perfect_planning` follows the previous real planning trajectory;
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
