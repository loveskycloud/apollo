#!/usr/bin/env bash
# Same lifecycle as LogSim: task.pb.txt selects WORLD / BAG and algorithm DAGs.
set -euo pipefail
if [[ "${1:-}" == "--build-only" ]]; then
  exec buildtool build -p simulation --cpu
fi
if [[ "${1:-}" != "--task-dir" || -z "${2:-}" ]]; then
  echo "Usage: bash simulation/worldsim/scripts/start_worldsim.sh --task-dir PATH [--repeat 2 --compare]" >&2
  echo "Or configure and queue a WorldSim JSON task in Web Monitor > Sim." >&2
  exit 2
fi
exec python3 simulation/logsim/tools/launch_job.py "$@"
