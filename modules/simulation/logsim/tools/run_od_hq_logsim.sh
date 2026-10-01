#!/usr/bin/env bash
# Launch LogSim on od_hq extracted bag + map.
#
# Usage (inside apollo_neo_dev container, cwd = /apollo_workspace):
#   bash modules/simulation/logsim/tools/run_od_hq_logsim.sh
#   bash modules/simulation/logsim/tools/run_od_hq_logsim.sh --smoke          # skip PnC .so
#   bash modules/simulation/logsim/tools/run_od_hq_logsim.sh --skip-ego-env
#   bash modules/simulation/logsim/tools/run_od_hq_logsim.sh --rebuild
#
# From host:
#   docker exec -u wangsheng -w /apollo_workspace apollo_neo_dev_wangsheng \
#     bash modules/simulation/logsim/tools/run_od_hq_logsim.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Resolve Apollo root: prefer /apollo_workspace in container, else repo root.
# Package lives at modules/simulation (preferred) or legacy simulation/.
if [[ -d /apollo_workspace/modules/simulation ]] || [[ -d /apollo_workspace/simulation ]]; then
  APOLLO_ROOT=/apollo_workspace
elif [[ -d "${SCRIPT_DIR}/../../../../cyber" ]]; then
  # …/modules/simulation/logsim/tools → workspace
  APOLLO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
elif [[ -d "${SCRIPT_DIR}/../../../cyber" ]]; then
  # …/simulation/logsim/tools → workspace (legacy)
  APOLLO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
else
  APOLLO_ROOT="$(pwd)"
fi
cd "${APOLLO_ROOT}"

TASK_DIR="${APOLLO_ROOT}/data/bag/data_with_map/logsim_task_od_hq"
# Relative task_dir for --task_dir flag (works when cwd is APOLLO_ROOT)
TASK_DIR_REL="data/bag/data_with_map/logsim_task_od_hq"
LOGSIM_BIN="${LOGSIM_BIN:-/opt/apollo/neo/bin/logsim_main}"

RECORD_REL="data/bag/data_with_map/extracted/20240913144453.record.00014.20240913145854"
MAP_REL="data/bag/data_with_map/extracted/od_hq_map"
VEHICLE_REL="data/bag/data_with_map/extracted/Jiyu_01/modules/common/data/vehicle_param.pb.txt"

SMOKE=0
SKIP_EGO=0
REBUILD=0
EXTRA_ARGS=()

for arg in "$@"; do
  case "${arg}" in
    --smoke)
      SMOKE=1
      EXTRA_ARGS+=(--skip_modules)
      ;;
    --skip-ego-env)
      SKIP_EGO=1
      EXTRA_ARGS+=(--skip_ego_env)
      ;;
    --rebuild)
      REBUILD=1
      ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *)
      EXTRA_ARGS+=("${arg}")
      ;;
  esac
done

echo "=== od_hq LogSim ==="
echo "APOLLO_ROOT=${APOLLO_ROOT}"
echo "TASK_DIR=${TASK_DIR_REL}"
echo "RECORD=${RECORD_REL}"
echo "MAP=${MAP_REL}"
echo "VEHICLE=${VEHICLE_REL}"
echo "SMOKE=${SMOKE} SKIP_EGO=${SKIP_EGO}"

need() {
  local p="$1"
  if [[ ! -e "${APOLLO_ROOT}/${p}" ]]; then
    echo "ERROR: missing ${APOLLO_ROOT}/${p}" >&2
    exit 1
  fi
}

need "${RECORD_REL}"
need "${MAP_REL}/base_map.bin"
need "${VEHICLE_REL}"

if [[ ! -f "${TASK_DIR}/task.pb.txt" ]]; then
  echo "Generating task_dir..."
  python3 simulation/logsim/tools/gen_simulation_task.py \
    --scenario-id od_hq_00014 \
    --task-dir "${TASK_DIR}" \
    --record-path "${RECORD_REL}" \
    --override-runtime-modules PREDICTION,PLANNING,CONTROL \
    --map-dir "${MAP_REL}" \
    --vehicle-config-path "${VEHICLE_REL}"
  # Normalize task_dir field to relative path
  sed -i 's|^task_dir:.*|task_dir: "data/bag/data_with_map/logsim_task_od_hq"|' \
    "${TASK_DIR}/task.pb.txt"
