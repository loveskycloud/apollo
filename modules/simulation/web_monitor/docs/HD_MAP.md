# HD map playback

## Data source and rendering

semantic-mcap-v17 adds explicit HDMap Driveable area surfaces and 2 cm display
ribbons along the actual ParkingSpace polygon edges.
Parking outlines use `/hdmap/parking_spaces`, available under Layers → map.
The display ribbon does not change the physical bay dimensions or evaluation geometry.
Reopen the original record to generate the v17 cache.

The migration follows `simulation/scene_editor/src/map/loaders/apolloBaseMap*.ts`
and `src/scene3d/mapRoadViz.ts`: Apollo lane boundaries define the asphalt polygon,
white 14 cm boundary ribbons and green 8 cm logical centerlines.
The asphalt is `#2e333d`, boundaries `#ebecee`, centerlines `#6b8f71`.
Centerlines are not physical yellow road markings.
The editor currently disables direction arrows, so playback does not invent them either.

The converter uses Apollo's installed `map_pb2.Map` for strict binary/text/JSON
decoding, and Earcut triangulation (the same algorithm family used by Three's
ShapeGeometry). The tangent-normal ribbon construction is ported from the editor.
Unlike the editor's flat display, explicit map elevations are preserved.
For a 2D map with absent z, the drawing plane is the localization origin's
height, matching the existing 2D planning-curve display convention.
Missing boundaries or invalid geometry fail with the lane ID, rather than drawing
an assumed lane width. Install the pinned `mapbox-earcut` dependency only in the
record-tools `.venv` using `requirements-web-monitor.txt`.

## Coordinates and lifetime

All map coordinates are rebased in float64 using the same first real localization
origin as `/vehicle` and `/planning/trajectory`, before GPU float32 conversion.
Do not copy the editor's separate map-centroid origin into playback.
Map geometry is emitted once into three `webmonitor.MapMesh` channels:

- `/hdmap/road_surface`
- `/hdmap/lane_boundaries`
- `/hdmap/lane_centerlines`

The importer writes static Mesh3D + CoordinateFrame(`wm_map_local`) components.
Maps therefore remain visible before playing and across both-clock seeks; they
do not add a clock. MCAP timestamps are only index placement, with
`static=true,message_time_available=false`; no map generation time is invented.
The existing sparse-predecessor playback planner imports static messages even
when a layer is first enabled far from the beginning; native per-topic coverage
prevents retransmitting already imported data.

All three standard layouts and newly added 3D panels include `/hdmap/**`.
Layers → map offers independent and parent visibility checkboxes, default on.
Hiding a layer changes drawing visibility, not cached data or the playhead.

## Choosing a map

- Sim → Replay automatically uses that job's `map/base_map.bin` or
  `map/base_map.txt` snapshot and validates its SHA-256 against `manifest.json`.
  A different explicit map is rejected. The source map directory can have changed
  since the run without changing playback.
- Ordinary records: Source → **HD map for .record** accepts a server-side Apollo
  map directory, `base_map.bin`, `.txt` or protobuf `.json`. Then use Open path or
  Browse. No arbitrary installed map is guessed when the field is empty.
- Existing MCAP files use their embedded map. Reopen the original `.record` to
  convert pre-v14 caches with a map. The converter CLI also accepts `--map`.
- `POST /api/convert_record` accepts the original plain record path or JSON
  `{"path":"...record...","map":"..."}`. Explicit paths respect
  `WEB_MONITOR_OPEN_ROOTS` and host/workspace alias rewriting.

The v14 cache key includes map content and task manifest content, not just the
record timestamp. Bad paths, parse errors, missing pose origins, and modified
task snapshots are visible conversion errors.

## Verification

`modules/simulation/tools/apollo_record_tools/test_hd_map.py` covers all three input encodings,
snapshot integrity, no implicit map selection, triangulated area, ribbon width,
large-coordinate precision and invalid geometry.
`scripts/test_hd_map_browser.py` clicks actual Sim Replay, checks rendered mesh
vertices against the embedded map and task origin, exercises each map checkbox,
all three layouts, ego/top navigation, paused seeks and playback on both clocks.
It saves screenshots, browser logs and numerical evidence to its `--out` directory.

Deployed 9090 acceptance: `test-artifacts/hdmap-20260913/deployed/`, task
`efcce7f9cf4c49c1`, 58 lanes, 676 road vertices, 1,352 boundary vertices and 1,068
centerline vertices. Eighteen captured states cover initial pause, individual and
parent visibility, camera modes, all layouts, 30/58/3-second absolute seeks and
playback on both clocks. Zero browser page/console errors.
`manual-deployed/` repeats all eighteen checks through Source mouse/keyboard
input with an explicitly selected map and an ordinary record copy outside any
task directory; the observed conversion request confirms both entered paths.
Both results use the deployed assets, without staging overrides.
`latest-clock/` additionally replays task `d897596d6b454fe1`: main vehicle,
planning trajectory, chassis HUD, all five Control debug panels, five paused
seeks per clock and playback on both clocks passed on deployed 9090.
The early `run1` exposed a diagnostic accessor incorrectly reading a mesh as a
single-value component; it was corrected to read the vertex batch. Early staging
runs are not acceptance evidence.

Python: 35 tests passed. Rust `re_mcap --all-features`: 73 passed after fetching
the missing upstream Git LFS attachment fixture and checking its SHA-256
(`cdf1195998265b37ff145c0037f390a42c8314800914b6d89c6f12873448e8e3`).
Native/Wasm Clippy and release build passed with existing warnings.
