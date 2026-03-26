"""
Session manager tests — orchestrator calls are mocked so no Docker daemon needed.
Global module state is reset before each test via the `clean_sessions` fixture.
"""
from datetime import datetime, timedelta
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


# ── create_session ────────────────────────────────────────────────────────────

@patch("cooperage.session.manager.orch.create_volume")
def test_create_session_returns_session(mock_create_vol):
    session = mgr.create_session()
    assert session.id
    assert session.volume_name == f"cooperage-session-{session.id}"
    mock_create_vol.assert_called_once_with(session.volume_name)


@patch("cooperage.session.manager.orch.create_volume")
def test_create_session_with_name(mock_create_vol):
    session = mgr.create_session(name="my-run")
    assert session.name == "my-run"


@patch("cooperage.session.manager.orch.create_volume")
def test_create_session_is_retrievable(mock_create_vol):
    session = mgr.create_session()
    assert mgr.get_session(session.id) is not None


@patch("cooperage.session.manager.orch.create_volume")
def test_create_session_sets_expiry(mock_create_vol, monkeypatch):
    monkeypatch.setattr("cooperage.session.manager.settings.session_ttl_seconds", 300)
    session = mgr.create_session()
    delta = session.expires_at - session.created_at
    assert 299 <= delta.total_seconds() <= 301


# ── get / list sessions ───────────────────────────────────────────────────────

def test_get_session_returns_none_for_unknown():
    assert mgr.get_session("nosuchid") is None


@patch("cooperage.session.manager.orch.create_volume")
def test_list_sessions(mock_create_vol):
    mgr.create_session()
    mgr.create_session()
    assert len(mgr.list_sessions()) == 2


# ── end_session ───────────────────────────────────────────────────────────────

@patch("cooperage.session.manager.orch.remove_volume")
@patch("cooperage.session.manager.orch.stop_container")
@patch("cooperage.session.manager.orch.create_volume")
def test_end_session_stops_containers_and_removes_volume(
    mock_create_vol, mock_stop, mock_remove_vol
):
    session = mgr.create_session()
    info = _container_info(session_id=session.id)
    mgr._containers[session.id]["sim"] = info
    mgr._sessions[session.id].containers["sim"] = info.container_id

    ok = mgr.end_session(session.id)

    assert ok is True
    mock_stop.assert_called_once_with(info.container_id)
    mock_remove_vol.assert_called_once_with(session.volume_name)
    assert mgr.get_session(session.id) is None


@patch("cooperage.session.manager.orch.remove_volume")
@patch("cooperage.session.manager.orch.stop_container")
@patch("cooperage.session.manager.orch.create_volume")
def test_end_session_returns_false_for_unknown(mock_cv, mock_stop, mock_rv):
    assert mgr.end_session("nosuchid") is False
    mock_stop.assert_not_called()


# ── get_or_start_container ────────────────────────────────────────────────────

@patch("cooperage.session.manager.orch.wait_until_ready", return_value=True)
@patch("cooperage.session.manager.orch.start_container")
@patch("cooperage.session.manager.orch.create_volume")
def test_get_or_start_container_starts_new(mock_cv, mock_start, mock_ready):
    session = mgr.create_session()
    server = _server_def()
    expected_info = _container_info(session_id=session.id)
    mock_start.return_value = expected_info

    info = mgr.get_or_start_container(session.id, server)

    assert info.container_id == "c123"
    mock_start.assert_called_once_with(server, mgr._sessions[session.id])


@patch("cooperage.session.manager.orch.wait_until_ready", return_value=True)
@patch("cooperage.session.manager.orch.start_container")
@patch("cooperage.session.manager.orch.create_volume")
def test_get_or_start_container_reuses_existing(mock_cv, mock_start, mock_ready):
    session = mgr.create_session()
    server = _server_def()
    info = _container_info(session_id=session.id)
    mgr._containers[session.id]["sim"] = info

    result = mgr.get_or_start_container(session.id, server)

    assert result is info
    mock_start.assert_not_called()


@patch("cooperage.session.manager.orch.stop_container")
@patch("cooperage.session.manager.orch.wait_until_ready", return_value=False)
@patch("cooperage.session.manager.orch.start_container")
@patch("cooperage.session.manager.orch.create_volume")
def test_get_or_start_container_raises_if_not_ready(mock_cv, mock_start, mock_ready, mock_stop):
    session = mgr.create_session()
    server = _server_def()
    mock_start.return_value = _container_info(session_id=session.id)

    with pytest.raises(RuntimeError, match="did not become ready"):
        mgr.get_or_start_container(session.id, server)

    mock_stop.assert_called_once()


def test_get_or_start_container_raises_for_unknown_session():
    with pytest.raises(ValueError, match="not found"):
        mgr.get_or_start_container("nosuchid", _server_def())
