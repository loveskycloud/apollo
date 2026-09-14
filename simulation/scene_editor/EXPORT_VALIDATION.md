# WorldSim export validation — 2026-09-13

## Format issue fixed

`examples/test.scenario.json` is a version 1 `mine-project` editor project, despite its filename. Passing it directly to `apollo.simulation.worldsim.Scenario` caused the unknown `kind` error. Its nested editor scenario also differs from the runtime schema; removing just the envelope or ignoring unknown fields is not a valid conversion.

- Added Project → 导出 WorldSim 场景… with a separate save/download destination. Project Save/Save As retain the editable envelope and their own file handle.
- Browser export, command-line export and bridge use `buildWorldSimScenarioPayload`.
- Preserve unset waypoint heading instead of injecting a zero-radian routing constraint.
- Reject missing/duplicate ego, duplicate agent IDs, unknown types, invalid active routes and nonfinite output numbers.
- Backend preflight keeps strict protobuf parsing and explains how to export when an editor project is selected.
- Converted the user's project to a **new** `examples/test.worldsim.scenario.json`. Original project, geometry, IDs, duration, map and module selections retained.

## Checks run inside apollo_neo_dev_wangsheng

- `npm run test:export`: 4/4 passed (ego/route conversion, all trigger/action mappings, invalid inputs and no silent type coercion).
- `npm run build`: passed; Vite reports the existing large bundle warning.
- `python3 -m unittest test_task_service -v`: 10/10 passed.
- Exported user scene: installed Apollo protobuf parser (`ignore_unknown_fields=False`) and runtime preflight passed.
- Browser visual verification was not performed: no enabled browser surface was available in this session.

## Actual simulation (not an all-healthy algorithm claim)

Task `efc47963c76240d1` was submitted through the running Web Monitor `/api/sim`. It copies failed task `578fbcb5ccd04280` configuration, changing only its source to the exported runtime file:

- map `1haolou_202608241047qh`, profile `ranger_mini_v3`;
- Prediction, Planning, Control, Routing; `perfect_planning`, 10 ms step, seed 1;
- 60-second scenario, two repetitions, both completed and recorded;
- 19,809 ordered messages per repetition, algorithm determinism PASS (wall-time profiling excluded, map ordering canonicalized). Raw bytes differ in 600 messages; raw differences are retained in the analysis.

**Remaining runtime problem:** first Planning error occurs at simulation timestamp 9.3 s (8.3 s after the 1.0 s start). Of 601 Planning messages, 95 are healthy and 506 report code 6000 (`planner failed to make a driving plan`). Control has 940 healthy messages and 5,061 code 1002 messages (no Planning trajectory, including startup). Ego displacement is 3.6422 m. The runtime log reports path optimization and path-bound failures. This requires separate algorithm/map/profile diagnosis; the export fix does not claim to solve it or suppress those errors.

Evidence in the container:

```text
/apollo_workspace/data/simulation/jobs/efc47963c76240d1/analysis.json
/apollo_workspace/data/simulation/jobs/efc47963c76240d1/manifest.json
/apollo_workspace/data/simulation/jobs/efc47963c76240d1/run-1/runtime.log
/apollo_workspace/data/simulation/jobs/efc47963c76240d1/run-1/simulation.record.00000.20260913114305
```

The completed task can be replayed from Web Monitor's simulation task list.
