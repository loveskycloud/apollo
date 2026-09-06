#!/usr/bin/env bash
# Build Apollo web_monitor Rerun viewer (0.37.1 + AD UI) with cargo + web viewer assets.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RERUN_SRC="${ROOT}/rerun"
OUT_DIR="${ROOT}/bin"
JOBS="${JOBS:-$(nproc)}"

if [[ -f "${HOME}/.cargo/env" ]]; then
  # shellcheck disable=SC1091
  source "${HOME}/.cargo/env"
fi

if ! command -v cargo >/dev/null 2>&1; then
  echo "cargo not found. Install Rust toolchain first (rustup)." >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"
cd "${RERUN_SRC}"

echo "[web_monitor] Ensuring wasm32 target..."
rustup target add wasm32-unknown-unknown

echo "[web_monitor] Building web viewer wasm/js assets..."
# MCAP/image/video importers come from re_viewer/re_data_source Cargo features
# (workspace disables re_importer default-features; see docs P23).
# --release requires wasm-opt (binaryen). Fall back to --debug assets if missing.
if command -v wasm-opt >/dev/null 2>&1; then
  cargo run -p re_dev_tools --release -- build-web-viewer --release -g
else
  echo "[web_monitor] wasm-opt not found; building debug web viewer assets"
  cargo run -p re_dev_tools --release -- build-web-viewer --debug
fi

echo "[web_monitor] Building rerun-cli (release, web_viewer)..."
cargo build -p rerun-cli --release --no-default-features --features "native_viewer,web_viewer" -j "${JOBS}"

install -m 755 "${RERUN_SRC}/target/release/rerun" "${OUT_DIR}/rerun"
echo "[web_monitor] Installed ${OUT_DIR}/rerun"
"${OUT_DIR}/rerun" --version || true
