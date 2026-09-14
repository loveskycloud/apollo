# web_monitor — Apollo simulation visualization (Rerun 0.37.1 fork)

## What this is

- [`rerun/`](rerun/): full Rerun **0.37.1** Viewer source, **first-class product code** in this repo (not a patch/dependency).
- Official welcome / example page is **removed** (AD shell on empty start).
- Left rail **Source / Layout / Panel / Sim**; Layout → **Planning / Perception / Control** (embedded `.rbl`).
- Opens `.rrd` / `.mcap` / `.rbl`; this package owns the **Viewer / UI**.
- Project context for agents: `Project.md`, `Architecture.md`, `Current-Status.md`, `TODO.md`. Details: `docs/RERUN_UPGRADE.md`.

## Algorithm debug tools

The **Layers** tree in the upper-left controls scene visibility, including already
cached data. It groups camera/lidar under sensing, plus localization, planning,
perception and prediction. Parent checkboxes control all children; hover to see
source topics. Lidar uses a yellow-based fixed height gradient. Planning is off
until checked; reopen the original record for the v12 perception/prediction layers.

Open **Panel** for Topic inspector, Signal plot, Value watch, Control dashboard,
Trajectory XY, Planning profile, State transitions and Topic health.
These read-only tools follow paused playback/seeks and live in docked layout panels.
See [usage, query limits and browser verification](docs/DEBUG_PANELS.md).
Planning has four default panels; Control has six. Panels can be added, removed,
dragged into splits/tabs, saved as Custom, and exported as `.rbl`.
See [layout editing, ego GLB, planning trajectory and coordinate requirements](docs/AD_LAYOUTS.md).

## Layouts

```bash
# Generate with a Rerun Python SDK, then migrate encoding to match the Viewer:
python3 simulation/web_monitor/scripts/generate_layouts.py -o layouts
./simulation/web_monitor/bin/rerun rrd migrate layouts/*.rbl
cp simulation/web_monitor/layouts/*.rbl \
  simulation/web_monitor/rerun/crates/viewer/re_viewer/layouts/
# Rebuild required so include_bytes picks up new .rbl
bash simulation/web_monitor/scripts/build_viewer.sh
```

## Build (Apollo container + buildtool)

```bash
# Inside Apollo env (aem / docker):
source "$HOME/.cargo/env"   # rustup 1.96.0 + wasm32-unknown-unknown

# Compile forked Viewer (wasm + CLI → bin/rerun)
bash /apollo_workspace/simulation/web_monitor/scripts/build_viewer.sh

# Package launcher + install layouts/bin via buildtool
cd /apollo_workspace
buildtool build -p simulation --opt
```

Installed artifacts:

- `/opt/apollo/neo/bin/web_monitor_main`
- `/opt/apollo/neo/share/simulation/web_monitor/bin/rerun`
- `/opt/apollo/neo/share/simulation/web_monitor/layouts/*.rbl`

## Run (start yourself)

```bash
# Web viewer (default): gRPC :9876 + HTTP UI :9090
# Browser opens bare http://HOST:9090/ — viewer auto-connects to gRPC proxy.
# No ?url= required; ?grpc_host= / ?grpc_port= override for LB/reverse-proxy setups.
web_monitor_main --recording=/path/to/data.rrd
# Browser: http://localhost:9090/            (auto: rerun+http://localhost:9876/proxy)
# Advanced: http://localhost:9090/?url=rerun+http://other-host:9876/proxy
# LB/proxy: http://public-host:9090/?grpc_host=public-host&grpc_port=9876

# Env overrides (useful for containers / orchestrators):
export WEB_MONITOR_GRPC_HOST=10.0.0.5   # public host the browser uses
export WEB_MONITOR_WEB_PORT=9090        # public HTTP port
export WEB_MONITOR_GRPC_PORT=9876       # public gRPC port

# CLI overrides (take precedence over env):
web_monitor_main --grpc_host=10.0.0.5 --web_port=80 --recording=/path/to/data.rrd

# Native viewer
AD_LAYOUT_DIR=/apollo_workspace/simulation/web_monitor/layouts \
  /apollo_workspace/simulation/web_monitor/bin/rerun /path/to/data.rrd
# Or: web_monitor_main --recording=/path/to/data.rrd --native
```

## Browser graphics requirement

The web viewer renders through WebGL 2 or WebGPU. Use a current Chrome/Edge browser with hardware acceleration enabled (`chrome://settings/system`), then fully restart it after changing the setting.
The HTTP page loading successfully does not prove that the browser can render the Viewer.
For a local service use `http://127.0.0.1:9090/` (not `172.0.0.1`); for a remote/container deployment, use that host's reachable IP or DNS name.
If the page reports that WebGL 2 is unavailable, do not retry the same GPU-disabled browser: use a GPU/WebGL-capable desktop session, or run `web_monitor_main --native` on the host instead.

In the left rail: **Layout** → **Planning** / **Perception** / **Control**.
