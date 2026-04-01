"""
Cooperage MCP Gateway

Exposes a single MCP server to the LLM with tools for orchestrating
ephemeral containers and resources for reading session/registry state.

All heavy lifting is delegated to the session manager and orchestrator.
"""

import asyncio
import contextvars
import itertools
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
from cooperage.core.auth import AuthContext, authenticate_request, check_server_access, get_oidc_config, load_api_keys
from cooperage.core.audit import AuditEvent, AuditEventType, emit as audit_emit, measure as audit_measure, elapsed_ms as audit_elapsed_ms
from cooperage.core.models import ContainerInfo, ServerDef

logger = logging.getLogger(__name__)

app = Server("cooperage-gateway")

# Per-request auth context (set by ASGI middleware, default for stdio)
_auth_ctx: contextvars.ContextVar[AuthContext] = contextvars.ContextVar(
    "_auth_ctx", default=AuthContext(tenant_id="default"),
)

# Incrementing JSON-RPC request IDs
_rpc_id_counter = itertools.count(1)

# Shared httpx client (created lazily, cleaned up on shutdown)
_http_client: httpx.AsyncClient | None = None


async def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=120)
    return _http_client


# Tracks server names that are currently warming up, keyed by session_id.
_warming: dict[str, set[str]] = {}
# Tracks warmup tasks so they can be cancelled on session teardown.
_warmup_tasks: dict[str, list[asyncio.Task]] = {}

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


# ── Tool registry (decorator-based) ──────────────────────────────────────────

_tools: dict[str, dict] = {}  # name → {handler, description, params, requires_session, requires_server}


def tool(
    name: str,
    *,
    description: str,
    params: dict[str, dict] | None = None,
    required: list[str] | None = None,
    requires_session: bool = False,
    requires_server: bool = False,
):
    """Register a gateway tool with automatic schema generation and auth wiring.

    - requires_session: auto-checks session tenant ownership before calling
    - requires_server: auto-checks server RBAC before calling
    """
    def decorator(fn):
        schema = {
            "type": "object",
            "properties": params or {},
            "required": required or [k for k, v in (params or {}).items()
                                     if not v.get("description", "").startswith("Optional")],
        }
        _tools[name] = {
            "handler": fn,
            "description": description,
            "schema": schema,
            "requires_session": requires_session,
            "requires_server": requires_server,
        }
        return fn
    return decorator


# ── Tool definitions ──────────────────────────────────────────────────────────

@tool(
    "cooperage_list_servers",
    description=(
        "Discover what specialized servers are available in this Cooperage deployment. "
        "Always call this first — registered servers may already provide domain-specific "
        "tools (e.g. simulators, analyzers, data pipelines) that are faster and more "
        "capable than writing a general script. "
        "After listing, pull the servers you plan to use with cooperage_pull_server. "
        "If a server has a repo_url, you can clone it with cooperage_run_bash to inspect "
        "its source code when debugging unexpected tool behavior."
        "The user is not required to know what servers are available in advance or their status."
        "So you may report your findings to the user of what relevant servers are available for the "
        "task at hand."
    ),
)
def list_servers(auth: AuthContext, **kwargs) -> list[dict]:
    orch = get_orchestrator()
    servers = []
    for s in registry.load():
        if s.name in _BUILTIN_SERVER_NAMES:
            continue
        if auth.allowed_servers is not None and s.name not in auth.allowed_servers:
            continue
        entry = {
            "name": s.name,
            "description": s.description,
            "image": s.image,
            "cached": orch.image_exists(s.image),
        }
        if s.repo_url:
            entry["repo_url"] = s.repo_url
        servers.append(entry)
    return servers


@tool(
    "cooperage_list_sessions",
    description="List all active sessions and their running containers.",
)
def list_sessions_tool(auth: AuthContext, **kwargs) -> list[dict]:
    tenant_filter = auth.tenant_id if auth.tenant_id != "default" else None
    all_sessions = sessions.list_sessions(tenant_id=tenant_filter)
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
            "tenant_id": s.tenant_id,
            "expires_at": s.expires_at.isoformat(),
            "containers": containers,
        })
    return result


