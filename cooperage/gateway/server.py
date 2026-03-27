"""
Cooperage MCP Gateway

Exposes a single MCP server to the LLM. Tools:
  cooperage_list_servers         — list registered MCP server images
  cooperage_pull_server          — pre-pull a server image
  cooperage_create_session       — create a workspace session (shared volume)
  cooperage_list_tools           — list tools exposed by a server
  cooperage_call_tool            — invoke a tool on a server within a session
  cooperage_end_session          — tear down a session and its containers
  cooperage_workspace_write      — write a file directly to the session workspace
  cooperage_workspace_read       — read a file from the session workspace
  cooperage_workspace_list       — list files in the session workspace

All heavy lifting is delegated to the session manager and orchestrator.
"""

import asyncio
import json
import logging
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

import cooperage.registry.registry as registry
import cooperage.session.manager as sessions
from cooperage.orchestrator import get_orchestrator
from cooperage.core.models import ContainerInfo, ServerDef

logger = logging.getLogger(__name__)

app = Server("cooperage-gateway")

# Tracks server names that are currently warming up, keyed by session_id.
_warming: dict[str, set[str]] = {}

# ── Built-in servers ──────────────────────────────────────────────────────────

_WORKSPACE_SERVER_NAME = "__workspace__"
_WORKSPACE_IMAGE = "cooperage-workspace:latest"
_WORKSPACE_SERVER_DEF = ServerDef(
    name=_WORKSPACE_SERVER_NAME,
    image=_WORKSPACE_IMAGE,
    port=8000,
    description="Built-in workspace server — read/write files in the session volume.",
)

_COMPUTE_SERVER_NAME = "__compute__"
_COMPUTE_IMAGE = "cooperage-compute:latest"
_COMPUTE_SERVER_DEF = ServerDef(
    name=_COMPUTE_SERVER_NAME,
    image=_COMPUTE_IMAGE,
    port=8000,
    description="Built-in compute server — execute Python scripts with numpy/pandas/scipy/sklearn.",
)

_BUILTIN_SERVER_NAMES = {_WORKSPACE_SERVER_NAME, _COMPUTE_SERVER_NAME}


def _ensure_builtins_registered() -> None:
    """Register built-in servers if not already present."""
    if registry.get(_WORKSPACE_SERVER_NAME) is None:
        registry.register(_WORKSPACE_SERVER_DEF)
        logger.info("Auto-registered built-in workspace server (%s)", _WORKSPACE_IMAGE)
    if registry.get(_COMPUTE_SERVER_NAME) is None:
        registry.register(_COMPUTE_SERVER_DEF)
        logger.info("Auto-registered built-in compute server (%s)", _COMPUTE_IMAGE)