fi

mkdir -p "${TASK_DIR}/output"
mkdir -p "${APOLLO_ROOT}/data/simulation"

if [[ "${REBUILD}" -eq 1 ]]; then
  echo "Rebuilding simulation package..."
  buildtool build -p simulation
fi

if [[ ! -x "${LOGSIM_BIN}" ]]; then
  echo "logsim_main not found at ${LOGSIM_BIN}, building..."
  buildtool build -p simulation
fi

export GLOG_alsologtostderr=1
export GLOG_minloglevel=0
# CYBER_PATH is installed by SimInitializer::SetupCyber from cyber_sim.pb.conf
# into task_dir/cyber_runtime — do not override here.

# Force neo install paths (do not inherit broken aem envroot paths that may
# not exist inside the container). Align with mainboard resolution.
export APOLLO_DISTRIBUTION_HOME="/opt/apollo/neo"
export APOLLO_PLUGIN_INDEX_PATH="/opt/apollo/neo/share/cyber_plugin_index"
export APOLLO_PLUGIN_DESCRIPTION_PATH="/opt/apollo/neo"
export APOLLO_PLUGIN_LIB_PATH="/opt/apollo/neo/lib"
export APOLLO_LIB_PATH="/opt/apollo/neo/lib"
export APOLLO_DAG_PATH="${APOLLO_ROOT}"
export APOLLO_FLAG_PATH="${APOLLO_ROOT}"
export APOLLO_CONF_PATH="${APOLLO_ROOT}"
export LD_LIBRARY_PATH="/opt/apollo/neo/lib:${LD_LIBRARY_PATH:-}"

echo "ENV: APOLLO_LIB_PATH=${APOLLO_LIB_PATH}"
echo "ENV: APOLLO_PLUGIN_DESCRIPTION_PATH=${APOLLO_PLUGIN_DESCRIPTION_PATH}"
echo "ENV: APOLLO_DAG_PATH=${APOLLO_DAG_PATH}"

echo "Starting: ${LOGSIM_BIN} --task_dir=${TASK_DIR_REL} ${EXTRA_ARGS[*]:-}"
set -x
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  exec env \
    APOLLO_DISTRIBUTION_HOME="${APOLLO_DISTRIBUTION_HOME}" \
    APOLLO_PLUGIN_INDEX_PATH="${APOLLO_PLUGIN_INDEX_PATH}" \
    APOLLO_PLUGIN_DESCRIPTION_PATH="${APOLLO_PLUGIN_DESCRIPTION_PATH}" \
    APOLLO_PLUGIN_LIB_PATH="${APOLLO_PLUGIN_LIB_PATH}" \
    APOLLO_LIB_PATH="${APOLLO_LIB_PATH}" \
    APOLLO_DAG_PATH="${APOLLO_DAG_PATH}" \
    APOLLO_FLAG_PATH="${APOLLO_FLAG_PATH}" \
    APOLLO_CONF_PATH="${APOLLO_CONF_PATH}" \
    LD_LIBRARY_PATH="${LD_LIBRARY_PATH}" \
    "${LOGSIM_BIN}" --task_dir="${TASK_DIR_REL}" "${EXTRA_ARGS[@]}"
else
  exec env \
    APOLLO_DISTRIBUTION_HOME="${APOLLO_DISTRIBUTION_HOME}" \
    APOLLO_PLUGIN_INDEX_PATH="${APOLLO_PLUGIN_INDEX_PATH}" \
    APOLLO_PLUGIN_DESCRIPTION_PATH="${APOLLO_PLUGIN_DESCRIPTION_PATH}" \
    APOLLO_PLUGIN_LIB_PATH="${APOLLO_PLUGIN_LIB_PATH}" \
    APOLLO_LIB_PATH="${APOLLO_LIB_PATH}" \
    APOLLO_DAG_PATH="${APOLLO_DAG_PATH}" \
    APOLLO_FLAG_PATH="${APOLLO_FLAG_PATH}" \
    APOLLO_CONF_PATH="${APOLLO_CONF_PATH}" \
    LD_LIBRARY_PATH="${LD_LIBRARY_PATH}" \
    "${LOGSIM_BIN}" --task_dir="${TASK_DIR_REL}"
fi