@tool(
    "cooperage_pull_server",
    description=(
        "Pre-pull a server's Docker image. Only needed if the image is not yet "
        "cached locally (check the 'cached' field from cooperage_list_servers). "
        "Skip this if cached=true — the session pre-warms containers automatically."
    ),
    params={"server_name": {"type": "string"}},
    required=["server_name"],
    requires_server=True,
)
async def pull_server(server_name: str, **kwargs) -> dict:
    server_def = registry.get(server_name)
    if server_def is None:
        raise ValueError(f"No server named {server_name!r} in registry")
    orch = get_orchestrator()
    image_id = await asyncio.to_thread(orch.pull_image, server_def.image)
    return {"server": server_name, "image": server_def.image, "image_id": image_id}


@tool(
    "cooperage_create_session",
    description=(
        "Create a new Cooperage workspace session. "
        "Returns a session_id. All containers started within this session "
        "share a /workspace volume for data exchange. "
        "IMPORTANT: if the response includes a ui_url, you MUST share it with "
        "the user immediately as a clickable link — they use it to monitor "
        "files and containers in real time."
    ),
    params={"name": {"type": "string", "description": "Optional human-readable name for the session"}},
    required=[],
)
async def create_session(auth: AuthContext, name: str | None = None, **kwargs) -> dict:
    from cooperage.core.config import settings as _settings
    if auth.max_sessions is not None:
        current = sessions.count_sessions_for_tenant(auth.tenant_id)
        if current >= auth.max_sessions:
            raise PermissionError(
                f"Tenant {auth.tenant_id!r} has reached the session limit ({auth.max_sessions})"
            )

    session = sessions.create_session(name=name, tenant_id=auth.tenant_id)
    audit_emit(AuditEvent(
        event_type=AuditEventType.SESSION_CREATE,
        session_id=session.id,
        tenant_id=auth.tenant_id,
        metadata={"name": name, "volume": session.volume_name},
    ))
    servers_to_warm = [_WORKSPACE_SERVER_DEF, _COMPUTE_SERVER_DEF]
    _warming[session.id] = {s.name for s in servers_to_warm}
    tasks = []
    for server_def in servers_to_warm:
        task = asyncio.create_task(_warmup_builtin(session.id, server_def))
        tasks.append(task)
    _warmup_tasks[session.id] = tasks
    lines = [
        f"Session created. session_id: {session.id}",
        f"Workspace volume: {session.volume_name}",
        f"Expires: {session.expires_at.isoformat()}",
    ]
    if _settings.ui_url:
        ui_url = f"{_settings.ui_url.rstrip('/')}/?session={session.id}"
        lines.append(f"IMPORTANT: Share this link with the user so they can monitor files and containers in real time: {ui_url}")
    return "\n".join(lines)