# ── Tool definitions ──────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="cooperage_list_servers",
            description=(
                "Discover what specialized servers are available in this Cooperage deployment. "
                "Always call this first — registered servers may already provide domain-specific "
                "tools (e.g. simulators, analyzers, data pipelines) that are faster and more "
                "capable than writing a general script. "
                "After listing, pull the servers you plan to use with cooperage_pull_server."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="cooperage_list_sessions",
            description="List all active sessions and their running containers.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="cooperage_pull_server",
            description=(
                "Pre-pull a server's Docker image. Only needed if the image is not yet "
                "cached locally (check the 'cached' field from cooperage_list_servers). "
                "Skip this if cached=true — the session pre-warms containers automatically."
            ),
            inputSchema={
                "type": "object",
                "properties": {"server_name": {"type": "string"}},
                "required": ["server_name"],
            },
        ),
        types.Tool(
            name="cooperage_create_session",
            description=(
                "Create a new Cooperage workspace session. "
                "Returns a session_id. All containers started within this session "
                "share a /workspace volume for data exchange."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Optional human-readable name for the session"},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="cooperage_list_tools",
            description=(
                "List tools available on a registered MCP server. "
                "Starts the container if it isn't already running."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "server_name": {"type": "string"},
                },
                "required": ["session_id", "server_name"],
            },
        ),
        types.Tool(
            name="cooperage_call_tool",
            description=(
                "Call a tool on a registered MCP server within a session. "
                "Starts the container if it isn't already running. "
                "All containers in the same session share /workspace."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "server_name": {"type": "string"},
                    "tool_name": {"type": "string"},
                    "arguments": {
                        "type": "object",
                        "description": "Arguments to pass to the tool",
                    },
                },
                "required": ["session_id", "server_name", "tool_name"],
            },
        ),
        types.Tool(
            name="cooperage_end_session",
            description="End a session: stop all containers and delete the shared workspace volume.",
            inputSchema={
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="cooperage_workspace_write",
            description=(
                "Write a file directly to the session's /workspace volume. "
                "Use this to persist plans, notes, intermediate results, or any text "
                "the agent needs to survive context compression. "
                "All servers in the session can read the file."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "path": {"type": "string", "description": "File path relative to /workspace (e.g. 'plan.md', 'results/output.json')"},
                    "content": {"type": "string", "description": "Text content to write"},
                },
                "required": ["session_id", "path", "content"],
            },
        ),
        types.Tool(
            name="cooperage_workspace_read",
            description=(
                "Read a file from the session's /workspace volume. "
                "Binary files (images, etc.) are returned as base64-encoded JSON. "
                "When embedding images in HTML, always set max_size=64 to get a thumbnail — "
                "this keeps the base64 small enough to embed without hitting size limits."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "path": {"type": "string", "description": "File path relative to /workspace"},
                    "max_size": {"type": "integer", "description": "For images: resize longest edge to this many pixels before returning. Use 64 when embedding in HTML. Omit for full resolution."},
                },
                "required": ["session_id", "path"],
            },
        ),
        types.Tool(
            name="cooperage_workspace_list",
            description="List all files currently in the session's /workspace volume.",
            inputSchema={
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="cooperage_run_bash",
            description=(
                "Execute a bash script in the session's compute container. "
                "The /workspace directory is available as $WORKSPACE. "
                "Useful for file manipulation, running CLI tools, or chaining commands. "
                "Do NOT use this to read files from /workspace — use cooperage_workspace_read "
                "instead (handles binary files and base64 encoding automatically). "
                "Do NOT use this for domain-specific work like image analysis — "
                "use cooperage_call_tool with a registered server instead."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "script": {"type": "string", "description": "Bash script to execute"},
                },
                "required": ["session_id", "script"],
            },
        ),
        types.Tool(
            name="cooperage_run_script",
            description=(
                "Execute a Python script in the session's compute container. "
                "Good for general computation, data wrangling, and post-processing. "
                "Before using this for domain-specific work (generating data, running simulations, "
                "specialized analysis), check cooperage_list_servers — a registered server may "
                "already do it better. "
                "Do NOT use this to read or base64-encode image files — use cooperage_workspace_read "
                "with max_size=64 instead. "
                "Keep stdout minimal — only print short confirmation messages, never large data. "
                "Write large results to /workspace files; do not print them to stdout."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "script": {"type": "string", "description": "Python script to execute"},
                },
                "required": ["session_id", "script"],
            },
        ),
    ]


# ── Tool handlers ─────────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    try:
        result = await _dispatch(name, arguments)
        text = result if isinstance(result, str) else json.dumps(result, indent=2)
        return [types.TextContent(type="text", text=text)]
    except Exception as e:
        logger.exception("Error in tool %s", name)
        return [types.TextContent(type="text", text=f"Error: {e}")]


