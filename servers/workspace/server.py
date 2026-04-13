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

import base64
import io
import json
import mimetypes
import os
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP

WORKSPACE = Path(os.environ.get("COOPERAGE_WORKSPACE", "/workspace"))
WORKSPACE.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("cooperage-workspace", json_response=True, stateless_http=True)

_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tiff", ".tif",
    ".pdf", ".zip", ".gz", ".tar", ".bin",
}


def _safe_path(filename: str) -> Path:
    """Resolve path and guard against traversal and symlink attacks."""
    raw = WORKSPACE / filename
    if raw.is_symlink():
        raise ValueError(f"Path {filename!r} is a symlink (not allowed)")
    resolved = raw.resolve()
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
def workspace_read(path: str, max_size: int = 32) -> str:
    """Read a file from /workspace. Text files are returned as-is. Binary files
    (images, PDFs, etc.) are returned as a JSON object:
    {"type":"binary","mime":"image/png","encoding":"base64","data":"..."}
    For images, set max_size (e.g. 256) to resize the longest edge before encoding —
    keeps the result small enough to embed in HTML without hitting size limits."""
    p = _safe_path(path)
    if not p.exists():
        raise FileNotFoundError(f"{path!r} not found in workspace")
    if p.suffix.lower() in _BINARY_EXTENSIONS:
        mime, _ = mimetypes.guess_type(str(p))
        data = p.read_bytes()
        if max_size > 0 and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"}:
            try:
                from PIL import Image as _Image
                img = _Image.open(io.BytesIO(data))
                img.thumbnail((max_size, max_size))
                buf = io.BytesIO()
                img.save(buf, format=img.format or "PNG")
                data = buf.getvalue()
            except Exception:
                pass
        return json.dumps({
            "type": "binary",
            "mime": mime or "application/octet-stream",
            "encoding": "base64",
            "data": base64.b64encode(data).decode("ascii"),
        })
    text = p.read_text(encoding="utf-8")
    if len(text) > 200_000:
        return text[:200_000] + f"\n...[truncated — {len(text)} total chars. Read in chunks or use a script to process.]"
    return text


@mcp.tool()
def workspace_list() -> list[str]:
    """List all files currently in /workspace, recursively."""
    return sorted(str(p.relative_to(WORKSPACE)) for p in WORKSPACE.rglob("*") if p.is_file())


@mcp.tool()
def workspace_write_binary(path: str, data: str) -> str:
    """Write a binary file to /workspace. data must be base64-encoded bytes. Creates parent directories as needed. Overwrites if the file exists."""
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = base64.b64decode(data)
    p.write_bytes(raw)
    return json.dumps({"written": path, "bytes": len(raw)})


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