@tool(
    "cooperage_list_tools",
    description=(
        "List tools available on a registered MCP server. "
        "Starts the container if it isn't already running."
    ),
    params={
        "session_id": {"type": "string"},
        "server_name": {"type": "string"},
    },
    required=["session_id", "server_name"],
    requires_session=True,
    requires_server=True,
)
async def proxy_list_tools(session_id: str, server_name: str, **kwargs) -> list[dict]:
    info = await _ensure_container(session_id, server_name)
    payload = {"jsonrpc": "2.0", "id": next(_rpc_id_counter), "method": "tools/list", "params": {}}
    client = await _get_http_client()
    resp = await client.post(f"{info.mcp_url}/mcp", json=payload, headers=_MCP_HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("result", {}).get("tools", [])


@tool(
    "cooperage_call_tool",
    description=(
        "Call a tool on a registered MCP server within a session. "
        "Starts the container if it isn't already running. "
        "All containers in the same session share /workspace."
    ),
    params={
        "session_id": {"type": "string"},
        "server_name": {"type": "string"},
        "tool_name": {"type": "string"},
        "arguments": {"type": "object", "description": "Optional arguments to pass to the tool"},
    },
    required=["session_id", "server_name", "tool_name"],
    requires_session=True,
    requires_server=True,
)
async def call_tool_proxy(session_id: str, server_name: str, tool_name: str, arguments: dict | None = None, **kwargs):
    return await _proxy_call_tool(session_id, server_name, tool_name, arguments or {})


@tool(
    "cooperage_end_session",
    description="End a session: stop all containers and delete the shared workspace volume.",
    params={"session_id": {"type": "string"}},
    required=["session_id"],
    requires_session=True,
)
async def end_session(session_id: str, **kwargs) -> dict:
    for task in _warmup_tasks.pop(session_id, []):
        task.cancel()
    _warming.pop(session_id, None)
    auth = _auth_ctx.get()
    ok = sessions.end_session(session_id)
    audit_emit(AuditEvent(
        event_type=AuditEventType.SESSION_END,
        session_id=session_id,
        tenant_id=auth.tenant_id,
    ))
    return {"ended": ok, "session_id": session_id}


@tool(
    "cooperage_workspace_write",
    description=(
        "Write a file directly to the session's /workspace volume. "
        "Use this to persist plans, notes, intermediate results, or any text "
        "the agent needs to survive context compression. "
        "All servers in the session can read the file."
    ),
    params={
        "session_id": {"type": "string"},
        "path": {"type": "string", "description": "File path relative to /workspace (e.g. 'plan.md', 'results/output.json')"},
        "content": {"type": "string", "description": "Text content to write"},
    },
    required=["session_id", "path", "content"],
    requires_session=True,
)
async def workspace_write(session_id: str, path: str, content: str, **kwargs):
    return await _workspace_op(session_id, "workspace_write", {"path": path, "content": content})


@tool(
    "cooperage_workspace_read",
    description=(
        "Read a file from the session's /workspace volume. "
        "Binary files (images, etc.) are returned as base64-encoded JSON. "
        "When embedding images in HTML, always set max_size=64 to get a thumbnail — "
        "this keeps the base64 small enough to embed without hitting size limits."
    ),
    params={
        "session_id": {"type": "string"},
        "path": {"type": "string", "description": "File path relative to /workspace"},
        "max_size": {"type": "integer", "description": "Optional — for images: resize longest edge to this many pixels before returning. Use 64 when embedding in HTML. Omit for full resolution."},
    },
    required=["session_id", "path"],
    requires_session=True,
)
async def workspace_read(session_id: str, path: str, max_size: int | None = None, **kwargs):
    op_args: dict[str, Any] = {"path": path}
    if max_size is not None:
        op_args["max_size"] = max_size
    return await _workspace_op(session_id, "workspace_read", op_args)


@tool(
    "cooperage_workspace_list",
    description="List all files currently in the session's /workspace volume.",
    params={"session_id": {"type": "string"}},
    required=["session_id"],
    requires_session=True,
)
async def workspace_list(session_id: str, **kwargs):
    return await _workspace_op(session_id, "workspace_list", {})


@tool(
    "cooperage_run_bash",
    description=(
        "Execute a bash script in the session's compute container. "
        "The /workspace directory is available as $WORKSPACE. "
        "Useful for file manipulation, running CLI tools, or chaining commands. "
        "Do NOT use this to read files from /workspace — use cooperage_workspace_read "
        "instead (handles binary files and base64 encoding automatically). "
        "Do NOT use this for domain-specific work like image analysis — "
        "use cooperage_call_tool with a registered server instead."
    ),
    params={
        "session_id": {"type": "string"},
        "script": {"type": "string", "description": "Bash script to execute"},
    },
    required=["session_id", "script"],
    requires_session=True,
)
async def run_bash(session_id: str, script: str, **kwargs):
    return await _proxy_call_tool(session_id, _COMPUTE_SERVER_NAME, "run_bash", {"script": script})


@tool(
    "cooperage_run_script",
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
    params={
        "session_id": {"type": "string"},
        "script": {"type": "string", "description": "Python script to execute"},
    },
    required=["session_id", "script"],
    requires_session=True,
)
async def run_script(session_id: str, script: str, **kwargs):
    return await _proxy_call_tool(session_id, _COMPUTE_SERVER_NAME, "run_script", {"script": script})


# ── MCP handlers (wired to the decorator registry) ───────────────────────────

@app.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(name=name, description=t["description"], inputSchema=t["schema"])
        for name, t in _tools.items()
    ]


