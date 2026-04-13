"""
Extended session manager tests — covers file persistence, activity tracking,
idle container cleanup, and cross-process session loading.
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import cooperage.session.manager as mgr
from cooperage.core.models import ContainerInfo, ServerDef


@pytest.fixture(autouse=True)
def clean_sessions(tmp_path, monkeypatch):
    """Reset in-memory state and redirect sessions file to a temp dir."""
    mgr._sessions.clear()
    mgr._containers.clear()
    sessions_file = tmp_path / "sessions.json"
    monkeypatch.setattr("cooperage.session.manager.settings.sessions_path", sessions_file)
    yield
    mgr._sessions.clear()
    mgr._containers.clear()


def _mock_orch():
    orch = MagicMock()
    orch.wait_until_ready.return_value = True
    return orch


def _container_info(server_name="sim", session_id="s1", container_id="c1") -> ContainerInfo:
    return ContainerInfo(
        container_id=container_id,
        server_name=server_name,
        session_id=session_id,
        host_port=9001,
    )


# ── File persistence ──────────────────────────────────────────────────────────

@patch("cooperage.session.manager.get_orchestrator")
def test_session_persisted_to_file(mock_get_orch, tmp_path, monkeypatch):
    monkeypatch.setattr("cooperage.session.manager.settings.sessions_path", tmp_path / "sessions.json")
    mock_get_orch.return_value = _mock_orch()
    session = mgr.create_session(name="my-run")
    data = json.loads((tmp_path / "sessions.json").read_text())
    assert any(e["id"] == session.id for e in data)


@patch("cooperage.session.manager.get_orchestrator")
def test_end_session_removes_from_file(mock_get_orch, tmp_path, monkeypatch):
    monkeypatch.setattr("cooperage.session.manager.settings.sessions_path", tmp_path / "sessions.json")
    mock_get_orch.return_value = _mock_orch()
    session = mgr.create_session()
    mgr.end_session(session.id)
    data = json.loads((tmp_path / "sessions.json").read_text())
    assert not any(e["id"] == session.id for e in data)


@patch("cooperage.session.manager.get_orchestrator")
def test_get_session_loads_from_file_when_not_in_memory(mock_get_orch, tmp_path, monkeypatch):
    monkeypatch.setattr("cooperage.session.manager.settings.sessions_path", tmp_path / "sessions.json")
    mock_get_orch.return_value = _mock_orch()
    session = mgr.create_session()
    # Evict from memory to simulate another process
    mgr._sessions.clear()
    mgr._containers.clear()
    found = mgr.get_session(session.id)
    assert found is not None
    assert found.id == session.id


@patch("cooperage.session.manager.get_orchestrator")
def test_list_sessions_evicts_deleted_sessions(mock_get_orch, tmp_path, monkeypatch):
    """Sessions removed from the file by another process are evicted from memory."""
    monkeypatch.setattr("cooperage.session.manager.settings.sessions_path", tmp_path / "sessions.json")
    mock_get_orch.return_value = _mock_orch()
    s1 = mgr.create_session()
    s2 = mgr.create_session()
    # Simulate another process deleting s1 from the file
    data = json.loads((tmp_path / "sessions.json").read_text())
    data = [e for e in data if e["id"] != s1.id]
    (tmp_path / "sessions.json").write_text(json.dumps(data))
    sessions = mgr.list_sessions()
    ids = [s.id for s in sessions]
    assert s1.id not in ids
    assert s2.id in ids


@patch("cooperage.session.manager.get_orchestrator")
def test_container_info_persisted_with_session(mock_get_orch, tmp_path, monkeypatch):
    monkeypatch.setattr("cooperage.session.manager.settings.sessions_path", tmp_path / "sessions.json")
    orch = _mock_orch()
    mock_get_orch.return_value = orch
    session = mgr.create_session()
    info = _container_info(session_id=session.id)
    orch.start_container.return_value = info
    mgr.get_or_start_container(session.id, ServerDef(name="sim", image="sim:latest"))
    data = json.loads((tmp_path / "sessions.json").read_text())
    entry = next(e for e in data if e["id"] == session.id)
    assert "sim" in entry["_containers"]


# ── Activity tracking ─────────────────────────────────────────────────────────

@patch("cooperage.session.manager.get_orchestrator")
def test_touch_container_updates_last_activity(mock_get_orch):
    mock_get_orch.return_value = _mock_orch()
    session = mgr.create_session()
    info = _container_info(session_id=session.id)
    mgr._containers[session.id]["sim"] = info
    before = info.last_activity
    mgr.touch_container(session.id, "sim")
    assert info.last_activity >= before


def test_touch_container_unknown_session_is_noop():
    mgr.touch_container("nosuchid", "sim")  # should not raise


@patch("cooperage.session.manager.get_orchestrator")
def test_touch_session_extends_expiry(mock_get_orch, monkeypatch):
    monkeypatch.setattr("cooperage.session.manager.settings.session_extend_on_activity", True)
    monkeypatch.setattr("cooperage.session.manager.settings.session_ttl_seconds", 3600)
    mock_get_orch.return_value = _mock_orch()
    session = mgr.create_session()
    original_expiry = session.expires_at
    mgr.touch_session(session.id)
    assert mgr._sessions[session.id].expires_at > original_expiry


@patch("cooperage.session.manager.get_orchestrator")
def test_touch_session_disabled_does_not_extend(mock_get_orch, monkeypatch):
    monkeypatch.setattr("cooperage.session.manager.settings.session_extend_on_activity", False)
    mock_get_orch.return_value = _mock_orch()
    session = mgr.create_session()
    original_expiry = session.expires_at
    mgr.touch_session(session.id)
    assert mgr._sessions[session.id].expires_at == original_expiry


@patch("cooperage.session.manager.get_orchestrator")
def test_touch_session_never_shortens_expiry(mock_get_orch, monkeypatch):
    """If expiry was manually extended beyond the default TTL, touch should not shorten it."""
    from datetime import timedelta, timezone, datetime
    monkeypatch.setattr("cooperage.session.manager.settings.session_extend_on_activity", True)
    monkeypatch.setattr("cooperage.session.manager.settings.session_ttl_seconds", 1800)
    mock_get_orch.return_value = _mock_orch()
    session = mgr.create_session()
    # Manually set expiry to 12 hours from now
    far_future = datetime.now(timezone.utc) + timedelta(hours=12)
    mgr._sessions[session.id].expires_at = far_future
    mgr.touch_session(session.id)
    # Should still be 12 hours, not reset to 30 minutes
    assert mgr._sessions[session.id].expires_at == far_future


# ── Idle container cleanup ────────────────────────────────────────────────────

@patch("cooperage.session.manager.get_orchestrator")
def test_cleanup_idle_containers_stops_stale(mock_get_orch, monkeypatch):
    monkeypatch.setattr("cooperage.session.manager.settings.container_idle_timeout", 60)
    orch = _mock_orch()
    mock_get_orch.return_value = orch
    session = mgr.create_session()
    info = _container_info(session_id=session.id)
    # Set last_activity far in the past
    info.last_activity = datetime.now(timezone.utc) - timedelta(seconds=120)
    mgr._containers[session.id]["sim"] = info
    mgr._sessions[session.id].containers["sim"] = info.container_id

    mgr._cleanup_idle_containers()

    orch.stop_container.assert_called_once_with(info.container_id)
    assert "sim" not in mgr._containers[session.id]


@patch("cooperage.session.manager.get_orchestrator")
def test_cleanup_idle_containers_leaves_active(mock_get_orch, monkeypatch):
    monkeypatch.setattr("cooperage.session.manager.settings.container_idle_timeout", 60)
    orch = _mock_orch()
    mock_get_orch.return_value = orch
    session = mgr.create_session()
    info = _container_info(session_id=session.id)
    # Recently active
    info.last_activity = datetime.now(timezone.utc)
    mgr._containers[session.id]["sim"] = info

    mgr._cleanup_idle_containers()

    orch.stop_container.assert_not_called()
    assert "sim" in mgr._containers[session.id]


@patch("cooperage.session.manager.get_orchestrator")
def test_cleanup_idle_containers_disabled_when_timeout_zero(mock_get_orch, monkeypatch):
    monkeypatch.setattr("cooperage.session.manager.settings.container_idle_timeout", 0)
    orch = _mock_orch()
    mock_get_orch.return_value = orch
    session = mgr.create_session()
    info = _container_info(session_id=session.id)
    info.last_activity = datetime.now(timezone.utc) - timedelta(seconds=9999)
    mgr._containers[session.id]["sim"] = info

    mgr._cleanup_idle_containers()

    orch.stop_container.assert_not_called()


# ── count_sessions_for_tenant ─────────────────────────────────────────────────

@patch("cooperage.session.manager.get_orchestrator")
def test_count_sessions_for_tenant(mock_get_orch, tmp_path, monkeypatch):
    monkeypatch.setattr("cooperage.session.manager.settings.sessions_path", tmp_path / "sessions.json")
    mock_get_orch.return_value = _mock_orch()
    mgr.create_session(tenant_id="acme")
    mgr.create_session(tenant_id="acme")
    mgr.create_session(tenant_id="other")
    assert mgr.count_sessions_for_tenant("acme") == 2
    assert mgr.count_sessions_for_tenant("other") == 1
    assert mgr.count_sessions_for_tenant("nobody") == 0


# ── get_container ─────────────────────────────────────────────────────────────

@patch("cooperage.session.manager.get_orchestrator")
def test_get_container_returns_info(mock_get_orch):
    mock_get_orch.return_value = _mock_orch()
    session = mgr.create_session()
    info = _container_info(session_id=session.id)
    mgr._containers[session.id]["sim"] = info
    assert mgr.get_container(session.id, "sim") is info


def test_get_container_returns_none_for_unknown():
    assert mgr.get_container("nosuchid", "sim") is None


# ── Fix #21: _cleanup_loop error handling ────────────────────────────────────


@patch("cooperage.session.manager.get_orchestrator")
@patch("cooperage.session.manager.reap_expired_sessions", side_effect=Exception("db error"))
def test_cleanup_loop_catches_reap_exception(mock_reap, mock_get_orch, monkeypatch):
    """_cleanup_loop catches exceptions from reap_expired_sessions and continues."""
    import time
    mock_get_orch.return_value = _mock_orch()
    monkeypatch.setattr("cooperage.session.manager.settings.session_cleanup_interval", 0.01)

    call_count = 0
    original_sleep = time.sleep

    def counting_sleep(secs):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise KeyboardInterrupt("stop loop")
        original_sleep(secs)

    monkeypatch.setattr("time.sleep", counting_sleep)

    # _cleanup_loop should not propagate the reap exception;
    # it should be caught and the loop continues until our KeyboardInterrupt stops it
    with pytest.raises(KeyboardInterrupt):
        mgr._cleanup_loop()
