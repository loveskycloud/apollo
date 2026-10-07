"""Shared workspace imports and exit handling for simulation CLI applications."""
from pathlib import Path
import subprocess
import sys

SIMULATION_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = SIMULATION_ROOT.parent.parent
# CLI scripts may run from any directory. Business modules stay under modules/.
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))


def run_cli(main):
    """Return an exit code; argparse retains its own help/usage exit handling."""
    try:
        return main() or 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"[{Path(sys.argv[0]).stem}] ERROR: {error}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        return 130