@app.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    try:
        result = await _dispatch(name, arguments)
        text = result if isinstance(result, str) else json.dumps(result, indent=2)
        return [types.TextContent(type="text", text=text)]
    except PermissionError as e:
        return [types.TextContent(type="text", text=f"Permission denied: {e}")]
    except Exception as e:
        logger.exception("Error in tool %s", name)
        return [types.TextContent(type="text", text=f"Error: {e}")]


async def _dispatch(name: str, args: dict[str, Any]) -> Any:
    entry = _tools.get(name)
    if entry is None:
        raise ValueError(f"Unknown tool: {name!r}")

    auth = _auth_ctx.get()
    start = audit_measure()
    error_msg = None
    try:
        # Auto-check session tenant ownership
        if entry["requires_session"] and "session_id" in args:
            _check_session_tenant(args["session_id"], auth)

        # Auto-check server RBAC
        if entry["requires_server"] and "server_name" in args:
            check_server_access(auth, args["server_name"])

        handler = entry["handler"]
        if asyncio.iscoroutinefunction(handler):
            result = await handler(auth=auth, **args)
        else:
            result = handler(auth=auth, **args)
        return result
    except Exception as e:
        error_msg = str(e)
        raise
    finally:
        audit_emit(AuditEvent(
            event_type=AuditEventType.TOOL_CALL,
            session_id=args.get("session_id"),
            tenant_id=auth.tenant_id,
            server_name=args.get("server_name"),
            tool_name=name,
            arguments={k: v for k, v in args.items() if k not in ("content", "data")},
            duration_ms=audit_elapsed_ms(start),
            error=error_msg,
        ))


def _check_session_tenant(session_id: str, auth: AuthContext) -> None:
    """Ensure the session belongs to the authenticated tenant."""
    session = sessions.get_session(session_id)
    if session is None:
        raise ValueError(f"Session {session_id!r} not found")
    if auth.tenant_id != "default" and session.tenant_id != auth.tenant_id:
        raise PermissionError(
            f"Session {session_id[:8]}... belongs to a different tenant"
        )


# ── MCP Resources ────────────────────────────────────────────────────────────

@app.list_resources()
async def handle_list_resources() -> list[types.Resource]:
    auth = _auth_ctx.get()
    resources = [
        types.Resource(
            uri="cooperage://registry/servers",
            name="Server Registry",
            description="All registered MCP servers and their images",
            mimeType="application/json",
        ),
        types.Resource(
            uri="cooperage://sessions",
            name="Active Sessions",
            description="All active sessions with container status",
            mimeType="application/json",
        ),
    ]

    # Add per-session workspace listings
    tenant_filter = auth.tenant_id if auth.tenant_id != "default" else None
    for s in sessions.list_sessions(tenant_id=tenant_filter):
        label = s.name or s.id[:8]
        resources.append(types.Resource(
            uri=f"cooperage://sessions/{s.id}/workspace",
            name=f"Workspace: {label}",
            description=f"Files in the /workspace volume for session {label}",
            mimeType="application/json",
        ))

    return resources


