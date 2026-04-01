"""Structured audit logging for Cooperage.

Appends JSON-lines to a configurable audit log file. Each line is a
self-contained AuditEvent that records tool calls, session lifecycle,
container lifecycle, and workspace operations with full context.
"""

import logging
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AuditEventType(str, Enum):
    TOOL_CALL = "tool_call"
    SESSION_CREATE = "session_create"
    SESSION_END = "session_end"
    CONTAINER_START = "container_start"
    CONTAINER_STOP = "container_stop"
    WORKSPACE_WRITE = "workspace_write"


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


_log_path: Path | None = None


def init(path: Path | None) -> None:
    """Initialize the audit log path. Called once at gateway startup."""
    global _log_path
    if path is None:
        return
    _log_path = path
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Audit log enabled: %s", _log_path)


def emit(event: AuditEvent) -> None:
    """Append an audit event to the log file."""
    if _log_path is None:
        return
    try:
        line = event.model_dump_json() + "\n"
        with open(_log_path, "a") as f:
            f.write(line)
    except Exception as e:
        logger.warning("Failed to write audit event: %s", e)


def measure() -> float:
    """Return a monotonic timestamp for measuring durations."""
    return time.monotonic()


def elapsed_ms(start: float) -> float:
    """Return elapsed milliseconds since a measure() call."""
    return round((time.monotonic() - start) * 1000, 2)
