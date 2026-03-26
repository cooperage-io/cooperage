"""
Cooperage Built-in: Compute MCP Server

Provides a Python execution environment with access to the session's /workspace volume.
numpy, pandas, scipy, matplotlib, and scikit-learn are pre-installed.
Use `uv pip install <package>` inside a script to add dependencies on the fly.

Tools:
  run_script  — execute a Python script, capture stdout/stderr
"""

import io
import os
import contextlib
import traceback
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP

WORKSPACE = Path(os.environ.get("COOPERAGE_WORKSPACE", "/workspace"))
WORKSPACE.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("cooperage-compute", json_response=True, stateless_http=True)


@mcp.tool()
def run_script(script: str) -> str:
    """Execute a Python script. The /workspace directory is available as the
    'workspace' variable. stdout and stderr are captured and returned.

    numpy, pandas, scipy, matplotlib, and scikit-learn are pre-installed.
    To install additional packages, include this at the top of your script:
        import subprocess; subprocess.run(['uv', 'pip', 'install', '<package>'], check=True)
    """
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    local_vars = {"workspace": WORKSPACE}
    try:
        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            exec(compile(script, "<cooperage-script>", "exec"), local_vars)  # noqa: S102
    except Exception:
        stderr_buf.write(traceback.format_exc())

    out = stdout_buf.getvalue()
    err = stderr_buf.getvalue()
    parts = []
    if out:
        parts.append(f"stdout:\n{out}")
    if err:
        parts.append(f"stderr:\n{err}")
    return "\n".join(parts) if parts else "(no output)"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=port)
