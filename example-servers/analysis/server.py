"""
Cooperage Example: Analysis MCP Server

Demonstrates stateful compute using a shared /workspace volume.
Tools:
  run_script     — execute a Python snippet, capture stdout/stderr
  write_file     — write a file to /workspace
  read_file      — read a file from /workspace

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

mcp = FastMCP("cooperage-analysis", json_response=True, stateless_http=True)


@mcp.tool()
def run_script(script: str) -> str:
    """Execute a Python script. The /workspace directory is available as the
    'workspace' variable. stdout and stderr are captured and returned."""
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


@mcp.tool()
def write_file(filename: str, content: str) -> str:
    """Write content to a file in /workspace."""
    path = WORKSPACE / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return f"Written {filename}"


@mcp.tool()
def read_file(filename: str) -> str:
    """Read a file from /workspace."""
    path = WORKSPACE / filename
    if not path.exists():
        return f"File not found: {filename}"
    return path.read_text()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=port)
