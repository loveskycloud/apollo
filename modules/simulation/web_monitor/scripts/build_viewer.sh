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

# Binaryen 105 (Ubuntu 22.04) corrupts the exported externref table used by
# current wasm-bindgen, causing WebAssembly.Table.grow() to fail at startup.
if [[ -x "${HOME}/.local/opt/binaryen-version_130/bin/wasm-opt" ]]; then
  export PATH="${HOME}/.local/opt/binaryen-version_130/bin:${PATH}"
fi
if ! command -v wasm-opt >/dev/null 2>&1; then
  echo "Binaryen >= 130 is required for the release web viewer." >&2
  exit 1
fi
binaryen_version="$(wasm-opt --version | sed -n 's/.*version \([0-9]*\).*/\1/p')"
if [[ -z "${binaryen_version}" || "${binaryen_version}" -lt 130 ]]; then
  echo "Binaryen >= 130 is required; found $(wasm-opt --version)." >&2
  exit 1
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
cargo run -p re_dev_tools --release -- build-web-viewer --release -g

echo "[web_monitor] Building rerun-cli (release, web_viewer)..."
cargo build -p rerun-cli --release --no-default-features --features "native_viewer,web_viewer" -j "${JOBS}"

install -m 755 "${RERUN_SRC}/target/release/rerun" "${OUT_DIR}/rerun"
echo "[web_monitor] Installed ${OUT_DIR}/rerun"
"${OUT_DIR}/rerun" --version || true