@app.read_resource()
async def handle_read_resource(uri) -> str:
    uri_str = str(uri)
    auth = _auth_ctx.get()

    if uri_str == "cooperage://registry/servers":
        return json.dumps(list_servers(auth=auth), indent=2)

    if uri_str == "cooperage://sessions":
        return json.dumps(list_sessions_tool(auth=auth), indent=2)

    # cooperage://sessions/{session_id}/workspace
    if uri_str.startswith("cooperage://sessions/") and uri_str.endswith("/workspace"):
        session_id = uri_str.split("/")[3]
        _check_session_tenant(session_id, auth)
        try:
            result = await _workspace_op(session_id, "workspace_list", {})
            if isinstance(result, str):
                return result
            return json.dumps(result, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)})

    raise ValueError(f"Unknown resource: {uri_str}")


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _warmup_builtin(session_id: str, server_def: ServerDef) -> None:
    try:
        await asyncio.to_thread(sessions.get_or_start_container, session_id, server_def)
        logger.info("%s container ready for session %s", server_def.name, session_id[:8])
    except asyncio.CancelledError:
        logger.info("%s warmup cancelled for session %s", server_def.name, session_id[:8])
    except Exception as e:
        logger.warning("%s pre-warm failed for session %s: %s", server_def.name, session_id[:8], e)
    finally:
        _warming.get(session_id, set()).discard(server_def.name)
        if session_id in _warming and not _warming[session_id]:
            del _warming[session_id]


async def _ensure_container(session_id: str, server_name: str) -> ContainerInfo:
    server_def = registry.get(server_name)
    if server_def is None:
        raise ValueError(f"No server named {server_name!r} in registry")
    session = sessions.get_session(session_id)
    already_running = session is not None and server_name in session.containers
    start = audit_measure()
    info = await asyncio.to_thread(sessions.get_or_start_container, session_id, server_def)
    if not already_running:
        audit_emit(AuditEvent(
            event_type=AuditEventType.CONTAINER_START,
            session_id=session_id,
            tenant_id=session.tenant_id if session else "default",
            server_name=server_name,
            duration_ms=audit_elapsed_ms(start),
            metadata={"container_id": info.container_id, "image": server_def.image},
        ))
    sessions.touch_container(session_id, server_name)
    sessions.touch_session(session_id)
    return info


async def _workspace_op(session_id: str, tool_name: str, arguments: dict) -> Any:
    """Route a workspace tool call through the built-in workspace container."""
    return await _proxy_call_tool(session_id, _WORKSPACE_SERVER_NAME, tool_name, arguments)


# ── MCP proxy ─────────────────────────────────────────────────────────────────

_MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


