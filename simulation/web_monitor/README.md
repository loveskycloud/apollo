# web_monitor — Apollo simulation visualization (Rerun 0.19.1 fork)

## What this is

- [`rerun/`](rerun/): full Rerun **0.19.1** Viewer source, **first-class product code** in this repo (not a patch/dependency).
- Official welcome / example page is **removed** (direct workspace entry).
- Left sidebar **Layout** expands to **Planning / Perception / Control** (embedded `.rbl`).
- C++/Python SDK writes `.rrd`; this package owns the **Viewer / UI**.

## Layouts

```bash
pip install 'rerun-sdk==0.19.1'
python3 simulation/web_monitor/scripts/generate_layouts.py
# copies into layouts/*.rbl and re_viewer/layouts/ for include_bytes
cp simulation/web_monitor/layouts/*.rbl \
  simulation/web_monitor/rerun/crates/viewer/re_viewer/layouts/
```

## Build (Apollo container + buildtool)

```bash
# Inside Apollo env (aem / docker):
source "$HOME/.cargo/env"   # after rustup install once

# Compile forked Viewer (native GUI; add wasm via scripts/build_viewer.sh for --web-viewer)
bash /apollo_workspace/simulation/web_monitor/scripts/build_viewer.sh

# Package launcher + install layouts/bin via buildtool
cd /apollo_workspace
buildtool build -p simulation/web_monitor --opt
```

Installed artifacts:

- `/opt/apollo/neo/bin/web_monitor_main`
- `/opt/apollo/neo/share/simulation/web_monitor/bin/rerun`
- `/opt/apollo/neo/share/simulation/web_monitor/layouts/*.rbl`

## Run (start yourself)

```bash
# Native viewer (recommended until wasm-opt/binaryen is installed for web assets)
AD_LAYOUT_DIR=/apollo_workspace/simulation/web_monitor/layouts \
  /apollo_workspace/simulation/web_monitor/bin/rerun /path/to/data.rrd

# Or launcher
web_monitor_main --recording=/path/to/data.rrd --web_viewer=false
```

In the left sidebar: click **Layout** → choose **Planning** / **Perception** / **Control**.
