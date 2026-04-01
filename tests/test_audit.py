"""Tests for the audit logging module."""
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cooperage.core.audit import (
    AuditEvent,
    AuditEventType,
    elapsed_ms,
    emit,
    init,
    measure,
)
from cooperage.core.auth import AuthContext
from cooperage.core.models import Session


# ── Unit tests for audit module ──────────────────────────────────────────────


def test_audit_event_defaults():
    event = AuditEvent(event_type=AuditEventType.TOOL_CALL)
    assert event.event_type == "tool_call"
    assert event.session_id is None
    assert event.tenant_id == "default"
    assert event.timestamp is not None


def test_measure_and_elapsed():
    start = measure()
    time.sleep(0.01)
    ms = elapsed_ms(start)
    assert ms >= 5  # at least ~10ms but allow for scheduling jitter


def test_emit_writes_jsonl(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    init(log_path)
    event = AuditEvent(
        event_type=AuditEventType.TOOL_CALL,
        session_id="s1",
        tenant_id="acme",
        tool_name="cooperage_list_servers",
        duration_ms=42.5,
    )
    emit(event)
    lines = log_path.read_text().strip().split("\n")
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["event_type"] == "tool_call"
    assert parsed["session_id"] == "s1"
    assert parsed["tenant_id"] == "acme"
    assert parsed["tool_name"] == "cooperage_list_servers"
    assert parsed["duration_ms"] == 42.5


def test_emit_appends_multiple(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    init(log_path)
    for i in range(3):
        emit(AuditEvent(
            event_type=AuditEventType.TOOL_CALL,
            tool_name=f"tool_{i}",
        ))
    lines = log_path.read_text().strip().split("\n")
    assert len(lines) == 3


def test_emit_noop_when_not_initialized():
    """emit() should silently no-op when init() was never called."""
    import cooperage.core.audit as audit_mod
    original = audit_mod._log_path
    audit_mod._log_path = None
    try:
        emit(AuditEvent(event_type=AuditEventType.TOOL_CALL))
    finally:
        audit_mod._log_path = original


def test_emit_creates_parent_dirs(tmp_path):
    log_path = tmp_path / "subdir" / "nested" / "audit.jsonl"
    init(log_path)
    emit(AuditEvent(event_type=AuditEventType.SESSION_CREATE, session_id="s1"))
    assert log_path.exists()
    parsed = json.loads(log_path.read_text().strip())
    assert parsed["event_type"] == "session_create"


def test_all_event_types_serialize():
    for event_type in AuditEventType:
        event = AuditEvent(event_type=event_type)
        data = json.loads(event.model_dump_json())
        assert data["event_type"] == event_type.value


def test_event_with_full_fields():
    event = AuditEvent(
        event_type=AuditEventType.CONTAINER_START,
        session_id="s1",
        tenant_id="acme",
        server_name="image-analyzer",
        tool_name=None,
        arguments=None,
        duration_ms=1234.56,
        error=None,
        metadata={"container_id": "c1", "image": "analyzer:latest"},
    )
    data = json.loads(event.model_dump_json())
    assert data["metadata"]["container_id"] == "c1"
    assert data["duration_ms"] == 1234.56


def test_event_with_error():
    event = AuditEvent(
        event_type=AuditEventType.TOOL_CALL,
        tool_name="cooperage_call_tool",
        error="Connection refused",
    )
    data = json.loads(event.model_dump_json())
    assert data["error"] == "Connection refused"


# ── Integration: audit emitted from _dispatch ────────────────────────────────


def _session_obj(sid="s1"):
    return Session(
        id=sid,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


@pytest.mark.asyncio
@patch("cooperage.gateway.server.audit_emit")
@patch("cooperage.gateway.server.get_orchestrator")
@patch("cooperage.gateway.server.registry")
async def test_dispatch_emits_tool_call_audit(mock_registry, mock_orch, mock_audit_emit):
    mock_registry.load.return_value = []
    mock_orch.return_value = MagicMock(image_exists=MagicMock(return_value=True))

    from cooperage.gateway.server import _dispatch, _auth_ctx
    token = _auth_ctx.set(AuthContext(tenant_id="test-tenant"))
    try:
        await _dispatch("cooperage_list_servers", {})
    finally:
        _auth_ctx.reset(token)

    mock_audit_emit.assert_called_once()
    event = mock_audit_emit.call_args[0][0]
    assert event.event_type == AuditEventType.TOOL_CALL
    assert event.tool_name == "cooperage_list_servers"
    assert event.tenant_id == "test-tenant"
    assert event.duration_ms is not None
    assert event.error is None


@pytest.mark.asyncio
@patch("cooperage.gateway.server.audit_emit")
@patch("cooperage.gateway.server._warmup_builtin", new_callable=AsyncMock)
@patch("cooperage.gateway.server.sessions.create_session")
async def test_create_session_emits_audit(mock_create, mock_warmup, mock_audit_emit):
    session = _session_obj()
    mock_create.return_value = session
    from cooperage.gateway.server import create_session
    await create_session(auth=AuthContext(tenant_id="acme"))

    # Should have at least the session_create event (dispatch also emits tool_call)
    events = [call[0][0] for call in mock_audit_emit.call_args_list]
    session_events = [e for e in events if e.event_type == AuditEventType.SESSION_CREATE]
    assert len(session_events) == 1
    assert session_events[0].session_id == session.id
    assert session_events[0].tenant_id == "acme"


@pytest.mark.asyncio
@patch("cooperage.gateway.server.audit_emit")
async def test_dispatch_unknown_tool_raises(mock_audit_emit):
    """Unknown tools raise before audit — no event emitted."""
    from cooperage.gateway.server import _dispatch, _auth_ctx
    token = _auth_ctx.set(AuthContext(tenant_id="default"))
    try:
        with pytest.raises(ValueError, match="Unknown tool"):
            await _dispatch("nonexistent_tool", {})
    finally:
        _auth_ctx.reset(token)
    mock_audit_emit.assert_not_called()


@pytest.mark.asyncio
@patch("cooperage.gateway.server.audit_emit")
@patch("cooperage.gateway.server.sessions.get_session")
async def test_dispatch_emits_error_on_handler_failure(mock_get_session, mock_audit_emit):
    """Handler errors are captured in the audit event."""
    mock_get_session.return_value = None
    from cooperage.gateway.server import _dispatch, _auth_ctx
    token = _auth_ctx.set(AuthContext(tenant_id="default"))
    try:
        with pytest.raises(ValueError, match="not found"):
            await _dispatch("cooperage_end_session", {"session_id": "nonexistent"})
    finally:
        _auth_ctx.reset(token)

    mock_audit_emit.assert_called_once()
    event = mock_audit_emit.call_args[0][0]
    assert event.error is not None
    assert "not found" in event.error
