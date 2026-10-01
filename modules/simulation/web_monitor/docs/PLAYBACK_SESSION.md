# Indexed playback session ownership

## Failure and root cause

A newly opened browser could display the proxy's cached map/ego/trajectory while
Topic inspector said to open a bag. The chunks were real, but `AdShell`'s skipped
runtime fields (MCAP path, header range, topics and recording owner) were absent.
The previous `clock_ready` diagnostic only meant a timeline existed. Reopening
Replay initialized the fields, masking the missing session lifecycle.

## Source protocol

- `POST /api/mcap_topics` returns `source`: version, resolved path, byte size,
  nanosecond modification time, topics. Integer file identifiers are decimal
  strings, so JavaScript cannot round nanoseconds.
- Every indexed window carries the same source identity as a static
  `TextDocument` at `/__web_monitor_session/source`, in the **same recording** as
  its data and ordered completion receipt. This is provenance metadata, not a
  third timeline. Public timelines remain `publish_time` and `message_time`.
- Each page saves its source, exact cursor, selected clock and layer checks in
  `sessionStorage` (`wm_playback_bookmark_v1`). Request state, cache coverage,
  recording IDs and playing state are deliberately not restored as ready.
- Reload validates the saved file identity, fetches its summary, imports the
  cursor window and waits for the actual receipt owner and committed paused
  seek **after layout activation**. A queued seek is not an acknowledgement:
  blueprint activation can replace the time controller. A new browser without
  a bookmark obtains the source from its active
  recording's descriptor. It never queries a server-global last-opened file.
- The shared ready predicate requires source identity, recording-owner match,
  initialized clock and completed initial seek. Debug panels and chassis HUD
  use that same binding; a geometric scene alone is not readiness.
- An explicit new source supersedes recovery, including while record conversion
  is pending. Replaced/unavailable files and invalid bookmarks are visible
  errors, not reasons to silently display/query a different bag.
- Server import/coverage state is keyed by source path. An unchanged file keeps
  its recording ID across client resets; otherwise another page's reload would
  invalidate existing page caches and reset its cursor on its next receipt.
  Reset force-sends the requested bootstrap window even when server coverage
  predates proxy cache eviction. Opening another source does not discard other
  pages' queued streams. Layout retargeting follows application ownership, not
  every client reset of that same source.

Old cached streams produced before this protocol contain no source descriptor.
Their source cannot safely be inferred from geometry. Such recordings display
an explicit missing-descriptor notice; newly opened indexed recordings carry
the descriptor automatically. Deploying the new server replaces its old proxy
cache, without modifying any source bag.

## Regression entry point

Run inside `apollo_neo_dev_wangsheng`, against the deployed server:

```sh
/apollo_workspace/modules/simulation/tools/apollo_record_tools/.venv/bin/python \
  /apollo_workspace/modules/simulation/web_monitor/scripts/test_session_recovery_browser.py \
  --out /apollo_workspace/modules/simulation/web_monitor/test-artifacts/session-recovery-20260913/deployed-final
```

The test opens Sim Replay with actual mouse input **once**, then reloads without
another Replay or source-open action. It checks paused topic fields, chassis
samples, exact clock/cursor, hidden map layers, a fresh independent browser,
different bags in two pages, switched-source reload, first-click playback and
explicit rejection/retry of an injected file-identity mismatch. Screenshots and
read-only viewer diagnostics are retained with the API request trace.

2026-09-13 acceptance: `deployed-final/` contains 15 captured states with real
Inspector fields (including header timestamp/sequence and control values),
chassis samples and screenshots. Source/clock/layer restoration, paused seeks,
stable IDs in two pages, switched-source reload, actual advancing playback,
injected identity mismatch and verified retry all passed against the deployed
assets (no request override). Both page errors and console errors were zero.
The injected-error snapshot has no active playhead by design and records that
diagnostic explicitly; it is not counted as a ready scene.

`clock-regression/` additionally replays task `d897596d6b454fe1`: both canonical
clocks, five paused forward/backward seeks per clock and actual playback (12
samples), all five Control panels, chassis HUD, ego/trajectory and all three
static map meshes passed. No page/console errors.
