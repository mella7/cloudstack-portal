from __future__ import annotations

from pydantic import BaseModel


class VirtualMachine(BaseModel):
    id: str
    name: str
    state: str
    zone_name: str | None = None
    service_offering_name: str | None = None
    template_name: str | None = None
    ip_address: str | None = None


class VmActionResult(BaseModel):
    vm_id: str
    action: str
    async_job_id: str | None = None
    # False when CloudStack was still working on it when we stopped waiting.
    completed: bool = True
