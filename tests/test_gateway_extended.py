"""
Extended gateway tests — covers MCP resources, _check_session_tenant,
create_session quota enforcement, ui_url, and repo_url in list_servers.
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cooperage.core.auth import AuthContext
from cooperage.core.models import ContainerInfo, ServerDef, Session

_DEFAULT_AUTH = AuthContext(tenant_id="default")
_TENANT_AUTH = AuthContext(tenant_id="acme")


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


# ── _check_session_tenant ─────────────────────────────────────────────────────

@patch("cooperage.gateway.server.sessions.get_session")
def test_check_session_tenant_default_auth_allows_any(mock_get):
    mock_get.return_value = _session(tenant_id="acme")
    from cooperage.gateway.server import _check_session_tenant
    _check_session_tenant("s1", _DEFAULT_AUTH)  # should not raise


@patch("cooperage.gateway.server.sessions.get_session")
def test_check_session_tenant_matching_tenant_allowed(mock_get):
    mock_get.return_value = _session(tenant_id="acme")
    from cooperage.gateway.server import _check_session_tenant
    _check_session_tenant("s1", _TENANT_AUTH)  # should not raise


@patch("cooperage.gateway.server.sessions.get_session")
def test_check_session_tenant_wrong_tenant_raises(mock_get):
    mock_get.return_value = _session(tenant_id="other")
    from cooperage.gateway.server import _check_session_tenant
    with pytest.raises(PermissionError, match="different tenant"):
        _check_session_tenant("s1", _TENANT_AUTH)


@patch("cooperage.gateway.server.sessions.get_session", return_value=None)
def test_check_session_tenant_missing_session_raises(mock_get):
    from cooperage.gateway.server import _check_session_tenant
    with pytest.raises(ValueError, match="not found"):
        _check_session_tenant("nosuchid", _DEFAULT_AUTH)


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


# ── create_session quota enforcement ─────────────────────────────────────────

@pytest.mark.asyncio
@patch("cooperage.gateway.server._warmup_builtin", new_callable=AsyncMock)
@patch("cooperage.gateway.server.sessions.count_sessions_for_tenant", return_value=5)
@patch("cooperage.gateway.server.sessions.create_session")
async def test_create_session_quota_exceeded_raises(mock_create, mock_count, mock_warmup):
    auth = AuthContext(tenant_id="acme", max_sessions=5)
    from cooperage.gateway.server import create_session
    with pytest.raises(PermissionError, match="session limit"):
        await create_session(auth=auth)
    mock_create.assert_not_called()


@pytest.mark.asyncio
@patch("cooperage.gateway.server._warmup_builtin", new_callable=AsyncMock)
@patch("cooperage.gateway.server.sessions.count_sessions_for_tenant", return_value=4)
@patch("cooperage.gateway.server.sessions.create_session")
async def test_create_session_under_quota_succeeds(mock_create, mock_count, mock_warmup):
    session = _session(tenant_id="acme")
    mock_create.return_value = session
    auth = AuthContext(tenant_id="acme", max_sessions=5)
    from cooperage.gateway.server import create_session
    result = await create_session(auth=auth)
    assert result["session_id"] == session.id


# ── create_session ui_url ─────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("cooperage.gateway.server._warmup_builtin", new_callable=AsyncMock)
@patch("cooperage.gateway.server.sessions.create_session")
async def test_create_session_includes_ui_url(mock_create, mock_warmup, monkeypatch):
    session = _session()
    mock_create.return_value = session
    monkeypatch.setattr("cooperage.gateway.server._settings", MagicMock(
        ui_url="http://localhost:8501",
        max_sessions=None,
    ), raising=False)
    # Patch the settings inside create_session directly
    import cooperage.core.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "settings", MagicMock(
        ui_url="http://localhost:8501",
        auth_enabled=False,
        max_sessions=None,
        session_ttl_seconds=1800,
    ))
    from cooperage.gateway.server import create_session
    result = await create_session(auth=_DEFAULT_AUTH)
    assert "ui_url" in result
    assert session.id in result["ui_url"]


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


# ── list_sessions_tool RBAC ───────────────────────────────────────────────────

@patch("cooperage.gateway.server.sessions.list_sessions")
def test_list_sessions_tool_filters_by_tenant(mock_list):
    s = _session(tenant_id="acme")
    mock_list.return_value = [s]
    from cooperage.gateway.server import list_sessions_tool
    auth = AuthContext(tenant_id="acme")
    result = list_sessions_tool(auth=auth)
    mock_list.assert_called_once_with(tenant_id="acme")


@patch("cooperage.gateway.server.sessions.list_sessions")
def test_list_sessions_tool_default_tenant_shows_all(mock_list):
    mock_list.return_value = []
    from cooperage.gateway.server import list_sessions_tool
    list_sessions_tool(auth=_DEFAULT_AUTH)
    mock_list.assert_called_once_with(tenant_id=None)
