"""
Cooperage Audit — Plugin Interface

The open-source core defines the AuditEvent schema and emits events at
key lifecycle points. By default, events are silently discarded.

The cooperage-enterprise package can register an audit sink (file,
S3, SIEM, etc.) via register_audit_sink().
"""

import logging
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AuditEventType(str, Enum):
    TOOL_CALL = "tool_call"
    SESSION_CREATE = "session_create"
    SESSION_END = "session_end"
    CONTAINER_START = "container_start"
    CONTAINER_STOP = "container_stop"
    WORKSPACE_WRITE = "workspace_write"
    JOB_START = "job_start"
    JOB_COMPLETE = "job_complete"
    JOB_FAIL = "job_fail"
    JOB_CANCEL = "job_cancel"


class AuditEvent(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: AuditEventType
    session_id: str | None = None
    tenant_id: str = "default"
    server_name: str | None = None
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    result_summary: str | None = None
    duration_ms: float | None = None
    error: str | None = None
    metadata: dict[str, Any] | None = None


# ── Plugin protocol ──────────────────────────────────────────────────────────

@runtime_checkable
class AuditSink(Protocol):
    """Interface for enterprise audit log backends."""

    def init(self) -> None:
        """Initialize the sink (open files, connect to services, etc.)."""
        ...

    def emit(self, event: AuditEvent) -> None:
        """Write an audit event."""
        ...


# ── Default (no-op) sink ─────────────────────────────────────────────────────

class _NoOpSink:
    def init(self) -> None:
        pass

    def emit(self, event: AuditEvent) -> None:
        pass  # silently discard


_sink: AuditSink | _NoOpSink = _NoOpSink()


def register_audit_sink(sink: AuditSink) -> None:
    """Register an enterprise audit sink."""
    global _sink
    _sink = sink
    logger.info("Enterprise audit sink registered: %s", type(sink).__name__)


# ── Public API (unchanged call sites in gateway) ─────────────────────────────

def init(path=None) -> None:
    """Initialize audit. In open core this is a no-op.
    Enterprise providers call register_audit_sink() instead."""
    _sink.init()


def emit(event: AuditEvent) -> None:
    _sink.emit(event)


def measure() -> float:
    return time.monotonic()


def elapsed_ms(start: float) -> float:
    return round((time.monotonic() - start) * 1000, 2)
