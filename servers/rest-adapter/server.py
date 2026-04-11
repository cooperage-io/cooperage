"""
Cooperage Universal Adapter — wraps REST APIs, LangChain tools, and Python
functions as MCP servers.

Reads COOPERAGE_ADAPTER_CONFIG env var (JSON), generates MCP tools dynamically.
"""

import base64
import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx
from cooperage_sdk import workspace, serve
from mcp.server.fastmcp import FastMCP


WORKSPACE = workspace.root

mcp = FastMCP("cooperage-adapter", json_response=True, stateless_http=True)


# ── Config parsing ───────────────────────────────────────────────────────────


def _load_config() -> dict:
    raw = os.environ.get("COOPERAGE_ADAPTER_CONFIG")
    if not raw:
        raise RuntimeError("COOPERAGE_ADAPTER_CONFIG env var not set")
    return json.loads(raw)


def _resolve_env(value: str | None) -> str | None:
    """Replace ${ENV_VAR} references with actual values."""
    if value is None:
        return None
    def _sub(m):
        return os.environ.get(m.group(1), "")
    return re.sub(r"\$\{(\w+)\}", _sub, value)


# ── Auth ─────────────────────────────────────────────────────────────────────


def _build_auth_headers(auth: dict) -> dict[str, str]:
    auth_type = auth.get("type", "none")
    headers = {}
    if auth_type == "bearer":
        token = _resolve_env(auth.get("token"))
        if token:
            headers["Authorization"] = f"Bearer {token}"
    elif auth_type == "api-key":
        key = _resolve_env(auth.get("api_key"))
        header_name = auth.get("api_key_header", "X-API-Key")
        if key:
            headers[header_name] = key
    elif auth_type == "basic":
        username = _resolve_env(auth.get("username")) or ""
        password = _resolve_env(auth.get("password")) or ""
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {encoded}"
    return headers


# ── REST API adapter ─────────────────────────────────────────────────────────


def _register_rest_tools(config: dict):
    base_url = config.get("base_url", "").rstrip("/")
    auth = config.get("auth", {})
    default_headers = config.get("default_headers", {})
    tools = config.get("tools", [])

    for tool_def in tools:
        _register_one_rest_tool(tool_def, base_url, auth, default_headers)


def _register_one_rest_tool(tool_def: dict, base_url: str, auth: dict, default_headers: dict):
    name = tool_def["name"]
    description = tool_def.get("description", "")
    method = tool_def.get("method", "GET").upper()
    path_template = tool_def.get("path", "/")
    params = tool_def.get("params", {})
    extra_headers = tool_def.get("headers", {})

    def make_handler(td_name, td_method, td_path, td_params, td_headers):
        async def handler(**kwargs):
            url = base_url + td_path
            query = {}
            body = {}
            headers = {**default_headers, **td_headers}
            headers.update(_build_auth_headers(auth))
            # Resolve ${ENV_VAR} in headers
            headers = {k: _resolve_env(v) for k, v in headers.items()}

            for pname, pdef in td_params.items():
                value = kwargs.get(pname)
                if value is None:
                    continue
                location = pdef.get("location")
                if location is None:
                    location = "query" if td_method in ("GET", "DELETE") else "body"

                if location == "path":
                    url = url.replace(f"{{{pname}}}", str(value))
                elif location == "query":
                    query[pname] = value
                elif location == "body":
                    body[pname] = value
                elif location == "header":
                    headers[pname] = str(value)

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.request(
                    td_method, url,
                    params=query or None,
                    json=body or None,
                    headers=headers,
                )

            content_type = resp.headers.get("content-type", "")
            if resp.status_code >= 400:
                return json.dumps({
                    "error": True,
                    "status": resp.status_code,
                    "body": resp.text[:2000],
                })

            if "application/json" in content_type:
                try:
                    return json.dumps(resp.json(), indent=2)
                except Exception:
                    return resp.text
            elif "text/" in content_type or "xml" in content_type:
                text = resp.text
                if len(text) > 50000:
                    text = text[:50000] + "\n...[truncated]"
                return text
            else:
                return json.dumps({
                    "status": resp.status_code,
                    "content_type": content_type,
                    "encoding": "base64",
                    "data": base64.b64encode(resp.content).decode(),
                })

        handler.__name__ = td_name
        handler.__doc__ = description
        return handler

    # Build parameter annotations for FastMCP
    fn = make_handler(name, method, path_template, params, extra_headers)

    # Register with dynamic schema
    properties = {}
    required = []
    for pname, pdef in params.items():
        ptype = pdef.get("type", "string")
        prop = {"type": ptype}
        if pdef.get("description"):
            prop["description"] = pdef["description"]
        properties[pname] = prop
        if pdef.get("required", True):
            required.append(pname)

    schema = {
        "type": "object",
        "properties": properties,
        "required": required,
    }

    # Use lower-level registration
    mcp._tool_manager._tools[name] = type("ToolMeta", (), {
        "name": name,
        "description": description,
        "parameters": schema,
        "fn": fn,
    })()


