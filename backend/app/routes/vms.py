"""Operations that go straight to the CloudStack API instead of Terraform."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_cloudstack
from ..models.vm import VirtualMachine, VmActionResult
from ..services.cloudstack_api import CloudStackClient, CloudStackError, VmNotFound

router = APIRouter(prefix="/api/vms", tags=["vms"])


@router.get("", response_model=list[VirtualMachine])
async def list_vms(client: CloudStackClient = Depends(get_cloudstack)) -> list[VirtualMachine]:
    try:
        return await client.list_vms()
    except CloudStackError as exc:
        raise HTTPException(502, f"CloudStack: {exc}") from None


@router.get("/{vm_id}", response_model=VirtualMachine)
async def get_vm(vm_id: UUID, client: CloudStackClient = Depends(get_cloudstack)) -> VirtualMachine:
    try:
        return await client.get_vm(str(vm_id))
    except VmNotFound:
        raise HTTPException(404, "VM not found") from None
    except CloudStackError as exc:
        raise HTTPException(502, f"CloudStack: {exc}") from None


@router.post("/{vm_id}/{action}", response_model=VmActionResult, status_code=202)
async def vm_action(
    vm_id: UUID,
    action: Literal["start", "stop", "reboot"],
    client: CloudStackClient = Depends(get_cloudstack),
) -> VmActionResult:
    call = {"start": client.start_vm, "stop": client.stop_vm, "reboot": client.reboot_vm}[action]
    try:
        outcome = await call(str(vm_id))
    except VmNotFound:
        raise HTTPException(404, "VM not found") from None
    except CloudStackError as exc:
        raise HTTPException(502, f"CloudStack: {exc}") from None
    return VmActionResult(
        vm_id=str(vm_id), action=action, async_job_id=outcome.async_job_id, completed=outcome.completed
    )
