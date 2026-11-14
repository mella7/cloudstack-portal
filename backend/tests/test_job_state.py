from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.jobs.broker import Broker
from app.jobs.manager import JobManager
from app.jobs.state import ALLOWED, InvalidTransition, check_transition
from app.jobs.store import JobNotFound, JobStore
from app.models.job import TERMINAL, JobStatus
from app.models.provision import ProvisionRequest
from app.services.masking import Masker
from app.services.terraform import PLAN_FILE, FakeTerraform

from .conftest import make_settings


@pytest.fixture
def store(tmp_path):
    s = JobStore(tmp_path / "jobs.db")
    yield s
    s.close()


@pytest.fixture
def request_(form):
    return ProvisionRequest.model_validate(form)


def test_happy_path_transitions_are_allowed():
    path = [JobStatus.QUEUED, JobStatus.PLANNING, JobStatus.AWAITING_CONFIRMATION, JobStatus.APPLYING, JobStatus.DONE]
    for current, new in zip(path, path[1:], strict=False):
        check_transition(current, new)


@pytest.mark.parametrize(
    "current,new",
    [
        (JobStatus.QUEUED, JobStatus.APPLYING),
        (JobStatus.PLANNING, JobStatus.APPLYING),
        (JobStatus.PLANNING, JobStatus.DONE),
        (JobStatus.QUEUED, JobStatus.DONE),
        (JobStatus.APPLYING, JobStatus.CANCELLED),
        (JobStatus.FAILED, JobStatus.APPLYING),
    ],
)
def test_skipping_the_confirmation_is_impossible(current, new):
    with pytest.raises(InvalidTransition):
        check_transition(current, new)


def test_terminal_states_have_no_way_out():
    assert all(ALLOWED[s] == frozenset() for s in TERMINAL)


def test_store_creates_queued_job_with_timestamps(store, request_):
    job = store.create(request_)
    assert job.status is JobStatus.QUEUED
    assert job.created_at == job.updated_at and job.finished_at is None
    assert store.get(job.id).request == request_


def test_store_enforces_transitions_and_sets_finished_at(store, request_):
    job = store.create(request_)
    with pytest.raises(InvalidTransition):
        store.set_status(job.id, JobStatus.APPLYING)
    assert store.get(job.id).status is JobStatus.QUEUED
    store.set_status(job.id, JobStatus.PLANNING)
    failed = store.set_status(job.id, JobStatus.FAILED, error="boom")
    assert failed.error == "boom" and failed.finished_at is not None


def test_store_unknown_job(store):
    with pytest.raises(JobNotFound):
        store.get("nope")
    with pytest.raises(JobNotFound):
        store.set_status("nope", JobStatus.PLANNING)


def test_log_lines_get_increasing_sequence_numbers_per_job(store, request_):
    a, b = store.create(request_), store.create(request_)
    assert [store.append_log(a.id, f"line {i}") for i in range(3)] == [1, 2, 3]
    assert store.append_log(b.id, "other") == 1
    assert [line.line for line in store.get_logs(a.id, after_seq=1)] == ["line 1", "line 2"]


def test_database_from_the_first_version_is_migrated(tmp_path, request_):
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        """CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT NOT NULL, request TEXT NOT NULL, plan_summary TEXT,
           plan_sha256 TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT,
           workdir_removed INTEGER NOT NULL DEFAULT 0);"""
    )
    db.execute(
        "INSERT INTO jobs (id, status, request, created_at, updated_at) VALUES ('old', 'done', ?, ?, ?)",
        (request_.model_dump_json(), "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    db.commit()
    db.close()

    store = JobStore(path)
    old = store.get("old")
    assert old.kind.value == "create" and old.source_job_id is None and old.destroyable is False
    store.close()


def test_plans_expire_after_the_plan_ttl_not_the_workdir_retention(tmp_path, store, request_):
    manager = _manager(tmp_path, store, plan_ttl_minutes=30, workdir_retention_hours=24)
    job = store.create(request_)
    store.set_status(job.id, JobStatus.PLANNING)
    store.set_status(job.id, JobStatus.AWAITING_CONFIRMATION)
    now = datetime.now(timezone.utc)
    asyncio.run(manager.cleanup(now=now + timedelta(minutes=20)))
    assert store.get(job.id).status is JobStatus.AWAITING_CONFIRMATION
    asyncio.run(manager.cleanup(now=now + timedelta(minutes=40)))
    assert store.get(job.id).status is JobStatus.CANCELLED


def _manager(tmp_path, store, **overrides):
    settings = make_settings(tmp_path, **overrides)
    return JobManager(store, Broker(), FakeTerraform(), settings, Masker(settings.secrets()))


def test_restart_marks_running_jobs_as_failed(tmp_path, store, request_):
    queued, applying, waiting = (store.create(request_) for _ in range(3))
    for status in (JobStatus.PLANNING, JobStatus.AWAITING_CONFIRMATION, JobStatus.APPLYING):
        store.set_status(applying.id, status)
    for status in (JobStatus.PLANNING, JobStatus.AWAITING_CONFIRMATION):
        store.set_status(waiting.id, status)

    assert asyncio.run(_manager(tmp_path, store).recover_interrupted()) == 2
    assert store.get(queued.id).status is JobStatus.FAILED
    assert "partially created" in store.get(applying.id).error
    # A saved plan survives a restart and can still be confirmed.
    assert store.get(waiting.id).status is JobStatus.AWAITING_CONFIRMATION


def test_cleanup_removes_old_workdirs_but_keeps_state_and_recent_jobs(tmp_path, store, request_):
    manager = _manager(tmp_path, store, workdir_retention_hours=1)
    settings = manager._s
    old, recent = store.create(request_), store.create(request_)
    for job in (old, recent):
        manager.workdir(job.id).mkdir(parents=True)
        (manager.workdir(job.id) / PLAN_FILE).write_text("plan")
        store.set_status(job.id, JobStatus.FAILED)
    state_file = settings.state_dir / old.id / "terraform.tfstate"
    state_file.parent.mkdir(parents=True)
    state_file.write_text("{}")
    store._db.execute(
        "UPDATE jobs SET updated_at = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(), old.id),
    )

    assert asyncio.run(manager.cleanup()) == 1
    assert not manager.workdir(old.id).exists()
    assert manager.workdir(recent.id).exists()
    assert state_file.exists()
    assert asyncio.run(manager.cleanup()) == 0  # already removed, not counted twice


def test_cleanup_expires_plans_nobody_confirmed(tmp_path, store, request_):
    manager = _manager(tmp_path, store, workdir_retention_hours=1)
    job = store.create(request_)
    manager.workdir(job.id).mkdir(parents=True)
    store.set_status(job.id, JobStatus.PLANNING)
    store.set_status(job.id, JobStatus.AWAITING_CONFIRMATION)

    asyncio.run(manager.cleanup(now=datetime.now(timezone.utc) + timedelta(hours=2)))
    assert store.get(job.id).status is JobStatus.CANCELLED
    assert not manager.workdir(job.id).exists()
