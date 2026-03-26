"""
Gateway dispatch tests — registry, session manager, and httpx are all mocked.
Tests cover each tool's logic independently.
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from cooperage.core.models import ContainerInfo, ServerDef, Session


def _session(name=None) -> Session:
    return Session(
        name=name,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def _info(host_port=9001) -> ContainerInfo:
    return ContainerInfo(
        container_id="c1",
        server_name="sim",
        session_id="s1",
        host_port=host_port,
    )


# ── cooperage_list_servers ──────────────────────────────────────────────────────

@patch("cooperage.gateway.server.orch.image_exists", return_value=True)
@patch("cooperage.gateway.server.registry.load")
def test_list_servers_returns_names_and_descriptions(mock_load, mock_exists):
    mock_load.return_value = [
        ServerDef(name="sim", image="sim:latest", description="Sim runner"),
        ServerDef(name="cfd", image="cfd:latest", description="CFD solver"),
    ]
    from cooperage.gateway.server import _list_servers
    result = _list_servers()
    assert len(result) == 2
    assert result[0]["name"] == "sim"
    assert result[1]["description"] == "CFD solver"


@patch("cooperage.gateway.server.orch.image_exists", return_value=False)
@patch("cooperage.gateway.server.registry.load")
def test_list_servers_includes_cached_flag(mock_load, mock_exists):
    mock_load.return_value = [ServerDef(name="sim", image="sim:latest")]
    from cooperage.gateway.server import _list_servers
    result = _list_servers()
    assert result[0]["cached"] is False


@patch("cooperage.gateway.server.orch.image_exists", return_value=True)
@patch("cooperage.gateway.server.registry.load", return_value=[])
def test_list_servers_empty(mock_load, mock_exists):
    from cooperage.gateway.server import _list_servers
    assert _list_servers() == []


# ── cooperage_pull_server ───────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("cooperage.gateway.server.orch.pull_image", return_value="sha256:abc123")
@patch("cooperage.gateway.server.registry.get")
async def test_pull_server_success(mock_get, mock_pull):
    mock_get.return_value = ServerDef(name="sim", image="sim:latest")
    from cooperage.gateway.server import _pull_server
    result = await _pull_server("sim")
    assert result["server"] == "sim"
    assert result["image_id"] == "sha256:abc123"
    mock_pull.assert_called_once_with("sim:latest")


@pytest.mark.asyncio
@patch("cooperage.gateway.server.registry.get", return_value=None)
async def test_pull_server_unknown_raises(mock_get):
    from cooperage.gateway.server import _pull_server
    with pytest.raises(ValueError, match="No server named"):
        await _pull_server("ghost")


# ── cooperage_create_session ────────────────────────────────────────────────────

@patch("cooperage.gateway.server.sessions.create_session")
def test_create_session_returns_expected_keys(mock_create):
    session = _session(name="test-run")
    mock_create.return_value = session

    from cooperage.gateway.server import _create_session
    result = _create_session("test-run")

    assert result["session_id"] == session.id
    assert result["name"] == "test-run"
    assert result["volume"] == session.volume_name
    assert "expires_at" in result
    mock_create.assert_called_once_with(name="test-run")


# ── cooperage_end_session ───────────────────────────────────────────────────────

@patch("cooperage.gateway.server.sessions.end_session", return_value=True)
def test_end_session_success(mock_end):
    from cooperage.gateway.server import _end_session
    result = _end_session("abc123")
    assert result["ended"] is True
    assert result["session_id"] == "abc123"
    mock_end.assert_called_once_with("abc123")


@patch("cooperage.gateway.server.sessions.end_session", return_value=False)
def test_end_session_unknown(mock_end):
    from cooperage.gateway.server import _end_session
    result = _end_session("nosuchid")
    assert result["ended"] is False


# ── cooperage_list_tools ────────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("cooperage.gateway.server._ensure_container")
@patch("cooperage.gateway.server.httpx.AsyncClient")
async def test_proxy_list_tools(mock_httpx_cls, mock_ensure):
    info = _info()
    mock_ensure.return_value = info

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"tools": [{"name": "run_sim", "description": "Run simulation"}]},
    }
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post.return_value = mock_resp
    mock_httpx_cls.return_value = mock_client

    from cooperage.gateway.server import _proxy_list_tools
    tools = await _proxy_list_tools("session1", "sim")

    assert len(tools) == 1
    assert tools[0]["name"] == "run_sim"


@pytest.mark.asyncio
@patch("cooperage.gateway.server.registry.get", return_value=None)
async def test_ensure_container_raises_for_unknown_server(mock_get):
    from cooperage.gateway.server import _ensure_container
    with pytest.raises(ValueError, match="No server named"):
        await _ensure_container("session1", "ghost")


# ── cooperage_call_tool ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("cooperage.gateway.server._ensure_container")
@patch("cooperage.gateway.server.httpx.AsyncClient")
async def test_proxy_call_tool_returns_text(mock_httpx_cls, mock_ensure):
    info = _info()
    mock_ensure.return_value = info

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [{"type": "text", "text": "mean=2.0"}]
        },
    }
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post.return_value = mock_resp
    mock_httpx_cls.return_value = mock_client

    from cooperage.gateway.server import _proxy_call_tool
    result = await _proxy_call_tool("s1", "sim", "run_sim", {"input": "x"})

    assert result == "mean=2.0"


@pytest.mark.asyncio
@patch("cooperage.gateway.server._ensure_container")
@patch("cooperage.gateway.server.httpx.AsyncClient")
async def test_proxy_call_tool_raises_on_mcp_error(mock_httpx_cls, mock_ensure):
    info = _info()
    mock_ensure.return_value = info

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32000, "message": "tool crashed"},
    }
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post.return_value = mock_resp
    mock_httpx_cls.return_value = mock_client

    from cooperage.gateway.server import _proxy_call_tool
    with pytest.raises(RuntimeError, match="tool crashed"):
        await _proxy_call_tool("s1", "sim", "run_sim", {})


# ── top-level dispatch ────────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("cooperage.gateway.server.registry.load", return_value=[])
async def test_dispatch_unknown_tool_raises(mock_load):
    from cooperage.gateway.server import _dispatch
    with pytest.raises(ValueError, match="Unknown tool"):
        await _dispatch("cooperage_does_not_exist", {})
