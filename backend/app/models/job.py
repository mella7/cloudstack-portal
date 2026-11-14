from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, field_serializer

from .provision import ProvisionRequest

USER_DATA_HIDDEN = "[hidden]"


class JobStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    APPLYING = "applying"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobKind(StrEnum):
    CREATE = "create"
    DESTROY = "destroy"


TERMINAL = frozenset({JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED})


class PlannedResource(BaseModel):
    address: str
    type: str
    name: str
    action: str
    details: dict[str, str] = {}  # the attributes worth reviewing: name, disk size, ...


class PlanSummary(BaseModel):
    add: int = 0
    change: int = 0
    destroy: int = 0
    resources: list[PlannedResource] = []


class Job(BaseModel):
    id: str
    kind: JobKind = JobKind.CREATE
    # For a destroy job: the create job whose instances it removes.
    source_job_id: str | None = None
    status: JobStatus
    request: ProvisionRequest
    plan_summary: PlanSummary | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    # For a create job: set once a destroy job has removed its instances.
    destroyed_at: datetime | None = None
    # True when this job may have created resources that can still be destroyed.
    destroyable: bool = False

    @field_serializer("request")
    def _hide_user_data(self, request: ProvisionRequest) -> dict[str, Any]:
        # Job objects are what the API returns. User data often contains secrets,
        # so only say whether it was set. The database keeps the real request.
        data = request.model_dump()
        if data["user_data"] is not None:
            data["user_data"] = USER_DATA_HIDDEN
        return data


class LogLine(BaseModel):
    seq: int
    ts: datetime
    line: str
