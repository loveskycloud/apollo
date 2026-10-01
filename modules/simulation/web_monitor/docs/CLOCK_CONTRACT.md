# Playback clock contract

The only product clock identifiers are `publish_time` and `message_time`.
These exact strings are used by the timeline selector, Rerun data timelines,
debug query API, HUD, plots, Topic inspector and playback tests.
`log_time`, `timestamp`, `message_publish_time` and `message_log_time` are not aliases.
Panels wait for a valid product clock while the recording is initializing.

## Data provenance

- `publish_time`: Apollo record message timestamp, preserved as integer nanoseconds.
  For simulator output this is the virtual-time dispatch timestamp.
  Legacy Cyber recorder fills this field in its receive callback (`cyber/tools/cyber_recorder/recorder.cc`),
  so it is the recorded observation of publication, **not proof of the publisher's exact send time**.
  A missing independently captured send time cannot be reconstructed from an existing bag.
  This limitation is recorded in MCAP conversion metadata; no third product clock is introduced.
- `message_time`: payload generation/measurement timestamp.
  Sensor `measurement_time` / camera `timestamp_sec_start` is used when populated;
  other Apollo messages use `header.timestamp_sec`.
  Raw Topic payload and its derived semantic overlay use the same timestamp.
  Localization indexing for sensor placement uses the same extraction function.
- Missing generation timestamps are **not** replaced with publication timestamps.
  Such raw messages remain available on `publish_time`, on explicitly marked
  `message_time_available=false` MCAP channels.
  Querying an entirely unstamped topic on `message_time` returns a specific
  unavailable-time error; partially stamped topics report the missing-time count.

MCAP's binary schema requires a storage member named `log_time`.
It is an internal index slot, mapped to `message_time`, not a third user-visible clock.
For publish-only messages that required slot uses their publication timestamp,
but its value is never exposed as a generated timestamp (channel metadata controls decoding).
Message payload fields remain unchanged.

## Playback and cache

The clock dropdown pauses at the current absolute nanosecond timestamp and keeps
that timestamp when switching between the two clocks. It never resets to the
first sample or clamps to the currently loaded range. This compares each clock's
latest-at data at the same instant, not at two independently remembered cursors.

The v13 converter invalidates the previous conversion cache by versioned keys.
Original records and old cache files are retained; reopen the original record to reconvert.

Window planning indexes actual `(publish_time, message_time)` pairs, not payload bytes.
A window imports the union needed for both clocks, including sparse-topic latest-at
predecessors and each clock's H.264 keyframe prerequisites.
Publication ranges are mapped through actual pairs to native MCAP index ranges;
no fixed latency or equality assumption is used.
Per-topic coverage deduplicates imports in storage coordinates.
The buffer receipt uses the two existing product clocks, never a third sequence timeline.

## Validation commands (container)

Release builds require Binaryen >= 130. Ubuntu 22.04's Binaryen 105 rewrites
the current wasm-bindgen externref export incorrectly: compilation succeeds,
but startup fails with `WebAssembly.Table.grow(): failed to grow table by 4`.
`scripts/build_viewer.sh` rejects that toolchain instead of installing broken
assets or silently switching to debug assets. Binaryen 130 is installed under
`~/.local/opt/binaryen-version_130` in `apollo_neo_dev_wangsheng`; the build
script selects it explicitly. Distribution: [official Binaryen 130 release](https://github.com/WebAssembly/binaryen/releases/tag/version_130).

```bash
cd /apollo_workspace/modules/simulation/tools/apollo_record_tools
.venv/bin/python -m unittest test_clock_contract test_ad_scene test_dashboard_query test_mcap_debug_query test_mcap_playback_plan

cd /apollo_workspace
modules/simulation/tools/apollo_record_tools/.venv/bin/python simulation/web_monitor/scripts/test_sim_tasks_browser.py \
  --url 'http://127.0.0.1:9090/?persist=false&renderer=webgl&theme=dark' \
  --replay-job efcce7f9cf4c49c1 --clock-checks \
  --out simulation/web_monitor/test-artifacts/clock-contract-20260913/deployed4
```

The browser test checks actual timeline names and HTTP query clocks, paused forward/back
seeks with visible ego data, both-clock playback, HUD/debug panels and screenshots.

## Deployed verification — 2026-09-13

- Rebuilt Wasm and native viewer with Binaryen 130 and restarted the existing
  9090 / 9876 service in `apollo_neo_dev_wangsheng`, after confirming no active
  simulation jobs. No original records or task history were removed.
- Replayed task `efcce7f9cf4c49c1` through the real Sim UI. The original run-1
  record was converted to v13 cache `d2fb3e03283c5c2d.mcap`.
- Chromium / software WebGL browser tests passed twice. Final evidence is in
  `test-artifacts/clock-contract-20260913/deployed4/`:
  `clock-checks.json`, `query-clocks.json`, `replay-state.json`, and screenshots.
  The actual recording contains exactly the two canonical timelines; all 75
  observed debug requests use them (40 publication, 35 message). No page or
  console errors occurred.
- Both clocks passed paused seeks at absolute 2, 30, 58, 6 and 3 seconds
  (record starts at 1 second), including initially uncached distant windows.
  Each of the ten pose sample timestamps equals the requested timestamp.
  Clicking Play advances both clocks and all five Control debug panels remain
  populated; chassis-only HUD and ego/trajectory rendering were verified.
- All 19,809 original messages in the task were decoded and their extracted
  generation timestamps checked. All 13,805 derived MCAP messages match their
  source topic's clock pair. A real 1.5-second slice of the older control bag
  also converts successfully (4,599 input events), including publish-only topics.
- 29 Python regression tests passed. Scoped Rust nextest: 140 passed, 25 skipped
  (unrelated fixture-dependent tests excluded). Native and Wasm Clippy completed
  with existing warnings. This is not a full-workspace test pass or hardware
  playback performance certification.

The task's existing Planning `6000` / Control `1002` algorithm statuses remain
visible in the recorded results; repairing playback does not repair those
algorithm failures or fabricate a valid late planning trajectory.

After deployment, hard-refresh the browser and reopen the task's original
record; already-open pages retain the old Wasm and clock protocol until reload.

## Reopened user report — 2026-09-13 12:56

The user reports that Replay still shows no data and the progress bar does not
move. The isolated browser passes above do **not** establish that the user's
existing browser session works; the incident remains open pending identification
of the affected URL/session. Server logs from 12:53–12:55 show successful first
window imports, but no subsequent prefetch for those attempts. That narrows the
failure to the path after initial import but does not prove a particular cause.

`scripts/test_replay_lifecycle_browser.py` adds a separate reproduction using the
bare URL (persistence enabled), actual mouse clicks only, first-click playback,
run-1 → run-2 → run-1, and page reload followed by replay. These four attempts
passed on the user's restarted service, with playheads advancing from 1 s to
approximately 10 s and matching pose samples. Evidence:
`test-artifacts/replay-stall-20260913/lifecycle1/`. This is additional coverage,
not a resolution of the user's reported failure. No production change was made
during this reproduction attempt.

Follow-up: the screenshot's missing-stream condition was reproduced and fixed.
See [Replay connection diagnosis and deployed verification](REPLAY_CONNECTION.md).
The original clock test now requires a live receiver and acknowledgement for the
active recording before accepting geometry; old cached data is not sufficient.
