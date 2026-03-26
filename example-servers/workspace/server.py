"""
Cooperage Built-in: Workspace MCP Server

Provides direct read/write/list/delete access to the session's /workspace volume.
Auto-registered and auto-started by the gateway — not intended for manual registration.

Tools:
  workspace_write   — write (or overwrite) a file in /workspace
  workspace_read    — read a file from /workspace
  workspace_list    — list all files in /workspace
  workspace_delete  — delete a file from /workspace
"""

import json
import os
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP

WORKSPACE = Path(os.environ.get("COOPERAGE_WORKSPACE", "/workspace"))
WORKSPACE.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("cooperage-workspace", json_response=True, stateless_http=True)


def _safe_path(filename: str) -> Path:
    """Resolve path and guard against traversal attacks."""
    resolved = (WORKSPACE / filename).resolve()
    if not str(resolved).startswith(str(WORKSPACE.resolve())):
        raise ValueError(f"Path {filename!r} escapes workspace")
    return resolved


@mcp.tool()
def workspace_write(path: str, content: str) -> str:
    """Write content to a file in /workspace. Creates parent directories as needed. Overwrites if the file exists."""
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return json.dumps({"written": path, "bytes": len(content.encode())})


@mcp.tool()
def workspace_read(path: str) -> str:
    """Read a file from /workspace. Returns the file contents as text."""
    p = _safe_path(path)
    if not p.exists():
        raise FileNotFoundError(f"{path!r} not found in workspace")
    return p.read_text(encoding="utf-8")


@mcp.tool()
def workspace_list() -> list[str]:
    """List all files currently in /workspace, recursively."""
    return sorted(str(p.relative_to(WORKSPACE)) for p in WORKSPACE.rglob("*") if p.is_file())


@mcp.tool()
def workspace_delete(path: str) -> str:
    """Delete a file from /workspace."""
    p = _safe_path(path)
    if not p.exists():
        raise FileNotFoundError(f"{path!r} not found in workspace")
    p.unlink()
    return json.dumps({"deleted": path})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=port)