async def _proxy_call_tool(
    session_id: str,
    server_name: str,
    tool_name: str,
    arguments: dict,
) -> Any:
    info = await _ensure_container(session_id, server_name)
    payload = {
        "jsonrpc": "2.0",
        "id": next(_rpc_id_counter),
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    client = await _get_http_client()
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
    structured = result.get("structuredContent", {}).get("result")
    if structured is not None:
        return json.dumps(structured)
    return result


# ── Container logs endpoint ────────────────────────────────────────────────────

async def _handle_logs(scope, receive, send) -> None:
    """Handle GET /logs/{session_id}/{container_id}?tail=100"""
    from urllib.parse import parse_qs

    try:
        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        auth = authenticate_request(headers)
    except PermissionError as e:
        await _send_json_response(send, 401, {"error": str(e)})
        return

    path = scope.get("path", "")
    parts = path.strip("/").split("/")
    # /logs/{session_id}/{container_id}
    if len(parts) < 3:
        await _send_json_response(send, 400, {"error": "Expected /logs/{session_id}/{container_id}"})
        return

    session_id = parts[1]
    container_id = parts[2]

    # Verify session exists and belongs to this tenant
    session = sessions.get_session(session_id)
    if session is None:
        await _send_json_response(send, 404, {"error": f"Session {session_id} not found"})
        return
    if auth.tenant_id != "default" and session.tenant_id != auth.tenant_id:
        await _send_json_response(send, 403, {"error": "Access denied"})
        return

    # Verify container belongs to this session
    valid_ids = set(session.containers.values())
    if container_id not in valid_ids:
        await _send_json_response(send, 404, {"error": f"Container {container_id} not in session"})
        return

    qs = parse_qs(scope.get("query_string", b"").decode())
    tail = int(qs.get("tail", ["100"])[0])

    orch = get_orchestrator()
    logs = await asyncio.to_thread(orch.get_container_logs, container_id, tail)

    await _send_json_response(send, 200, {"container_id": container_id, "logs": logs})


# ── Upload endpoint ───────────────────────────────────────────────────────────

async def _send_json_response(send, status: int, body: dict) -> None:
    body_bytes = json.dumps(body).encode()
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

    try:
        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        auth = authenticate_request(headers)
    except PermissionError as e:
        await _send_json_response(send, 401, {"error": str(e)})
        return

    path = scope.get("path", "")
    parts = path.strip("/").split("/")
    if len(parts) < 2 or not parts[1]:
        await _send_json_response(send, 400, {"error": "Missing session_id in path"})
        return

    session_id = parts[1]

    try:
        _check_session_tenant(session_id, auth)
    except (PermissionError, ValueError) as e:
        status = 403 if isinstance(e, PermissionError) else 404
        await _send_json_response(send, status, {"error": str(e)})
        return

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


# ── Entry points ──────────────────────────────────────────────────────────────

async def run_proxy(url: str) -> None:
    """Bridge stdio (Claude Desktop) to a remote Cooperage gateway over HTTP.

    Forwards every MCP message from stdin to the remote gateway and streams
    responses back to stdout — no local Docker or session state needed.
    """
    import anyio
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (remote_read, remote_write, _):
        async with stdio_server() as (local_read, local_write):
            async def stdin_to_remote() -> None:
                async with remote_write:
                    async for msg in local_read:
                        if isinstance(msg, Exception):
                            continue
                        await remote_write.send(msg)

            async def remote_to_stdout() -> None:
                async with local_write:
                    async for msg in remote_read:
                        if isinstance(msg, Exception):
                            continue
                        await local_write.send(msg)

            async with anyio.create_task_group() as tg:
                tg.start_soon(stdin_to_remote)
                tg.start_soon(remote_to_stdout)


async def run_stdio() -> None:
    """Run the gateway over stdio (for Claude Desktop / MCP CLI).
    No auth — stdio is always the default tenant."""
    from cooperage.session.manager import start_cleanup_thread
    from cooperage.core.config import settings
    from cooperage.core import audit
    audit.init(settings.audit_log_path)
    _ensure_builtins_registered()
    start_cleanup_thread()
    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    finally:
        if _http_client and not _http_client.is_closed:
            await _http_client.aclose()


async def run_sse(host: str | None = None, port: int | None = None) -> None:
    """Run the gateway as a streamable HTTP server (POST /mcp)."""
    from cooperage.session.manager import start_cleanup_thread
    from cooperage.core.config import settings
    from cooperage.core import audit
    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    audit.init(settings.audit_log_path)
    _ensure_builtins_registered()
    load_api_keys()
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
                return

            if scope["type"] == "http" and scope.get("path", "") == "/health":
                await _send_json_response(send, 200, {"status": "ok"})
                return

            if scope["type"] == "http" and scope.get("path", "") == "/oidc-config":
                oidc = get_oidc_config()
                if oidc is None:
                    await _send_json_response(send, 404, {"error": "OIDC not configured"})
                else:
                    await _send_json_response(send, 200, oidc)
                return

            if scope["type"] == "http" and scope.get("path", "").startswith("/logs/"):
                await _handle_logs(scope, receive, send)
                return

            if scope["type"] == "http" and scope.get("path", "").startswith("/upload/"):
                await _handle_upload(scope, receive, send)
                return

            # Authenticate MCP requests from HTTP headers
            if scope["type"] == "http":
                headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
                try:
                    auth = authenticate_request(headers)
                except PermissionError as e:
                    await _send_json_response(send, 401, {"error": str(e)})
                    return
                token = _auth_ctx.set(auth)
                try:
                    await session_manager.handle_request(scope, receive, send)
                finally:
                    _auth_ctx.reset(token)
            else:
                await session_manager.handle_request(scope, receive, send)

    config = uvicorn.Config(
        GatewayASGIApp(),
        host=host or settings.gateway_host,
        port=port or settings.gateway_port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        if _http_client and not _http_client.is_closed:
            await _http_client.aclose()
