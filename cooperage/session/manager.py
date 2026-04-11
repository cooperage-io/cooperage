import json
import logging
import threading
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cooperage.core.config import settings
from cooperage.core.errors import ContainerStartupError, SessionNotFoundError
from cooperage.core.models import ContainerInfo, Session, ServerDef
from cooperage.orchestrator import get_orchestrator

logger = logging.getLogger(__name__)

# In-memory store: session_id → Session
_sessions: dict[str, Session] = {}
# session_id → {server_name → ContainerInfo}
_containers: dict[str, dict[str, ContainerInfo]] = {}
_lock = threading.Lock()
# Per-(session_id, server_name) locks to prevent duplicate container starts
_start_locks: dict[str, threading.Lock] = {}


# ── File persistence ──────────────────────────────────────────────────────────

def _sessions_path() -> Path:
    return settings.sessions_path


def _atomic_write(path: Path, data: str) -> None:
    """Write data to path atomically via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with open(fd, "w") as f:
            f.write(data)
        Path(tmp).rename(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _save() -> None:
    """Persist sessions to disk. Caller must hold _lock."""
    data = []
    for session in _sessions.values():
        entry = session.model_dump(mode="json")
        entry["_containers"] = {
            name: info.model_dump()
            for name, info in _containers.get(session.id, {}).items()
        }
        data.append(entry)
    _atomic_write(_sessions_path(), json.dumps(data, indent=2, default=str))


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


def _parse_file_entry(entry: dict) -> tuple[Session, dict[str, ContainerInfo]]:
    """Parse a single session entry from the JSON file."""
    container_data = entry.pop("_containers", {})
    containers = {
        name: ContainerInfo(**info)
        for name, info in container_data.items()
    }
    session = Session(**entry)
    return session, containers


# ── Session lifecycle ─────────────────────────────────────────────────────────

def create_session(name: str | None = None, tenant_id: str = "default") -> Session:
    orch = get_orchestrator()
    session = Session(
        name=name,
        tenant_id=tenant_id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.session_ttl_seconds),
    )
    orch.create_volume(session.volume_name)
    orch.create_session_network(session)
    with _lock:
        _sessions[session.id] = session
        _containers[session.id] = {}
        _save()
    logger.info("Created session %s (tenant=%s, volume=%s)", session.id, tenant_id, session.volume_name)
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
                session, containers = _parse_file_entry(entry)
                with _lock:
                    _sessions[session.id] = session
                    _containers[session.id] = containers
                return session
    except Exception as e:
        logger.warning("Could not load session %s from file: %s", session_id, e)
    return None


def list_sessions(tenant_id: str | None = None) -> list[Session]:
    """Return all active sessions, optionally filtered by tenant.
    File is authoritative for deletions — sessions removed by another
    process (e.g. stdio gateway) are evicted from memory here."""
    path = _sessions_path()
    try:
        data = json.loads(path.read_text()) if path.exists() else []
        file_ids = {e["id"] for e in data}
        file_sessions = {}
        for e in data:
            entry_copy = dict(e)
            entry_copy.pop("_containers", None)
            file_sessions[e["id"]] = Session(**entry_copy)
    except Exception as e:
        logger.warning("Failed to load sessions from %s: %s", path, e)
        file_ids = None
        file_sessions = {}
    with _lock:
        if file_ids is not None:
            for sid in list(_sessions.keys()):
                if sid not in file_ids:
                    _sessions.pop(sid, None)
                    _containers.pop(sid, None)
        merged = {**file_sessions, **_sessions}
        all_sessions = list(merged.values())

    if tenant_id is not None:
        return [s for s in all_sessions if s.tenant_id == tenant_id]
    return all_sessions


def count_sessions_for_tenant(tenant_id: str) -> int:
    """Count non-expired sessions for a given tenant (for quota enforcement)."""
    now = datetime.now(timezone.utc)
    return len([s for s in list_sessions(tenant_id=tenant_id) if s.expires_at > now])


def end_session(session_id: str) -> bool:
    orch = get_orchestrator()
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

    try:
        orch.remove_session_network(session)
    except Exception as e:
        logger.warning("Failed to remove network %s: %s", session.network_name, e)

    # Remove from file atomically
    path = _sessions_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            data = [e for e in data if e["id"] != session_id]
            _atomic_write(path, json.dumps(data, indent=2, default=str))
        except Exception as e:
            logger.warning("Could not update sessions file: %s", e)

    logger.info("Ended session %s", session_id)
    return True


# ── Container management within a session ────────────────────────────────────

def get_or_start_container(session_id: str, server_def: ServerDef) -> ContainerInfo:
    orch = get_orchestrator()
    session = get_session(session_id)
    if session is None:
        raise SessionNotFoundError(f"Session {session_id!r} not found")

    start_key = f"{session_id}:{server_def.name}"

    # Double-checked locking pattern:
    #   1. Check under _lock → return immediately if the container exists (fast path).
    #   2. Grab a per-container start_lock so only one thread starts the container.
    #   3. Re-check under _lock — another thread may have won the race while we waited.
    # This avoids holding _lock during the slow Docker/K8s start_container call,
    # while still preventing two threads from starting the same container.

    with _lock:
        existing = _containers[session_id].get(server_def.name)
        if existing is not None:
            return existing
        if start_key not in _start_locks:
            _start_locks[start_key] = threading.Lock()
        start_lock = _start_locks[start_key]

    with start_lock:
        with _lock:
            existing = _containers[session_id].get(server_def.name)
        if existing is not None:
            return existing

        info = orch.start_container(server_def, session)
        ready = orch.wait_until_ready(info)
        if not ready:
            logs = orch.get_container_logs(info.container_id)
            orch.stop_container(info.container_id)
            raise ContainerStartupError(
                f"Container for server {server_def.name!r} failed to start "
                f"within {settings.container_startup_timeout}s.",
                logs=logs,
            )

        with _lock:
            if session_id in _sessions:
                _sessions[session_id].containers[server_def.name] = info.container_id
                _containers[session_id][server_def.name] = info
                _save()
            _start_locks.pop(start_key, None)

    return info


def get_container(session_id: str, server_name: str) -> ContainerInfo | None:
    with _lock:
        return _containers.get(session_id, {}).get(server_name)


def remove_container(session_id: str, server_name: str) -> None:
    """Remove a container from tracking (after manual stop)."""
    with _lock:
        containers = _containers.get(session_id, {})
        containers.pop(server_name, None)
        session = _sessions.get(session_id)
        if session:
            session.containers.pop(server_name, None)
        _save()


# ── Activity tracking ─────────────────────────────────────────────────────────

def touch_container(session_id: str, server_name: str) -> None:
    """Record activity on a container (called on every tool invocation)."""
    now = datetime.now(timezone.utc)
    with _lock:
        info = _containers.get(session_id, {}).get(server_name)
        if info is not None:
            info.last_activity = now


MAX_SESSION_LIFETIME_SECONDS = 72 * 3600  # 72 hours from creation


def set_session_expiry(session_id: str, expires_at: datetime) -> datetime:
    """Set session expiry to a specific time. Capped at 72h from creation.
    Returns the actual expiry that was set."""
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        max_expiry = session.created_at + timedelta(seconds=MAX_SESSION_LIFETIME_SECONDS)
        if expires_at > max_expiry:
            expires_at = max_expiry
        if expires_at <= datetime.now(timezone.utc):
            raise ValueError("Expiry must be in the future")
        session.expires_at = expires_at
        _save()
    return expires_at


def touch_session(session_id: str) -> None:
    """Extend session TTL on activity (if enabled). Never shortens the expiry."""
    if not settings.session_extend_on_activity:
        return
    now = datetime.now(timezone.utc)
    new_expiry = now + timedelta(seconds=settings.session_ttl_seconds)
    with _lock:
        session = _sessions.get(session_id)
        if session is not None and new_expiry > session.expires_at:
            session.expires_at = new_expiry
            _save()


# ── Cleanup background thread ────────────────────────────────────────────────

def _cleanup_idle_containers() -> None:
    """Stop containers that haven't been used within the idle timeout."""
    if settings.container_idle_timeout <= 0:
        return
    orch = get_orchestrator()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=settings.container_idle_timeout)
    to_stop: list[tuple[str, str, ContainerInfo]] = []

    with _lock:
        for sid, container_map in _containers.items():
            for server_name, info in container_map.items():
                if info.last_activity < cutoff:
                    to_stop.append((sid, server_name, info))

    for sid, server_name, info in to_stop:
        logger.info(
            "Container %s (server=%s, session=%s) idle for >%ds, stopping",
            info.container_id[:12], server_name, sid[:8],
            settings.container_idle_timeout,
        )
        try:
            orch.stop_container(info.container_id)
        except Exception as e:
            logger.warning("Failed to stop idle container %s: %s", info.container_id[:12], e)

        with _lock:
            if sid in _containers and server_name in _containers[sid]:
                del _containers[sid][server_name]
            if sid in _sessions and server_name in _sessions[sid].containers:
                del _sessions[sid].containers[server_name]
            _save()


def reap_expired_sessions() -> list[str]:
    """End all sessions past their TTL. Returns list of reaped session IDs."""
    now = datetime.now(timezone.utc)
    with _lock:
        expired = [sid for sid, s in _sessions.items() if s.expires_at <= now]
    for sid in expired:
        logger.info("Session %s expired, cleaning up", sid)
        end_session(sid)
    return expired


def _cleanup_loop() -> None:
    import time
    while True:
        time.sleep(settings.session_cleanup_interval)
        reap_expired_sessions()
        _cleanup_idle_containers()


def start_cleanup_thread() -> None:
    _load_from_file()
    t = threading.Thread(target=_cleanup_loop, daemon=True, name="cooperage-cleanup")
    t.start()
    logger.info("Session cleanup thread started (interval=%ds)", settings.session_cleanup_interval)
