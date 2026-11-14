"""Job state and logs in SQLite."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..models.job import TERMINAL, Job, JobKind, JobStatus, LogLine, PlanSummary
from ..models.provision import ProvisionRequest
from .state import check_transition

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    request TEXT NOT NULL,
    plan_summary TEXT,
    plan_sha256 TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    workdir_removed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS logs (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    line TEXT NOT NULL,
    PRIMARY KEY (job_id, seq)
);
"""


# Columns added after the first version. Applied to existing databases on start.
MIGRATIONS = {
    "kind": "TEXT NOT NULL DEFAULT 'create'",
    "source_job_id": "TEXT",
    "apply_started_at": "TEXT",
    "destroyed_at": "TEXT",
}


class JobNotFound(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, db_path: Path | str) -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(SCHEMA)
            existing = {row["name"] for row in self._db.execute("PRAGMA table_info(jobs)")}
            for column, definition in MIGRATIONS.items():
                if column not in existing:
                    self._db.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")  # noqa: S608 - constants

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        kind, status = JobKind(row["kind"]), JobStatus(row["status"])
        return Job(
            id=row["id"],
            kind=kind,
            source_job_id=row["source_job_id"],
            destroyed_at=row["destroyed_at"],
            # An apply that started may have created something, even if it failed.
            destroyable=(
                kind is JobKind.CREATE
                and row["apply_started_at"] is not None
                and status in (JobStatus.DONE, JobStatus.FAILED)
                and row["destroyed_at"] is None
            ),
            status=status,
            request=ProvisionRequest.model_validate_json(row["request"]),
            plan_summary=PlanSummary.model_validate_json(row["plan_summary"]) if row["plan_summary"] else None,
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            finished_at=row["finished_at"],
        )

    def create(
        self, request: ProvisionRequest, kind: JobKind = JobKind.CREATE, source_job_id: str | None = None
    ) -> Job:
        job_id, now = str(uuid.uuid4()), _now()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO jobs (id, kind, source_job_id, status, request, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, kind.value, source_job_id, JobStatus.QUEUED.value, request.model_dump_json(), now, now),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> Job:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return self._job(row)

    def list(self, limit: int = 50) -> list[Job]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._job(r) for r in rows]

    def set_status(self, job_id: str, new: JobStatus, error: str | None = None) -> Job:
        """Atomic check-and-set, so two concurrent confirms cannot both win."""
        now = _now()
        with self._lock, self._db:
            row = self._db.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise JobNotFound(job_id)
            check_transition(JobStatus(row["status"]), new)
            self._db.execute(
                "UPDATE jobs SET status = ?, updated_at = ?, error = COALESCE(?, error), finished_at = ? WHERE id = ?",
                (new.value, now, error, now if new in TERMINAL else None, job_id),
            )
            if new is JobStatus.APPLYING:
                self._db.execute("UPDATE jobs SET apply_started_at = ? WHERE id = ?", (now, job_id))
        return self.get(job_id)

    def set_plan(self, job_id: str, summary: PlanSummary, sha256: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE jobs SET plan_summary = ?, plan_sha256 = ?, updated_at = ? WHERE id = ?",
                (summary.model_dump_json(), sha256, _now(), job_id),
            )

    def plan_sha256(self, job_id: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT plan_sha256 FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return row["plan_sha256"]

    def append_log(self, job_id: str, line: str) -> int:
        with self._lock, self._db:
            seq = self._db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM logs WHERE job_id = ?", (job_id,)).fetchone()[
                0
            ]
            self._db.execute(
                "INSERT INTO logs (job_id, seq, ts, line) VALUES (?, ?, ?, ?)", (job_id, seq, _now(), line)
            )
        return seq

    def get_logs(self, job_id: str, after_seq: int = 0) -> list[LogLine]:
        with self._lock:
            rows = self._db.execute(
                "SELECT seq, ts, line FROM logs WHERE job_id = ? AND seq > ? ORDER BY seq", (job_id, after_seq)
            ).fetchall()
        return [LogLine(seq=r["seq"], ts=r["ts"], line=r["line"]) for r in rows]

    def unfinished(self) -> list[Job]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM jobs WHERE status IN (?, ?, ?)",
                (JobStatus.QUEUED.value, JobStatus.PLANNING.value, JobStatus.APPLYING.value),
            ).fetchall()
        return [self._job(r) for r in rows]

    def older_than(self, cutoff: datetime, statuses: frozenset[JobStatus] | set[JobStatus]) -> list[Job]:
        """Jobs with a workdir still on disk, last updated before `cutoff`."""
        marks = ",".join("?" * len(statuses))
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM jobs WHERE workdir_removed = 0 AND updated_at < ? AND status IN ({marks})",  # noqa: S608 - only "?" marks are interpolated
                (cutoff.isoformat(), *[s.value for s in statuses]),
            ).fetchall()
        return [self._job(r) for r in rows]

    def mark_destroyed(self, job_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE jobs SET destroyed_at = ? WHERE id = ?", (_now(), job_id))

    def active_destroy_for(self, source_job_id: str) -> Job | None:
        """A destroy job for this source that has not finished yet."""
        done = ",".join("?" * len(TERMINAL))
        with self._lock:
            row = self._db.execute(
                f"SELECT * FROM jobs WHERE source_job_id = ? AND status NOT IN ({done})",  # noqa: S608 - "?" marks only
                (source_job_id, *[s.value for s in TERMINAL]),
            ).fetchone()
        return self._job(row) if row else None

    def mark_workdir_removed(self, job_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE jobs SET workdir_removed = 1 WHERE id = ?", (job_id,))
