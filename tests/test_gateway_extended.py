"""
Extended gateway tests — covers MCP resources, ui_url, and repo_url in list_servers.
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cooperage.core.auth import AuthContext
from cooperage.core.errors import SessionNotFoundError
from cooperage.core.models import ServerDef, Session

_DEFAULT_AUTH = AuthContext(tenant_id="default")


def _session(tenant_id="default", name=None) -> Session:
    return Session(
        name=name,
        tenant_id=tenant_id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def _set_auth(auth: AuthContext):
    from cooperage.gateway.server import _auth_ctx
    return _auth_ctx.set(auth)


def _reset_auth(token):
    from cooperage.gateway.server import _auth_ctx
    _auth_ctx.reset(token)


# ── list_servers with repo_url ────────────────────────────────────────────────

@patch("cooperage.gateway.server.get_orchestrator")
@patch("cooperage.gateway.server.registry.load")
def test_list_servers_includes_repo_url(mock_load, mock_get_orch):
    mock_get_orch.return_value = MagicMock(image_exists=MagicMock(return_value=True))
    mock_load.return_value = [
        ServerDef(name="sim", image="sim:latest", repo_url="https://github.com/myorg/sim"),
    ]
    from cooperage.gateway.server import list_servers
    result = list_servers(auth=_DEFAULT_AUTH)
    assert result[0]["repo_url"] == "https://github.com/myorg/sim"


@patch("cooperage.gateway.server.get_orchestrator")
@patch("cooperage.gateway.server.registry.load")
def test_list_servers_omits_repo_url_when_not_set(mock_load, mock_get_orch):
    mock_get_orch.return_value = MagicMock(image_exists=MagicMock(return_value=True))
    mock_load.return_value = [ServerDef(name="sim", image="sim:latest")]
    from cooperage.gateway.server import list_servers
    result = list_servers(auth=_DEFAULT_AUTH)
    assert "repo_url" not in result[0]


# ── create_session ui_url ─────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("cooperage.gateway.server._warmup_builtin", new_callable=AsyncMock)
@patch("cooperage.gateway.server.sessions.create_session")
async def test_create_session_includes_ui_url(mock_create, mock_warmup, monkeypatch):
    session = _session()
    mock_create.return_value = session
    import cooperage.core.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "settings", MagicMock(
        ui_url="http://localhost:8501",
        auth_enabled=False,
        max_sessions=None,
        session_ttl_seconds=1800,
    ))
    from cooperage.gateway.server import create_session
    result = await create_session(auth=_DEFAULT_AUTH)
    assert "http://localhost:8501" in result
    assert session.id in result


@pytest.mark.asyncio
@patch("cooperage.gateway.server._warmup_builtin", new_callable=AsyncMock)
@patch("cooperage.gateway.server.sessions.create_session")
async def test_create_session_no_ui_url_when_not_configured(mock_create, mock_warmup, monkeypatch):
    session = _session()
    mock_create.return_value = session
    import cooperage.core.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "settings", MagicMock(
        ui_url=None,
        auth_enabled=False,
        max_sessions=None,
        session_ttl_seconds=1800,
    ))
    from cooperage.gateway.server import create_session
    result = await create_session(auth=_DEFAULT_AUTH)
    assert "ui_url" not in result


# ── MCP resources ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("cooperage.gateway.server.sessions.list_sessions", return_value=[])
@patch("cooperage.gateway.server.get_orchestrator")
@patch("cooperage.gateway.server.registry.load", return_value=[])
async def test_handle_list_resources_returns_base_resources(mock_load, mock_orch, mock_list):
    from cooperage.gateway.server import handle_list_resources
    token = _set_auth(_DEFAULT_AUTH)
    try:
        resources = await handle_list_resources()
    finally:
        _reset_auth(token)
    uris = [str(r.uri) for r in resources]
    assert "cooperage://registry/servers" in uris
    assert "cooperage://sessions" in uris


@pytest.mark.asyncio
@patch("cooperage.gateway.server.sessions.list_sessions")
@patch("cooperage.gateway.server.get_orchestrator")
@patch("cooperage.gateway.server.registry.load", return_value=[])
async def test_handle_list_resources_includes_session_workspaces(mock_load, mock_orch, mock_list):
    s = _session(name="my-run")
    mock_list.return_value = [s]
    from cooperage.gateway.server import handle_list_resources
    token = _set_auth(_DEFAULT_AUTH)
    try:
        resources = await handle_list_resources()
    finally:
        _reset_auth(token)
    uris = [str(r.uri) for r in resources]
    assert f"cooperage://sessions/{s.id}/workspace" in uris


@pytest.mark.asyncio
@patch("cooperage.gateway.server.get_orchestrator")
@patch("cooperage.gateway.server.registry.load", return_value=[])
async def test_handle_read_resource_registry(mock_load, mock_orch):
    from cooperage.gateway.server import handle_read_resource
    token = _set_auth(_DEFAULT_AUTH)
    try:
        result = await handle_read_resource("cooperage://registry/servers")
    finally:
        _reset_auth(token)
    assert json.loads(result) == []


@pytest.mark.asyncio
@patch("cooperage.gateway.server.sessions.list_sessions", return_value=[])
async def test_handle_read_resource_sessions(mock_list):
    from cooperage.gateway.server import handle_read_resource
    token = _set_auth(_DEFAULT_AUTH)
    try:
        result = await handle_read_resource("cooperage://sessions")
    finally:
        _reset_auth(token)
    assert json.loads(result) == []


@pytest.mark.asyncio
@patch("cooperage.gateway.server._workspace_op")
@patch("cooperage.gateway.server._check_session_tenant")
async def test_handle_read_resource_workspace(mock_check, mock_op):
    mock_op.return_value = ["file.txt"]
    from cooperage.gateway.server import handle_read_resource
    token = _set_auth(_DEFAULT_AUTH)
    try:
        result = await handle_read_resource("cooperage://sessions/abc123/workspace")
    finally:
        _reset_auth(token)
    mock_op.assert_called_once_with("abc123", "workspace_list", {})
    assert "file.txt" in result


@pytest.mark.asyncio
async def test_handle_read_resource_unknown_raises():
    from cooperage.gateway.server import handle_read_resource
    token = _set_auth(_DEFAULT_AUTH)
    try:
        with pytest.raises(ValueError, match="Unknown resource"):
            await handle_read_resource("cooperage://unknown/path")
    finally:
        _reset_auth(token)


# ── list_sessions_tool ───────────────────────────────────────────────────────

@patch("cooperage.gateway.server.sessions.list_sessions")
def test_list_sessions_tool_default_tenant_shows_all(mock_list):
    mock_list.return_value = []
    from cooperage.gateway.server import list_sessions_tool
    list_sessions_tool(auth=_DEFAULT_AUTH)
    mock_list.assert_called_once_with(tenant_id=None)


# ── _check_session_tenant isolation ─────────────────────────────────────────


@patch("cooperage.gateway.server.sessions.get_session")
def test_tenant_a_cannot_access_tenant_b_session(mock_get):
    """Tenant A must not be able to access a session owned by tenant B."""
    mock_get.return_value = _session(tenant_id="tenant-b")
    from cooperage.gateway.server import _check_session_tenant
    auth_a = AuthContext(tenant_id="tenant-a")
    with pytest.raises(PermissionError, match="different tenant"):
        _check_session_tenant("some-session-id", auth_a)


@patch("cooperage.gateway.server.sessions.get_session")
def test_default_tenant_can_access_any_session(mock_get):
    """The 'default' tenant should be able to access sessions from any tenant."""
    mock_get.return_value = _session(tenant_id="tenant-x")
    from cooperage.gateway.server import _check_session_tenant
    # Should not raise
    _check_session_tenant("some-session-id", _DEFAULT_AUTH)


@patch("cooperage.gateway.server.sessions.get_session")
def test_check_session_tenant_raises_for_unknown_session(mock_get):
    """Unknown session IDs should raise SessionNotFoundError."""
    mock_get.return_value = None
    from cooperage.gateway.server import _check_session_tenant
    with pytest.raises(SessionNotFoundError, match="not found"):
        _check_session_tenant("nonexistent-id", _DEFAULT_AUTH)
