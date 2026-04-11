"""
Session manager tests — orchestrator calls are mocked so no Docker daemon needed.
Global module state is reset before each test via the `clean_sessions` fixture.
"""
from unittest.mock import MagicMock, patch
import pytest

import cooperage.session.manager as mgr
from cooperage.core.models import ContainerInfo, ServerDef


@pytest.fixture(autouse=True)
def clean_sessions():
    """Reset in-memory session state between tests."""
    mgr._sessions.clear()
    mgr._containers.clear()
    yield
    mgr._sessions.clear()
    mgr._containers.clear()


def _server_def(name="sim") -> ServerDef:
    return ServerDef(name=name, image=f"{name}:latest")


def _container_info(server_name="sim", session_id="s1") -> ContainerInfo:
    return ContainerInfo(
        container_id="c123",
        server_name=server_name,
        session_id=session_id,
        host_port=9001,
    )


def _mock_orch():
    orch = MagicMock()
    orch.wait_until_ready.return_value = True
    return orch


# ── create_session ────────────────────────────────────────────────────────────

@patch("cooperage.session.manager.get_orchestrator")
def test_create_session_returns_session(mock_get_orch):
    mock_get_orch.return_value = _mock_orch()
    session = mgr.create_session()
    assert session.id
    assert session.volume_name == f"cooperage-session-{session.id}"
    mock_get_orch.return_value.create_volume.assert_called_once_with(session.volume_name)


@patch("cooperage.session.manager.get_orchestrator")
def test_create_session_with_name(mock_get_orch):
    mock_get_orch.return_value = _mock_orch()
    session = mgr.create_session(name="my-run")
    assert session.name == "my-run"


@patch("cooperage.session.manager.get_orchestrator")
def test_create_session_is_retrievable(mock_get_orch):
    mock_get_orch.return_value = _mock_orch()
    session = mgr.create_session()
    assert mgr.get_session(session.id) is not None


@patch("cooperage.session.manager.get_orchestrator")
def test_create_session_sets_expiry(mock_get_orch, monkeypatch):
    mock_get_orch.return_value = _mock_orch()
    monkeypatch.setattr("cooperage.session.manager.settings.session_ttl_seconds", 300)
    session = mgr.create_session()
    delta = session.expires_at - session.created_at
    assert 299 <= delta.total_seconds() <= 301


# ── get / list sessions ───────────────────────────────────────────────────────

def test_get_session_returns_none_for_unknown():
    assert mgr.get_session("nosuchid") is None


@patch("cooperage.session.manager.get_orchestrator")
def test_list_sessions(mock_get_orch):
    mock_get_orch.return_value = _mock_orch()
    mgr.create_session()
    mgr.create_session()
    assert len(mgr.list_sessions()) == 2


# ── end_session ───────────────────────────────────────────────────────────────

@patch("cooperage.session.manager.get_orchestrator")
def test_end_session_stops_containers_and_removes_volume(mock_get_orch):
    orch = _mock_orch()
    mock_get_orch.return_value = orch
    session = mgr.create_session()
    info = _container_info(session_id=session.id)
    mgr._containers[session.id]["sim"] = info
    mgr._sessions[session.id].containers["sim"] = info.container_id

    ok = mgr.end_session(session.id)

    assert ok is True
    orch.stop_container.assert_called_once_with(info.container_id)
    orch.remove_volume.assert_called_once_with(session.volume_name)
    assert mgr.get_session(session.id) is None


@patch("cooperage.session.manager.get_orchestrator")
def test_end_session_returns_false_for_unknown(mock_get_orch):
    mock_get_orch.return_value = _mock_orch()
    assert mgr.end_session("nosuchid") is False
    mock_get_orch.return_value.stop_container.assert_not_called()


# ── get_or_start_container ────────────────────────────────────────────────────

@patch("cooperage.session.manager.get_orchestrator")
def test_get_or_start_container_starts_new(mock_get_orch):
    orch = _mock_orch()
    mock_get_orch.return_value = orch
    session = mgr.create_session()
    server = _server_def()
    expected_info = _container_info(session_id=session.id)
    orch.start_container.return_value = expected_info

    info = mgr.get_or_start_container(session.id, server)

    assert info.container_id == "c123"
    orch.start_container.assert_called_once_with(server, mgr._sessions[session.id])


