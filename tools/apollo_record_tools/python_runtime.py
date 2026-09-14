"""Use the record tools' isolated dependencies without changing Apollo's Python.

Called before third-party imports by CLI entry points launched by Web Monitor.
Imports from tests/libraries must use .venv/bin/python directly.
"""
import os
from pathlib import Path
import sys


def ensure_runtime():
    # Apollo's login shell selects its system C++ protobuf extension, which is
    # not installed in this isolated environment. This also applies when the
    # caller already selected .venv/bin/python (no re-exec required).
    os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
    environment = Path(__file__).resolve().parent / ".venv"
    if Path(sys.prefix).resolve() == environment:
        return
    interpreter = environment / "bin/python"
    if not interpreter.is_file():
        raise RuntimeError(
            "Missing record-tools Python environment. Follow "
            "tools/apollo_record_tools/README.md (Web Monitor runtime dependencies)."
        )
    env = os.environ.copy()
    env["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
    os.execve(str(interpreter), [str(interpreter), *sys.argv], env)
