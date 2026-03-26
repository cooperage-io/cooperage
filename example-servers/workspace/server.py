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

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp import types
import uvicorn

WORKSPACE = Path(os.environ.get("COOPERAGE_WORKSPACE", "/workspace"))
WORKSPACE.mkdir(parents=True, exist_ok=True)

app = Server("cooperage-workspace")


def _safe_path(filename: str) -> Path:
    """Resolve path and guard against traversal attacks."""
    resolved = (WORKSPACE / filename).resolve()
    if not str(resolved).startswith(str(WORKSPACE.resolve())):
        raise ValueError(f"Path {filename!r} escapes workspace")
    return resolved


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="workspace_write",
            description="Write content to a file in /workspace. Creates parent directories as needed. Overwrites if the file exists.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to /workspace (e.g. 'plan.md', 'results/output.json')"},
                    "content": {"type": "string", "description": "Text content to write"},
                },
                "required": ["path", "content"],
            },
        ),
        types.Tool(
            name="workspace_read",
            description="Read a file from /workspace. Returns the file contents as text.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to /workspace"},
                },
                "required": ["path"],
            },
        ),
        types.Tool(
            name="workspace_list",
            description="List all files currently in /workspace, recursively.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="workspace_delete",
            description="Delete a file from /workspace.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to /workspace"},
                },
                "required": ["path"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "workspace_write":
            p = _safe_path(arguments["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(arguments["content"], encoding="utf-8")
            return [types.TextContent(type="text", text=json.dumps({
                "written": arguments["path"],
                "bytes": len(arguments["content"].encode()),
            }))]

        if name == "workspace_read":
            p = _safe_path(arguments["path"])
            if not p.exists():
                raise FileNotFoundError(f"{arguments['path']!r} not found in workspace")
            return [types.TextContent(type="text", text=p.read_text(encoding="utf-8"))]

        if name == "workspace_list":
            files = sorted(
                str(p.relative_to(WORKSPACE))
                for p in WORKSPACE.rglob("*") if p.is_file()
            )
            return [types.TextContent(type="text", text=json.dumps(files))]

        if name == "workspace_delete":
            p = _safe_path(arguments["path"])
            if not p.exists():
                raise FileNotFoundError(f"{arguments['path']!r} not found in workspace")
            p.unlink()
            return [types.TextContent(type="text", text=json.dumps({"deleted": arguments["path"]}))]

        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [types.TextContent(type="text", text=f"Error: {e}")]


# ── ASGI app ──────────────────────────────────────────────────────────────────

session_manager = StreamableHTTPSessionManager(
    app=app,
    json_response=True,
    stateless=True,
)


class CooperageASGIApp:
    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            async with session_manager.run():
                await receive()
                await send({"type": "lifespan.startup.complete"})
                await receive()
                await send({"type": "lifespan.shutdown.complete"})
        else:
            await session_manager.handle_request(scope, receive, send)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(CooperageASGIApp(), host="0.0.0.0", port=port)
