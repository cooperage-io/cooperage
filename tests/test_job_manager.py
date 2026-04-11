"""
Job manager tests — create, status transitions, persistence, mark_lost, filtering.
"""

import json
from unittest.mock import patch

import pytest

import cooperage.job.manager as jobs
from cooperage.core.models import Job, JobStatus


@pytest.fixture(autouse=True)
def clean_jobs():
    """Reset job state between tests."""
    jobs._jobs.clear()
    jobs._job_tasks.clear()
    yield
    jobs._jobs.clear()
    jobs._job_tasks.clear()


@pytest.fixture
def mock_save(monkeypatch):
    """Skip file I/O in unit tests."""
    monkeypatch.setattr("cooperage.job.manager._save", lambda: None)


# ── create_job ───────────────────────────────────────────────────────────────


def test_create_job(mock_save):
    job = jobs.create_job(
        session_id="s1",
        tenant_id="alpha",
        server_name="sim",
        tool_name="run_sim",
        arguments={"input": "x"},
    )
    assert job.id
    assert job.session_id == "s1"
    assert job.tenant_id == "alpha"
    assert job.server_name == "sim"
    assert job.tool_name == "run_sim"
    assert job.status == JobStatus.PENDING
    assert job.started_at is None
    assert job.completed_at is None


def test_create_job_is_retrievable(mock_save):
    job = jobs.create_job("s1", "alpha", "sim", "run_sim", {})
    assert jobs.get_job(job.id) is not None


def test_get_job_unknown():
    assert jobs.get_job("nosuchid") is None


# ── list_jobs ────────────────────────────────────────────────────────────────


def test_list_jobs_empty():
    assert jobs.list_jobs() == []


def test_list_jobs_all(mock_save):
    jobs.create_job("s1", "alpha", "sim", "run", {})
    jobs.create_job("s2", "beta", "sim", "run", {})
    assert len(jobs.list_jobs()) == 2


def test_list_jobs_by_session(mock_save):
    jobs.create_job("s1", "alpha", "sim", "run", {})
    jobs.create_job("s2", "beta", "sim", "run", {})
    assert len(jobs.list_jobs(session_id="s1")) == 1


def test_list_jobs_by_tenant(mock_save):
    jobs.create_job("s1", "alpha", "sim", "run", {})
    jobs.create_job("s2", "beta", "sim", "run", {})
    assert len(jobs.list_jobs(tenant_id="beta")) == 1


# ── update_job ───────────────────────────────────────────────────────────────


def test_update_to_running(mock_save):
    job = jobs.create_job("s1", "alpha", "sim", "run", {})
    updated = jobs.update_job(job.id, JobStatus.RUNNING)
    assert updated.status == JobStatus.RUNNING
    assert updated.started_at is not None
    assert updated.completed_at is None


def test_update_to_completed(mock_save):
    job = jobs.create_job("s1", "alpha", "sim", "run", {})
    jobs.update_job(job.id, JobStatus.RUNNING)
    updated = jobs.update_job(job.id, JobStatus.COMPLETED, result_path=".jobs/abc.json")
    assert updated.status == JobStatus.COMPLETED
    assert updated.completed_at is not None
    assert updated.result_path == ".jobs/abc.json"


def test_update_to_failed(mock_save):
    job = jobs.create_job("s1", "alpha", "sim", "run", {})
    jobs.update_job(job.id, JobStatus.RUNNING)
    updated = jobs.update_job(job.id, JobStatus.FAILED, error="boom")
    assert updated.status == JobStatus.FAILED
    assert updated.error == "boom"
    assert updated.completed_at is not None


def test_update_unknown_returns_none(mock_save):
    assert jobs.update_job("nosuchid", JobStatus.RUNNING) is None


def test_running_sets_started_at_only_once(mock_save):
    job = jobs.create_job("s1", "alpha", "sim", "run", {})
    jobs.update_job(job.id, JobStatus.RUNNING)
    first_started = jobs.get_job(job.id).started_at
    jobs.update_job(job.id, JobStatus.RUNNING)
    assert jobs.get_job(job.id).started_at == first_started


# ── cancel_job ───────────────────────────────────────────────────────────────


def test_cancel_job(mock_save):
    job = jobs.create_job("s1", "alpha", "sim", "run", {})
    jobs.update_job(job.id, JobStatus.RUNNING)
    updated = jobs.cancel_job(job.id)
    assert updated.status == JobStatus.CANCELLED
    assert updated.completed_at is not None


# ── mark_lost_jobs ───────────────────────────────────────────────────────────


def test_mark_lost_jobs(mock_save):
    j1 = jobs.create_job("s1", "alpha", "sim", "run", {})
    j2 = jobs.create_job("s1", "alpha", "sim", "run", {})
    j3 = jobs.create_job("s1", "alpha", "sim", "run", {})
    jobs.update_job(j1.id, JobStatus.RUNNING)
    jobs.update_job(j2.id, JobStatus.COMPLETED, result_path="x")
    # j3 is still PENDING

    lost = jobs.mark_lost_jobs()
    assert j1.id in lost  # was RUNNING
    assert j3.id in lost  # was PENDING
    assert j2.id not in lost  # was COMPLETED
    assert jobs.get_job(j1.id).status == JobStatus.LOST
    assert jobs.get_job(j3.id).status == JobStatus.LOST
    assert jobs.get_job(j2.id).status == JobStatus.COMPLETED


def test_mark_lost_noop_when_no_active_jobs(mock_save):
    j1 = jobs.create_job("s1", "alpha", "sim", "run", {})
    jobs.update_job(j1.id, JobStatus.COMPLETED, result_path="x")
    lost = jobs.mark_lost_jobs()
    assert lost == []


# ── cancel_session_jobs ──────────────────────────────────────────────────────


def test_cancel_session_jobs(mock_save):
    j1 = jobs.create_job("s1", "alpha", "sim", "run", {})
    j2 = jobs.create_job("s1", "alpha", "sim", "run", {})
    j3 = jobs.create_job("s2", "beta", "sim", "run", {})
    jobs.update_job(j1.id, JobStatus.RUNNING)

    cancelled = jobs.cancel_session_jobs("s1")
    assert j1.id in cancelled
    assert j2.id in cancelled
    assert j3.id not in cancelled
    assert jobs.get_job(j1.id).status == JobStatus.CANCELLED
    assert jobs.get_job(j3.id).status == JobStatus.PENDING


# ── persistence ──────────────────────────────────────────────────────────────


def test_persistence_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr("cooperage.job.manager.settings.jobs_path", tmp_path / "jobs.json")
    job = jobs.create_job("s1", "alpha", "sim", "run", {"k": "v"})
    jobs.update_job(job.id, JobStatus.RUNNING)

    # Clear in-memory state
    jobs._jobs.clear()
    assert jobs.get_job(job.id) is None

    # Reload from file
    jobs._load_from_file()
    loaded = jobs.get_job(job.id)
    assert loaded is not None
    assert loaded.status == JobStatus.RUNNING
    assert loaded.session_id == "s1"
    assert loaded.arguments == {"k": "v"}