@patch("cooperage.session.manager.get_orchestrator")
def test_get_or_start_container_reuses_existing(mock_get_orch):
    orch = _mock_orch()
    mock_get_orch.return_value = orch
    session = mgr.create_session()
    server = _server_def()
    info = _container_info(session_id=session.id)
    mgr._containers[session.id]["sim"] = info

    result = mgr.get_or_start_container(session.id, server)

    assert result is info
    orch.start_container.assert_not_called()


@patch("cooperage.session.manager.get_orchestrator")
def test_get_or_start_container_raises_if_not_ready(mock_get_orch):
    orch = _mock_orch()
    orch.wait_until_ready.return_value = False
    orch.get_container_logs.return_value = "error log"
    mock_get_orch.return_value = orch
    session = mgr.create_session()
    server = _server_def()
    orch.start_container.return_value = _container_info(session_id=session.id)

    from cooperage.core.errors import ContainerStartupError
    with pytest.raises(ContainerStartupError, match="failed to start"):
        mgr.get_or_start_container(session.id, server)

    orch.stop_container.assert_called_once()


def test_get_or_start_container_raises_for_unknown_session():
    from cooperage.core.errors import SessionNotFoundError
    with pytest.raises(SessionNotFoundError, match="not found"):
        mgr.get_or_start_container("nosuchid", _server_def())


# ── count_sessions_for_tenant (quota support) ────────────────────────────────


@patch("cooperage.session.manager.get_orchestrator")
def test_count_sessions_for_tenant(mock_get_orch):
    mock_get_orch.return_value = _mock_orch()
    mgr.create_session(tenant_id="alpha")
    mgr.create_session(tenant_id="alpha")
    mgr.create_session(tenant_id="beta")
    assert mgr.count_sessions_for_tenant("alpha") == 2
    assert mgr.count_sessions_for_tenant("beta") == 1
    assert mgr.count_sessions_for_tenant("unknown") == 0


@patch("cooperage.session.manager.get_orchestrator")
def test_count_excludes_expired_sessions(mock_get_orch):
    """Expired sessions should not count against a tenant's quota."""
    from datetime import datetime, timedelta, timezone

    mock_get_orch.return_value = _mock_orch()
    s1 = mgr.create_session(tenant_id="alpha")
    s2 = mgr.create_session(tenant_id="alpha")

    # Expire one session by backdating its expiry
    with mgr._lock:
        mgr._sessions[s1.id].expires_at = datetime.now(timezone.utc) - timedelta(seconds=60)

    assert mgr.count_sessions_for_tenant("alpha") == 1


@patch("cooperage.session.manager.get_orchestrator")
def test_count_all_expired_returns_zero(mock_get_orch):
    """If all sessions are expired, count should be zero."""
    from datetime import datetime, timedelta, timezone

    mock_get_orch.return_value = _mock_orch()
    s1 = mgr.create_session(tenant_id="alpha")
    s2 = mgr.create_session(tenant_id="alpha")

    with mgr._lock:
        past = datetime.now(timezone.utc) - timedelta(seconds=60)
        mgr._sessions[s1.id].expires_at = past
        mgr._sessions[s2.id].expires_at = past

    assert mgr.count_sessions_for_tenant("alpha") == 0


# ── reap_expired_sessions ────────────────────────────────────────────────────


@patch("cooperage.session.manager.get_orchestrator")
def test_reap_removes_expired_sessions(mock_get_orch):
    """Expired sessions are removed by reap_expired_sessions."""
    from datetime import datetime, timedelta, timezone

    orch = _mock_orch()
    mock_get_orch.return_value = orch
    s1 = mgr.create_session(tenant_id="alpha")
    s2 = mgr.create_session(tenant_id="alpha")

    # Expire s1 only
    with mgr._lock:
        mgr._sessions[s1.id].expires_at = datetime.now(timezone.utc) - timedelta(seconds=60)

    reaped = mgr.reap_expired_sessions()
    assert s1.id in reaped
    assert s2.id not in reaped
    assert mgr.get_session(s1.id) is None
    assert mgr.get_session(s2.id) is not None


@patch("cooperage.session.manager.get_orchestrator")
def test_reap_returns_empty_when_nothing_expired(mock_get_orch):
    """No sessions expired means nothing to reap."""
    mock_get_orch.return_value = _mock_orch()
    mgr.create_session(tenant_id="alpha")

    reaped = mgr.reap_expired_sessions()
    assert reaped == []
