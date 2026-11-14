from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from ..deps import get_catalog, get_manager
from ..jobs.manager import DestroyNotAllowed, JobManager, PlanNotReady
from ..jobs.state import InvalidTransition
from ..jobs.store import JobNotFound
from ..models.job import TERMINAL, Job, LogLine
from ..models.provision import ProvisionRequest
from ..services.catalog import CatalogService
from ..services.cloudstack_api import CloudStackError

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

KEEPALIVE_SECONDS = 15.0


def field_errors(exc: ValidationError) -> list[dict[str, str]]:
    """One error shape for the frontend: [{field, message}]."""
    out = []
    for err in exc.errors():
        loc = [str(p) for p in err["loc"] if p != "body"]
        field = (err.get("ctx") or {}).get("field") or (loc[-1] if loc else "form")
        out.append({"field": str(field), "message": err["msg"]})
    return out


def _get(manager: JobManager, job_id: str) -> Job:
    try:
        return manager.store.get(job_id)
    except JobNotFound:
        raise HTTPException(404, "job not found") from None


@router.post("", response_model=Job, status_code=202)
async def create_job(
    request: ProvisionRequest,
    manager: JobManager = Depends(get_manager),
    catalog: CatalogService = Depends(get_catalog),
) -> Job:
    try:
        allowed = await catalog.get()
    except CloudStackError as exc:
        raise HTTPException(502, f"CloudStack: {exc}") from None
    try:
        checked = ProvisionRequest.model_validate(request.model_dump(), context={"catalog": allowed})
    except ValidationError as exc:
        raise HTTPException(422, field_errors(exc)) from None
    return await manager.submit(checked)


@router.get("", response_model=list[Job])
async def list_jobs(manager: JobManager = Depends(get_manager)) -> list[Job]:
    return manager.store.list()


@router.get("/{job_id}", response_model=Job)
async def get_job(job_id: str, manager: JobManager = Depends(get_manager)) -> Job:
    return _get(manager, job_id)


@router.get("/{job_id}/logs", response_model=list[LogLine])
async def get_logs(job_id: str, after: int = 0, manager: JobManager = Depends(get_manager)) -> list[LogLine]:
    _get(manager, job_id)
    return manager.store.get_logs(job_id, after)


@router.post("/{job_id}/confirm", response_model=Job, status_code=202)
async def confirm_job(job_id: str, manager: JobManager = Depends(get_manager)) -> Job:
    _get(manager, job_id)
    try:
        return await manager.confirm(job_id)
    except (InvalidTransition, PlanNotReady) as exc:
        raise HTTPException(409, f"cannot apply: {exc}") from None


@router.post("/{job_id}/cancel", response_model=Job)
async def cancel_job(job_id: str, manager: JobManager = Depends(get_manager)) -> Job:
    _get(manager, job_id)
    try:
        return await manager.cancel(job_id)
    except InvalidTransition as exc:
        raise HTTPException(409, f"cannot cancel: {exc}") from None


@router.post("/{job_id}/destroy", response_model=Job, status_code=202)
async def destroy_job(job_id: str, manager: JobManager = Depends(get_manager)) -> Job:
    """Start a destroy job for the instances this job created. Like a create
    job, it stops after the plan and waits for confirmation."""
    _get(manager, job_id)
    try:
        return await manager.request_destroy(job_id)
    except DestroyNotAllowed as exc:
        raise HTTPException(409, f"cannot destroy: {exc}") from None


def sse(event: str, data: object, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data)}\n\n"


async def job_events(
    manager: JobManager, job_id: str, after_seq: int, request: Request | None = None
) -> AsyncIterator[str]:
    """Replay stored log lines after `after_seq`, then follow the job until it finishes.

    Log events carry the line's sequence number as SSE id, so a browser that
    reconnects sends Last-Event-ID and continues without gaps or duplicates."""
    last_status = None
    while True:
        seen = manager.broker.version(job_id)
        # Status is read before the logs: a terminal status is always written
        # after the job's last log line, so the logs read below are complete.
        job = manager.store.get(job_id)
        for line in manager.store.get_logs(job_id, after_seq):
            after_seq = line.seq
            yield sse("log", {"seq": line.seq, "ts": line.ts.isoformat(), "line": line.line}, line.seq)
        if job.status != last_status:
            last_status = job.status
            yield sse("status", {"status": job.status.value, "error": job.error})
        if job.status in TERMINAL:
            yield sse("end", {"status": job.status.value})
            return
        if request is not None and await request.is_disconnected():
            return
        if not await manager.broker.wait(job_id, seen, KEEPALIVE_SECONDS):
            yield ": keepalive\n\n"


@router.get("/{job_id}/stream")
async def stream_job(
    job_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None),
    manager: JobManager = Depends(get_manager),
) -> StreamingResponse:
    _get(manager, job_id)
    try:
        after = max(0, int(last_event_id)) if last_event_id else 0
    except ValueError:
        after = 0
    return StreamingResponse(
        job_events(manager, job_id, after, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
