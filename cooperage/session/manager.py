import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


# ── File persistence ──────────────────────────────────────────────────────────

def _sessions_path() -> Path:
    return settings.sessions_path


def _save() -> None:
    path = _sessions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = []
    for session in _sessions.values():
        entry = session.model_dump(mode="json")
        entry["_containers"] = {
            name: info.model_dump()
            for name, info in _containers.get(session.id, {}).items()
        }
        data.append(entry)
    path.write_text(json.dumps(data, indent=2, default=str))


def _load_from_file() -> None:
    path = _sessions_path()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
        for entry in data:
            container_data = entry.pop("_containers", {})
            session = Session(**entry)
            _sessions[session.id] = session
            _containers[session.id] = {
                name: ContainerInfo(**info)
                for name, info in container_data.items()
            }
    except Exception as e:
        logger.warning("Could not load sessions from %s: %s", path, e)


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
        _save()
    logger.info("Created session %s (volume=%s)", session.id, session.volume_name)
    return session


def get_session(session_id: str) -> Session | None:
    with _lock:
        session = _sessions.get(session_id)
        if session is not None:
            return session

    # Not in memory — check file (session may have been created by another gateway process)
    path = _sessions_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        for entry in data:
            if entry["id"] == session_id:
                container_data = {k: v for k, v in entry.items() if k == "_containers"}
                containers = {
                    name: ContainerInfo(**info)
                    for name, info in entry.get("_containers", {}).items()
                }
                session = Session(**{k: v for k, v in entry.items() if k != "_containers"})
                with _lock:
                    _sessions[session.id] = session
                    _containers[session.id] = containers
                return session
    except Exception as e:
        logger.warning("Could not load session %s from file: %s", session_id, e)
    return None


def list_sessions() -> list[Session]:
    """Return all active sessions. File is authoritative for deletions — sessions
    removed by another process (e.g. stdio gateway) are evicted from memory here."""
    path = _sessions_path()
    try:
        data = json.loads(path.read_text()) if path.exists() else []
        file_ids = {e["id"] for e in data}
        file_sessions = {e["id"]: Session(**e) for e in data}
    except Exception:
        file_ids = None
        file_sessions = {}
    with _lock:
        # Evict in-memory sessions that were deleted by another process
        if file_ids is not None:
            for sid in list(_sessions.keys()):
                if sid not in file_ids:
                    _sessions.pop(sid, None)
                    _containers.pop(sid, None)
        merged = {**file_sessions, **_sessions}
        return list(merged.values())


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

    # Remove from file too
    path = _sessions_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            data = [e for e in data if e["id"] != session_id]
            path.write_text(json.dumps(data, indent=2, default=str))
        except Exception as e:
            logger.warning("Could not update sessions file: %s", e)

    logger.info("Ended session %s", session_id)
    return True


# ── Container management within a session ────────────────────────────────────

def get_or_start_container(session_id: str, server_def: ServerDef) -> ContainerInfo:
    session = get_session(session_id)  # also loads from file if needed
    if session is None:
        raise ValueError(f"Session {session_id!r} not found")
    with _lock:
        existing = _containers[session_id].get(server_def.name)

    if existing is not None:
        return existing

    info = orch.start_container(server_def, session)
    ready = orch.wait_until_ready(info)
    if not ready:
        logs = orch.get_container_logs(info.container_id)
        orch.stop_container(info.container_id)
        raise RuntimeError(
            f"Container for server {server_def.name!r} failed to start "
            f"within {settings.container_startup_timeout}s.\n"
            f"Container logs:\n{logs}"
        )

    with _lock:
        if session_id in _sessions:
            _sessions[session_id].containers[server_def.name] = info.container_id
            _containers[session_id][server_def.name] = info
            _save()

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
    _load_from_file()
    t = threading.Thread(target=_cleanup_loop, daemon=True, name="cooperage-cleanup")
    t.start()
    logger.info("Session cleanup thread started (interval=%ds)", settings.session_cleanup_interval)
