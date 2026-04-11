"""
Async job manager — tracks background tool executions.

Mirrors the session manager pattern: in-memory dict + file persistence.
"""

import asyncio
import json
import logging
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from cooperage.core.config import settings
from cooperage.core.models import Job, JobStatus

logger = logging.getLogger(__name__)

_jobs: dict[str, Job] = {}
_job_tasks: dict[str, asyncio.Task] = {}  # in-flight asyncio tasks (not persisted)
_lock = threading.Lock()


# ── File persistence ─────────────────────────────────────────────────────────


def _jobs_path() -> Path:
    return settings.jobs_path


def _atomic_write(path: Path, data: str) -> None:
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
    """Persist jobs to disk. Caller must hold _lock."""
    data = [job.model_dump(mode="json") for job in _jobs.values()]
    _atomic_write(_jobs_path(), json.dumps(data, indent=2))


def _load_from_file() -> None:
    """Load jobs from disk into memory."""
    path = _jobs_path()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
        with _lock:
            for entry in data:
                job = Job(**entry)
                _jobs[job.id] = job
        logger.info("Loaded %d job(s) from %s", len(data), path)
    except Exception as e:
        logger.warning("Failed to load jobs from %s: %s", path, e)


# ── Job lifecycle ────────────────────────────────────────────────────────────


def create_job(
    session_id: str,
    tenant_id: str,
    server_name: str,
    tool_name: str,
    arguments: dict,
) -> Job:
    job = Job(
        session_id=session_id,
        tenant_id=tenant_id,
        server_name=server_name,
        tool_name=tool_name,
        arguments=arguments,
    )
    with _lock:
        _jobs[job.id] = job
        _save()
    logger.info("Created job %s (%s/%s) for session %s", job.id[:8], server_name, tool_name, session_id[:8])
    return job


def get_job(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def list_jobs(
    session_id: str | None = None,
    tenant_id: str | None = None,
) -> list[Job]:
    with _lock:
        jobs = list(_jobs.values())
    if session_id is not None:
        jobs = [j for j in jobs if j.session_id == session_id]
    if tenant_id is not None:
        jobs = [j for j in jobs if j.tenant_id == tenant_id]
    return jobs


def update_job(
    job_id: str,
    status: JobStatus,
    *,
    error: str | None = None,
    result_path: str | None = None,
) -> Job | None:
    now = datetime.now(timezone.utc)
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        job.status = status
        if status == JobStatus.RUNNING and job.started_at is None:
            job.started_at = now
        if status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.LOST):
            job.completed_at = now
        if error is not None:
            job.error = error
        if result_path is not None:
            job.result_path = result_path
        _save()
    return job


def cancel_job(job_id: str) -> Job | None:
    """Soft-cancel a job: cancel the asyncio task and update status."""
    task = _job_tasks.pop(job_id, None)
    if task is not None:
        task.cancel()
    return update_job(job_id, JobStatus.CANCELLED)


def register_task(job_id: str, task: asyncio.Task) -> None:
    """Associate an asyncio task with a job for cancellation support."""
    _job_tasks[job_id] = task


def unregister_task(job_id: str) -> None:
    _job_tasks.pop(job_id, None)


def mark_lost_jobs() -> list[str]:
    """Mark any RUNNING/PENDING jobs as LOST. Called on gateway startup."""
    lost = []
    with _lock:
        for job in _jobs.values():
            if job.status in (JobStatus.RUNNING, JobStatus.PENDING):
                job.status = JobStatus.LOST
                job.completed_at = datetime.now(timezone.utc)
                job.error = "Gateway restarted while job was in progress"
                lost.append(job.id)
        if lost:
            _save()
    if lost:
        logger.warning("Marked %d job(s) as lost after restart: %s", len(lost), [j[:8] for j in lost])
    return lost


def cancel_session_jobs(session_id: str) -> list[str]:
    """Cancel all running/pending jobs for a session. Called when ending a session."""
    cancelled = []
    with _lock:
        for job in _jobs.values():
            if job.session_id == session_id and job.status in (JobStatus.RUNNING, JobStatus.PENDING):
                task = _job_tasks.pop(job.id, None)
                if task is not None:
                    task.cancel()
                job.status = JobStatus.CANCELLED
                job.completed_at = datetime.now(timezone.utc)
                cancelled.append(job.id)
        if cancelled:
            _save()
    return cancelled