async def _dispatch(name: str, args: dict[str, Any]) -> Any:
    if name == "cooperage_list_servers":
        return _list_servers()
    if name == "cooperage_list_sessions":
        return _list_sessions()
    if name == "cooperage_pull_server":
        return await _pull_server(args["server_name"])
    if name == "cooperage_create_session":
        return await _create_session(args.get("name"))
    if name == "cooperage_list_tools":
        return await _proxy_list_tools(args["session_id"], args["server_name"])
    if name == "cooperage_call_tool":
        return await _proxy_call_tool(
            args["session_id"],
            args["server_name"],
            args["tool_name"],
            args.get("arguments", {}),
        )
    if name == "cooperage_end_session":
        return _end_session(args["session_id"])
    if name == "cooperage_workspace_write":
        return await _workspace_op(args["session_id"], "workspace_write", {
            "path": args["path"], "content": args["content"],
        })
    if name == "cooperage_workspace_read":
        op_args = {"path": args["path"]}
        if "max_size" in args:
            op_args["max_size"] = args["max_size"]
        return await _workspace_op(args["session_id"], "workspace_read", op_args)
    if name == "cooperage_workspace_list":
        return await _workspace_op(args["session_id"], "workspace_list", {})
    if name == "cooperage_run_script":
        return await _proxy_call_tool(args["session_id"], _COMPUTE_SERVER_NAME, "run_script", {"script": args["script"]})
    if name == "cooperage_run_bash":
        return await _proxy_call_tool(args["session_id"], _COMPUTE_SERVER_NAME, "run_bash", {"script": args["script"]})
    raise ValueError(f"Unknown tool: {name!r}")


def _list_sessions() -> list[dict]:
    all_sessions = sessions.list_sessions()
    result = []
    for s in all_sessions:
        containers = []
        started = set(s.containers.keys())
        for server_name, container_id in s.containers.items():
            containers.append({
                "server_name": server_name,
                "container_id": container_id,
                "builtin": server_name in _BUILTIN_SERVER_NAMES,
                "status": "ready",
            })
        for server_name in _warming.get(s.id, set()):
            if server_name not in started:
                containers.append({
                    "server_name": server_name,
                    "container_id": None,
                    "builtin": server_name in _BUILTIN_SERVER_NAMES,
                    "status": "warming",
                })
        result.append({
            "session_id": s.id,
            "name": s.name,
            "expires_at": s.expires_at.isoformat(),
            "containers": containers,
        })
    return result


def _list_servers() -> list[dict]:
    orch = get_orchestrator()
    return [
        {
            "name": s.name,
            "description": s.description,
            "image": s.image,
            "cached": orch.image_exists(s.image),
        }
        for s in registry.load()
        if s.name not in _BUILTIN_SERVER_NAMES  # hide internal servers
    ]


async def _pull_server(server_name: str) -> dict:
    server_def = registry.get(server_name)
    if server_def is None:
        raise ValueError(f"No server named {server_name!r} in registry")
    orch = get_orchestrator()
    image_id = await asyncio.to_thread(orch.pull_image, server_def.image)
    return {"server": server_name, "image": server_def.image, "image_id": image_id}


async def _create_session(name: str | None) -> dict:
    session = sessions.create_session(name=name)
    servers_to_warm = [_WORKSPACE_SERVER_DEF, _COMPUTE_SERVER_DEF] + [
        s for s in registry.load() if s.name not in _BUILTIN_SERVER_NAMES
    ]
    _warming[session.id] = {s.name for s in servers_to_warm}
    for server_def in servers_to_warm:
        asyncio.create_task(_warmup_builtin(session.id, server_def))
    return {
        "session_id": session.id,
        "name": session.name,
        "volume": session.volume_name,
        "expires_at": session.expires_at.isoformat(),
    }


async def _warmup_builtin(session_id: str, server_def: ServerDef) -> None:
    try:
        await asyncio.to_thread(sessions.get_or_start_container, session_id, server_def)
        logger.info("%s container ready for session %s", server_def.name, session_id[:8])
    except Exception as e:
        logger.warning("%s pre-warm failed for session %s: %s", server_def.name, session_id[:8], e)
    finally:
        _warming.get(session_id, set()).discard(server_def.name)
        if session_id in _warming and not _warming[session_id]:
            del _warming[session_id]


def _end_session(session_id: str) -> dict:
    ok = sessions.end_session(session_id)
    return {"ended": ok, "session_id": session_id}


async def _ensure_container(session_id: str, server_name: str) -> ContainerInfo:
    server_def = registry.get(server_name)
    if server_def is None:
        raise ValueError(f"No server named {server_name!r} in registry")
    return await asyncio.to_thread(sessions.get_or_start_container, session_id, server_def)


async def _workspace_op(session_id: str, tool_name: str, arguments: dict) -> Any:
    """Route a workspace tool call through the built-in workspace container."""
    return await _proxy_call_tool(session_id, _WORKSPACE_SERVER_NAME, tool_name, arguments)


_MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


async def _proxy_list_tools(session_id: str, server_name: str) -> list[dict]:
    info = await _ensure_container(session_id, server_name)
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{info.mcp_url}/mcp", json=payload, headers=_MCP_HEADERS)
        resp.raise_for_status()
        data = resp.json()
    return data.get("result", {}).get("tools", [])


async def _proxy_call_tool(
    session_id: str,
    server_name: str,
    tool_name: str,
    arguments: dict,
) -> Any:
    info = await _ensure_container(session_id, server_name)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f"{info.mcp_url}/mcp", json=payload, headers=_MCP_HEADERS)
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        raise RuntimeError(data["error"].get("message", str(data["error"])))

    result = data.get("result", {})
    content = result.get("content", [])
    if content:
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(texts) if texts else result
    # Empty content (e.g. FastMCP returns [] for empty list) — use structuredContent if present
    structured = result.get("structuredContent", {}).get("result")
    if structured is not None:
        return json.dumps(structured)
    return result


# ── Upload endpoint ───────────────────────────────────────────────────────────

async def _send_json_response(send, status: int, body: dict) -> None:
    import json as _json
    body_bytes = _json.dumps(body).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            [b"content-type", b"application/json"],
            [b"content-length", str(len(body_bytes)).encode()],
            [b"access-control-allow-origin", b"*"],
        ],
    })
    await send({"type": "http.response.body", "body": body_bytes})


async def _handle_upload(scope, receive, send) -> None:
    """Handle POST /upload/{session_id}?path=<workspace_path> with raw bytes body."""
    import base64
    from urllib.parse import parse_qs, unquote

    path = scope.get("path", "")
    parts = path.strip("/").split("/")
    if len(parts) < 2 or not parts[1]:
        await _send_json_response(send, 400, {"error": "Missing session_id in path"})
        return

    session_id = parts[1]

    query_string = scope.get("query_string", b"").decode()
    params = parse_qs(query_string)
    file_path = params.get("path", [None])[0]
    if not file_path:
        await _send_json_response(send, 400, {"error": "Missing 'path' query parameter"})
        return
    file_path = unquote(file_path)

    body = b""
    more_body = True
    while more_body:
        event = await receive()
        body += event.get("body", b"")
        more_body = event.get("more_body", False)

    encoded = base64.b64encode(body).decode("ascii")
    try:
        result = await _workspace_op(session_id, "workspace_write_binary", {"path": file_path, "data": encoded})
        await _send_json_response(send, 200, {"ok": True, "path": file_path, "result": result})
    except Exception as e:
        logger.exception("Upload failed for session %s path %s", session_id, file_path)
        await _send_json_response(send, 500, {"error": str(e)})


# ── Entry point ───────────────────────────────────────────────────────────────

async def run_stdio() -> None:
    """Run the gateway over stdio (for Claude Desktop / MCP CLI)."""
    from cooperage.session.manager import start_cleanup_thread
    _ensure_builtins_registered()
    start_cleanup_thread()
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


async def run_sse(host: str | None = None, port: int | None = None) -> None:
    """Run the gateway as a streamable HTTP server (POST /mcp)."""
    from cooperage.session.manager import start_cleanup_thread
    from cooperage.core.config import settings
    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    _ensure_builtins_registered()
    start_cleanup_thread()

    session_manager = StreamableHTTPSessionManager(
        app=app,
        json_response=True,
        stateless=True,
    )

    class GatewayASGIApp:
        async def __call__(self, scope, receive, send):
            if scope["type"] == "lifespan":
                async with session_manager.run():
                    await receive()
                    await send({"type": "lifespan.startup.complete"})
                    await receive()
                    await send({"type": "lifespan.shutdown.complete"})
            elif scope["type"] == "http" and scope.get("path", "").startswith("/upload/"):
                await _handle_upload(scope, receive, send)
            else:
                await session_manager.handle_request(scope, receive, send)

    config = uvicorn.Config(
        GatewayASGIApp(),
        host=host or settings.gateway_host,
        port=port or settings.gateway_port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()
