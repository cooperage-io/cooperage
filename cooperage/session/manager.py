import logging
import threading
from datetime import datetime, timedelta, timezone

from cooperage.core.config import settings
from cooperage.core.models import ContainerInfo, Session, ServerDef
from cooperage.orchestrator import get_orchestrator

orch = get_orchestrator()

logger = logging.getLogger(__name__)

# In-memory store: session_id → Session
_sessions: dict[str, Session] = {}
# session_id → {server_name → ContainerInfo}
_containers: dict[str, dict[str, ContainerInfo]] = {}
_lock = threading.Lock()


# ── Session lifecycle ─────────────────────────────────────────────────────────

def create_session(name: str | None = None) -> Session:
    session = Session(
        name=name,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.session_ttl_seconds),
    )
    orch.create_volume(session.volume_name)
    with _lock:
        _sessions[session.id] = session
        _containers[session.id] = {}
    logger.info("Created session %s (volume=%s)", session.id, session.volume_name)
    return session


def get_session(session_id: str) -> Session | None:
    with _lock:
        return _sessions.get(session_id)


def list_sessions() -> list[Session]:
    with _lock:
        return list(_sessions.values())


def end_session(session_id: str) -> bool:
    with _lock:
        session = _sessions.pop(session_id, None)
        container_map = _containers.pop(session_id, {})

    if session is None:
        return False

    for info in container_map.values():
        try:
            orch.stop_container(info.container_id)
        except Exception as e:
            logger.warning("Failed to stop container %s: %s", info.container_id[:12], e)

    try:
        orch.remove_volume(session.volume_name)
    except Exception as e:
        logger.warning("Failed to remove volume %s: %s", session.volume_name, e)

    logger.info("Ended session %s", session_id)
    return True


# ── Container management within a session ────────────────────────────────────

def get_or_start_container(session_id: str, server_def: ServerDef) -> ContainerInfo:
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            raise ValueError(f"Session {session_id!r} not found")
        existing = _containers[session_id].get(server_def.name)

    if existing is not None:
        return existing

    info = orch.start_container(server_def, session)
    ready = orch.wait_until_ready(info)
    if not ready:
        orch.stop_container(info.container_id)
        raise RuntimeError(
            f"Container for server {server_def.name!r} did not become ready "
            f"within {settings.container_startup_timeout}s"
        )

    with _lock:
        if session_id in _sessions:  # session might have expired during startup
            _sessions[session_id].containers[server_def.name] = info.container_id
            _containers[session_id][server_def.name] = info

    return info


def get_container(session_id: str, server_name: str) -> ContainerInfo | None:
    with _lock:
        return _containers.get(session_id, {}).get(server_name)


# ── TTL cleanup background thread ────────────────────────────────────────────

def _cleanup_loop() -> None:
    import time
    while True:
        time.sleep(settings.session_cleanup_interval)
        now = datetime.now(timezone.utc)
        with _lock:
            expired = [sid for sid, s in _sessions.items() if s.expires_at <= now]
        for sid in expired:
            logger.info("Session %s expired, cleaning up", sid)
            end_session(sid)


def start_cleanup_thread() -> None:
    t = threading.Thread(target=_cleanup_loop, daemon=True, name="cooperage-cleanup")
    t.start()
    logger.info("Session cleanup thread started (interval=%ds)", settings.session_cleanup_interval)
