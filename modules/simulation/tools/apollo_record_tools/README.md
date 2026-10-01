# Apollo Record Tools

Small offline utilities for Apollo CyberRT `.record` files.

## Build

Run inside the Apollo AEM container:

```bash
cd /apollo_workspace/modules/simulation/tools/apollo_record_tools
bash build.sh
```

## Create A Time Index

```bash
bin/apollo_record_tool index \
  -o /apollo_workspace/data/bag/index.jsonl \
  /apollo_workspace/data/bag/20260514114049.record.00000.20260514114049 \
  /apollo_workspace/data/bag/20260514114049.record.00001.20260514114110
```

This writes:

- `index.jsonl`: one JSON object per message, including timestamp, channel, type, source file and payload size.
- `index.jsonl.summary.json`: duration and per-channel counts.

## Split By Time

Split into 1-second chunks:

```bash
bin/apollo_record_tool split \
  --chunk-ms 1000 \
  --output-dir /apollo_workspace/data/bag/chunks_1s \
  /apollo_workspace/data/bag/20260514114049.record.00000.20260514114049 \
  /apollo_workspace/data/bag/20260514114049.record.00001.20260514114110
```

Split only localization and lidar:

```bash
bin/apollo_record_tool split \
  --chunk-ms 100 \
  --output-dir /apollo_workspace/data/bag/chunks_100ms \
  -c /apollo/localization/pose \
  -c /apollo/sensor/rslidar/up/PointCloud2 \
  /apollo_workspace/data/bag/20260514114049.record.00000.20260514114049
```

The output chunks are still Apollo CyberRT record files, so no data is decoded or lost.

## Extract A Random Time Window

Use a timestamp from `index.jsonl`, then extract a short window around it:

```bash
bin/apollo_record_tool slice \
  --begin-ns 1778730061085202401 \
  --duration-ms 500 \
  -o /apollo_workspace/data/bag/slice.record \
  -c /apollo/localization/pose \
  -c /apollo/sensor/rslidar/up/PointCloud2 \
  /apollo_workspace/data/bag/20260514114049.record.00000.20260514114049 \
  /apollo_workspace/data/bag/20260514114049.record.00001.20260514114110
```

The result is a tiny Apollo record containing only the selected time range and channels.

## Convert A Window To MCAP

Semantic v16 uses one verified vehicle configuration for the ego model and the
physical-width planning ribbon. Simulation recordings use their task's frozen
`vehicle/vehicle_param.pb.txt` and manifest checksum; ordinary recordings use
`--vehicle-config`, `WEB_MONITOR_VEHICLE_CONFIG`, or the workspace
`global_flagfile.txt` vehicle_config_path. Missing or inconsistent dimensions
fail conversion. Changing vehicle configuration invalidates Web Monitor's cache.

The planning ribbon follows the configured left/right edges. Green means zero
deceleration/braking; red intensity is the greater of planned deceleration
normalized by `abs(max_deceleration)` and recorded chassis brake percentage.
Feedback is latest-at the planning message timestamp and expires after 500 ms.
Missing both acceleration and fresh brake feedback is gray, not assumed green.

### Web Monitor runtime dependencies

For semantic conversion, Topic panels, debug queries and playback-window indexing,
install the complete pinned runtime **inside the Apollo container, as the same
user that runs Web Monitor**:

```bash
cd /apollo_workspace/modules/simulation/tools/apollo_record_tools
python3 -m pip install --user virtualenv
python3 -m virtualenv .venv
.venv/bin/python -m pip install -r requirements-web-monitor.txt
.venv/bin/python -m pip check
python3 apollo_record_to_semantic_mcap.py --help
.venv/bin/python -m unittest test_ad_scene test_dashboard_query test_mcap_debug_query test_mcap_playback_plan
```

The four Web Monitor CLI scripts automatically re-execute in this `.venv` before
importing their dependencies. No shell activation or Web Monitor rebuild/restart
is needed. Imported modules/tests should use `.venv/bin/python` directly.
The runtime is intentionally separate from Apollo's system protobuf/grpc tools;
do not upgrade the system protobuf just to obtain `GetMessageClass`. Missing
runtime/dependencies are explicit errors, not silently skipped data.

If the container is recreated, rerun these commands. Do not copy `.venv` between
host/container paths: virtual environments contain absolute interpreter paths.
After moving the tools from `tools/apollo_record_tools` to
`modules/simulation/tools/apollo_record_tools`, rerun the setup in the new
directory as well. Web Monitor selects the new directory, so an environment
left under the old path does not satisfy the runtime dependency check.

Browser acceptance tests also use this directory's interpreter. Install their
optional dependencies here (never reuse an environment from the removed root
`tools` directory):

```bash
/apollo_workspace/modules/simulation/tools/apollo_record_tools/.venv/bin/python -m pip install playwright==1.63.0 Pillow==12.3.0
/apollo_workspace/modules/simulation/tools/apollo_record_tools/.venv/bin/python -m playwright install chromium
```

### Standalone raw converter

Install Python dependencies inside the Apollo container once:

```bash
python3 -m pip install --user mcap mcap-protobuf-support
```

Convert selected channels and time range:

```bash
python3 apollo_record_to_mcap.py \
  -o /apollo_workspace/data/bag/slice_test.mcap \
  --begin-ns 1778730061085202401 \
  --duration-ms 500 \
  -c /apollo/localization/pose \
  -c /apollo/sensor/rslidar/up/PointCloud2 \
  /apollo_workspace/data/bag/20260514114049.record.00000.20260514114049 \
  /apollo_workspace/data/bag/20260514114049.record.00001.20260514114110
```

The MCAP stores Apollo protobuf payloads as-is with the Apollo protobuf descriptors from the original record.

## Export To Rerun

Install Rerun once inside the Apollo container:

```bash
python3 -m pip install --user rerun-sdk
```

Export localization trajectory and lidar point clouds to an `.rrd` file:

```bash
python3 apollo_record_to_rerun.py \
  -o /apollo_workspace/data/bag/slice_test.rrd \
  --begin-ns 1778730061085202401 \
  --duration-ms 3000 \
  --max-points 120000 \
  /apollo_workspace/data/bag/20260514114049.record.00000.20260514114049 \
  /apollo_workspace/data/bag/20260514114049.record.00001.20260514114110
```

Open it with:

```bash
python3 -m rerun /apollo_workspace/data/bag/slice_test.rrd
```

## Notes

This is the first layer for random access. The generated index and chunks can be consumed by a custom viewer, or used as the input for a later MCAP/Rerun conversion step.
