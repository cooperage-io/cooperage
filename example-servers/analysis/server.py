"""
Cooperage Example: Analysis MCP Server

Demonstrates stateful compute using a shared /workspace volume.
Tools:
  run_script    — execute a Python snippet, capture stdout/stderr
  write_file    — write a file to /workspace
  read_file     — read a file from /workspace
  list_workspace — list files in /workspace
"""

import os
import io
import sys
import traceback
import contextlib
from pathlib import Path

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp import types
import uvicorn

WORKSPACE = Path(os.environ.get("COOPERAGE_WORKSPACE", "/workspace"))
WORKSPACE.mkdir(parents=True, exist_ok=True)

app = Server("cooperage-analysis")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="run_script",
            description=(
                "Execute a Python script. The /workspace directory is available "
                "as the 'workspace' variable. Output is captured and returned."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "script": {"type": "string", "description": "Python code to execute"},
                },
                "required": ["script"],
            },
        ),
        types.Tool(
            name="write_file",
            description="Write content to a file in /workspace.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["filename", "content"],
            },
        ),
        types.Tool(
            name="read_file",
            description="Read a file from /workspace.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                },
                "required": ["filename"],
            },
        ),
        types.Tool(
            name="list_workspace",
            description="List all files in /workspace.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "run_script":
        return [types.TextContent(type="text", text=_run_script(arguments["script"]))]

    if name == "write_file":
        path = WORKSPACE / arguments["filename"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments["content"])
        return [types.TextContent(type="text", text=f"Written {path}")]

    if name == "read_file":
        path = WORKSPACE / arguments["filename"]
        if not path.exists():
            return [types.TextContent(type="text", text=f"File not found: {arguments['filename']}")]
        return [types.TextContent(type="text", text=path.read_text())]

    if name == "list_workspace":
        files = sorted(str(p.relative_to(WORKSPACE)) for p in WORKSPACE.rglob("*") if p.is_file())
        return [types.TextContent(type="text", text="\n".join(files) if files else "(empty)")]

    return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


def _run_script(script: str) -> str:
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


# ── Streamable HTTP transport (used by Cooperage gateway via POST /mcp) ────────

session_manager = StreamableHTTPSessionManager(
    app=app,
    json_response=True,  # return plain JSON, not SSE streams
    stateless=True,      # no session state needed between requests
)


class CooperageASGIApp:
    """Minimal ASGI wrapper: handles lifespan + delegates requests to session_manager."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
        else:
            await session_manager.handle_request(scope, receive, send)

    async def _lifespan(self, receive, send):
        async with session_manager.run():
            await receive()  # lifespan.startup
            await send({"type": "lifespan.startup.complete"})
            await receive()  # lifespan.shutdown
            await send({"type": "lifespan.shutdown.complete"})


asgi_app = CooperageASGIApp()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(asgi_app, host="0.0.0.0", port=port)
