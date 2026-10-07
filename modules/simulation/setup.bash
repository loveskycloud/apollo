# Source from Bash to make simulation apps available in the current terminal.
_simulation_setup() {
  local simulation_dir
  simulation_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || return
  export SIMULATION_ROOT_DIR="${simulation_dir}"
  if [[ ":${PATH:-}:" != *":${simulation_dir}/apps:"* ]]; then
    export PATH="${simulation_dir}/apps${PATH:+:${PATH}}"
  fi
  hash -r
}

_simulation_setup
unset -f _simulation_setup
