"""Runs the two-stage flow in background tasks, one working directory per job."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import Settings
from ..models.job import TERMINAL, Job, JobKind, JobStatus
from ..models.provision import ProvisionRequest
from ..services.masking import Masker
from ..services.terraform import PLAN_FILE, TerraformError, TerraformRunner
from .broker import Broker
from .state import InvalidTransition
from .store import JobStore

log = logging.getLogger(__name__)


class PlanNotReady(Exception):
    """Confirm was called for a job that has no valid saved plan to apply."""


class DestroyNotAllowed(Exception):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class JobManager:
    def __init__(
        self, store: JobStore, broker: Broker, terraform: TerraformRunner, settings: Settings, masker: Masker
    ) -> None:
        self.store = store
        self.broker = broker
        self._tf = terraform
        self._s = settings
        self._masker = masker
        self._slots = asyncio.Semaphore(settings.max_concurrent_jobs)
        self._plan_tasks: dict[str, asyncio.Task[None]] = {}
        self._apply_tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_requested: set[str] = set()

    def workdir(self, job_id: str) -> Path:
        return self._s.workdirs_dir / job_id

    @staticmethod
    def _state_id(job: Job) -> str:
        # A destroy job works on the state of the job that created the instances.
        return job.source_job_id or job.id

    # -- logging and status ---------------------------------------------------

    async def log(self, job_id: str, line: str) -> None:
        self.store.append_log(job_id, self._masker.mask(line))
        await self.broker.publish(job_id)

    async def _set(self, job_id: str, status: JobStatus, error: str | None = None) -> Job:
        job = self.store.set_status(job_id, status, error)
        await self.broker.publish(job_id)
        return job

    async def _fail(self, job_id: str, message: str) -> None:
        message = self._masker.mask(message)
        await self.log(job_id, f"ERROR: {message}")
        await self._set(job_id, JobStatus.FAILED, message)

    def _spawn(self, registry: dict[str, asyncio.Task[None]], job_id: str, coro) -> None:
        task = asyncio.create_task(coro)
        registry[job_id] = task
        task.add_done_callback(lambda _: registry.pop(job_id, None))

    # -- stage 1 ----------------------------------------------------------------

    async def submit(self, request: ProvisionRequest) -> Job:
        return await self._start(self.store.create(request))

    async def request_destroy(self, source_job_id: str) -> Job:
        """Create a destroy job for the instances of an earlier create job.
        It goes through the same plan, review and confirm steps."""
        source = self.store.get(source_job_id)
        if source.kind is not JobKind.CREATE:
            raise DestroyNotAllowed("only a create job can be destroyed")
        if source.destroyed_at is not None:
            raise DestroyNotAllowed("these instances were already destroyed")
        if not source.destroyable:
            raise DestroyNotAllowed("this job has not created anything")
        active = self.store.active_destroy_for(source_job_id)
        if active is not None:
            raise DestroyNotAllowed(f"destroy job {active.id} is already in progress")
        return await self._start(self.store.create(source.request, JobKind.DESTROY, source_job_id))

    async def _start(self, job: Job) -> Job:
        await self.log(job.id, f"Job {job.id} queued ({job.kind.value})")
        self._spawn(self._plan_tasks, job.id, self._plan(job))
        return job

    async def _plan(self, job: Job) -> None:
        job_id = job.id
        try:
            async with self._slots:
                try:
                    await self._set(job_id, JobStatus.PLANNING)
                    workdir = self.workdir(job_id)
                    workdir.mkdir(parents=True, exist_ok=True)
                    summary = await self._tf.plan(
                        job_id,
                        workdir,
                        job.request.to_tfvars(),
                        lambda line: self.log(job_id, line),
                        state_id=self._state_id(job),
                        destroy=job.kind is JobKind.DESTROY,
                    )
                    plan_path = workdir / PLAN_FILE
                    if not plan_path.is_file():
                        raise TerraformError("plan finished but no plan file was saved")
                    self.store.set_plan(job_id, summary, _sha256(plan_path))
                    await self.log(job_id, "Plan saved. Nothing has been changed yet. Waiting for confirmation.")
                    await self._set(job_id, JobStatus.AWAITING_CONFIRMATION)
                except TerraformError as exc:
                    await self._fail(job_id, str(exc))
                except Exception:
                    log.exception("plan crashed for job %s", job_id)
                    await self._fail(job_id, "internal error during plan")
        except asyncio.CancelledError:
            if job_id not in self._cancel_requested:
                raise  # shutdown: the job is marked failed on the next start
            self._cancel_requested.discard(job_id)
            await self.log(job_id, "Cancelled before the plan finished. Nothing was changed.")
            await self._set(job_id, JobStatus.CANCELLED)

    # -- stage 2 ----------------------------------------------------------------

    async def confirm(self, job_id: str) -> Job:
        """Start apply. Raises InvalidTransition unless the job is awaiting confirmation."""
        job = self.store.get(job_id)
        if job.status is not JobStatus.AWAITING_CONFIRMATION:
            raise InvalidTransition(job.status, JobStatus.APPLYING)
        plan_path = self.workdir(job_id) / PLAN_FILE
        expected = self.store.plan_sha256(job_id)
        if not plan_path.is_file() or expected is None:
            await self._fail(job_id, "saved plan is missing, refusing to apply")
            raise PlanNotReady("saved plan is missing")
        if _sha256(plan_path) != expected:
            await self._fail(job_id, "saved plan changed after it was shown, refusing to apply")
            raise PlanNotReady("saved plan does not match the plan that was shown")
        job = await self._set(job_id, JobStatus.APPLYING)  # atomic: a second confirm fails here
        await self.log(job_id, "Confirmed. Applying the saved plan.")
        self._spawn(self._apply_tasks, job_id, self._apply(job))
        return job

    async def _apply(self, job: Job) -> None:
        job_id = job.id
        destroy = job.kind is JobKind.DESTROY
        async with self._slots:
            try:
                await self._tf.apply(
                    job_id,
                    self.workdir(job_id),
                    lambda line: self.log(job_id, line),
                    state_id=self._state_id(job),
                    destroy=destroy,
                )
                if destroy and job.source_job_id:
                    self.store.mark_destroyed(job.source_job_id)
                await self._set(job_id, JobStatus.DONE)
            except TerraformError as exc:
                await self._fail(job_id, str(exc))
            except Exception:
                log.exception("apply crashed for job %s", job_id)
                await self._fail(job_id, "internal error during apply")

    async def cancel(self, job_id: str) -> Job:
        """Reject a saved plan, or stop a job that is still queued or planning.
        A running apply cannot be cancelled: stopping it halfway is worse than letting it finish."""
        job = self.store.get(job_id)
        if job.status in (JobStatus.QUEUED, JobStatus.PLANNING):
            task = self._plan_tasks.get(job_id)
            if task is None:
                return await self._set(job_id, JobStatus.CANCELLED)
            self._cancel_requested.add(job_id)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return self.store.get(job_id)
        if job.status is not JobStatus.AWAITING_CONFIRMATION:
            raise InvalidTransition(job.status, JobStatus.CANCELLED)
        # Log before the status change so streams see the line before they end.
        await self.log(job_id, "Plan rejected. Nothing was changed.")
        return await self._set(job_id, JobStatus.CANCELLED)

    # -- housekeeping -----------------------------------------------------------

    async def recover_interrupted(self) -> int:
        """Jobs that were running when the process stopped can never finish."""
        jobs = self.store.unfinished()
        for job in jobs:
            hint = " Check CloudStack for partially created resources." if job.status is JobStatus.APPLYING else ""
            await self._fail(job.id, f"interrupted by a backend restart while {job.status.value}.{hint}")
        return len(jobs)

    async def cleanup(self, now: datetime | None = None) -> int:
        """Expire stale plans and delete old working directories. Terraform state is kept."""
        now = now or datetime.now(timezone.utc)
        # A plan describes the world at the time it was made, so it does not stay valid for long.
        plan_cutoff = now - timedelta(minutes=self._s.plan_ttl_minutes)
        for job in self.store.older_than(plan_cutoff, {JobStatus.AWAITING_CONFIRMATION}):
            await self.log(job.id, "Plan expired without confirmation. Nothing was changed.")
            await self._set(job.id, JobStatus.CANCELLED)
            shutil.rmtree(self.workdir(job.id), ignore_errors=True)
            self.store.mark_workdir_removed(job.id)
        removed = 0
        cutoff = now - timedelta(hours=self._s.workdir_retention_hours)
        for job in self.store.older_than(cutoff, TERMINAL):
            shutil.rmtree(self.workdir(job.id), ignore_errors=True)
            self.store.mark_workdir_removed(job.id)
            removed += 1
        return removed

    async def cleanup_forever(self) -> None:
        while True:
            try:
                await self.cleanup()
            except Exception:
                log.exception("cleanup failed")
            await asyncio.sleep(self._s.cleanup_interval_seconds)

    async def shutdown(self) -> None:
        """Plans are safe to drop. Applies are not, so give them time to finish first."""
        for task in list(self._plan_tasks.values()):
            task.cancel()
        applies = list(self._apply_tasks.values())
        if applies:
            log.warning("waiting up to %ss for %d running apply job(s)", self._s.shutdown_grace_seconds, len(applies))
            _, pending = await asyncio.wait(applies, timeout=self._s.shutdown_grace_seconds)
            for task in pending:
                task.cancel()
        await asyncio.gather(*self._plan_tasks.values(), *applies, return_exceptions=True)
