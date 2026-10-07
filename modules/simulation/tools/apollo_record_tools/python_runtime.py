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
    distribution = Path(os.environ.get("APOLLO_DISTRIBUTION_HOME", "/opt/apollo/neo"))
    if (distribution / "manifest.json").is_file():
        dependencies = distribution / "lib/python/record_tools"
        if not (dependencies / "runtime.json").is_file():
            raise RuntimeError(f"Missing packaged record-tools dependencies: {dependencies}")
        # Only converter/debug subprocesses use these pinned dependencies.
        # The task service keeps the Python packages supplied by the Apollo image.
        sys.path.insert(0, str(dependencies))
        os.environ["PYTHONPATH"] = str(dependencies) + os.pathsep + os.environ.get("PYTHONPATH", "")
        return
    environment = Path(__file__).resolve().parent / ".venv"
    if Path(sys.prefix).resolve() == environment:
        return
    interpreter = environment / "bin/python"
    if not interpreter.is_file():
        raise RuntimeError(
            f"Missing record-tools Python environment: {environment}. "
            f"Follow {environment.parent / 'README.md'} "
            "(Web Monitor runtime dependencies); create .venv in this tool "
            "directory after moving the tools."
        )
    env = os.environ.copy()
    env["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
    os.execve(str(interpreter), [str(interpreter), *sys.argv], env)