# ── LangChain adapter ────────────────────────────────────────────────────────


def _import_module_from_source(source: str):
    """Import a module from a file path or module name."""
    path = Path(source)
    # Check workspace first
    workspace_path = WORKSPACE / source
    if workspace_path.exists():
        path = workspace_path
    if path.exists():
        spec = importlib.util.spec_from_file_location("adapter_module", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    return importlib.import_module(source)


def _register_langchain_tools(config: dict):
    source = config.get("source")
    package = config.get("package")
    tool_names = config.get("langchain_tools", [])

    if package:
        subprocess.check_call([sys.executable, "-m", "uv", "pip", "install", "--system", package])

    if not source:
        raise RuntimeError("LangChain adapter requires 'source' field")

    mod = _import_module_from_source(source)

    # Find LangChain tools: @tool decorated functions or BaseTool subclasses
    discovered = {}
    for attr_name in dir(mod):
        obj = getattr(mod, attr_name)
        # Check for LangChain @tool (has .name and .invoke)
        if hasattr(obj, "name") and hasattr(obj, "invoke") and callable(getattr(obj, "invoke", None)):
            discovered[obj.name] = obj
        # Check for BaseTool subclass instances
        elif hasattr(obj, "run") and hasattr(obj, "name") and hasattr(obj, "description"):
            discovered[obj.name] = obj

    if tool_names:
        discovered = {k: v for k, v in discovered.items() if k in tool_names}

    for tool_name, lc_tool in discovered.items():
        _register_one_langchain_tool(tool_name, lc_tool)


def _register_one_langchain_tool(name: str, lc_tool):
    description = getattr(lc_tool, "description", "") or ""

    # Extract schema from LangChain tool
    schema = {"type": "object", "properties": {}, "required": []}
    if hasattr(lc_tool, "args_schema") and lc_tool.args_schema is not None:
        try:
            schema = lc_tool.args_schema.model_json_schema()
        except Exception:
            pass

    def make_handler(tool):
        async def handler(**kwargs):
            result = tool.invoke(kwargs)
            if isinstance(result, str):
                return result
            return json.dumps(result, indent=2, default=str)
        handler.__name__ = name
        handler.__doc__ = description
        return handler

    fn = make_handler(lc_tool)
    mcp._tool_manager._tools[name] = type("ToolMeta", (), {
        "name": name,
        "description": description,
        "parameters": schema,
        "fn": fn,
    })()


# ── Python function adapter ──────────────────────────────────────────────────


def _register_python_tools(config: dict):
    source = config.get("source")
    package = config.get("package")
    tools = config.get("python_tools", [])

    if package:
        subprocess.check_call([sys.executable, "-m", "uv", "pip", "install", "--system", package])

    if not source:
        raise RuntimeError("Python adapter requires 'source' field")

    mod = _import_module_from_source(source)

    for tool_def in tools:
        func_name = tool_def.get("function", tool_def["name"])
        func = getattr(mod, func_name, None)
        if func is None:
            raise RuntimeError(f"Function {func_name!r} not found in {source}")
        _register_one_python_tool(tool_def, func)


def _register_one_python_tool(tool_def: dict, func):
    name = tool_def["name"]
    description = tool_def.get("description", "") or (func.__doc__ or "")
    params = tool_def.get("params", {})

    properties = {}
    required = []
    for pname, pdef in params.items():
        prop = {"type": pdef.get("type", "string")}
        if pdef.get("description"):
            prop["description"] = pdef["description"]
        properties[pname] = prop
        if pdef.get("required", True):
            required.append(pname)

    schema = {"type": "object", "properties": properties, "required": required}

    def make_handler(f):
        async def handler(**kwargs):
            import asyncio
            if asyncio.iscoroutinefunction(f):
                result = await f(**kwargs)
            else:
                result = f(**kwargs)
            if isinstance(result, str):
                return result
            return json.dumps(result, indent=2, default=str)
        handler.__name__ = name
        handler.__doc__ = description
        return handler

    fn = make_handler(func)
    mcp._tool_manager._tools[name] = type("ToolMeta", (), {
        "name": name,
        "description": description,
        "parameters": schema,
        "fn": fn,
    })()


# ── Main ─────────────────────────────────────────────────────────────────────


def setup():
    config = _load_config()
    adapter_type = config.get("type", "rest-api")

    if adapter_type == "rest-api":
        _register_rest_tools(config)
    elif adapter_type == "langchain":
        _register_langchain_tools(config)
    elif adapter_type == "python":
        _register_python_tools(config)
    else:
        raise RuntimeError(f"Unknown adapter type: {adapter_type!r}")


if os.environ.get("COOPERAGE_ADAPTER_CONFIG"):
    setup()

if __name__ == "__main__":
    if not os.environ.get("COOPERAGE_ADAPTER_CONFIG"):
        raise RuntimeError("COOPERAGE_ADAPTER_CONFIG env var not set")
    serve(mcp)
