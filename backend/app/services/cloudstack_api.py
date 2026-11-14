"""Direct CloudStack API access for things Terraform does not do well:
live status and power actions. Callers only depend on CloudStackClient."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from ..models.catalog import DiskOffering, Network, ServiceOffering, Template, Zone
from ..models.vm import VirtualMachine

# httpx logs full request URLs at INFO, which would include apiKey and signature.
logging.getLogger("httpx").setLevel(logging.WARNING)


class CloudStackError(Exception):
    pass


class VmNotFound(CloudStackError):
    pass


@dataclass(frozen=True)
class ActionOutcome:
    """Result of a power action. `completed` is False when CloudStack was
    still working on it when we stopped waiting."""

    async_job_id: str | None
    completed: bool = True


class CloudStackClient(Protocol):
    async def list_zones(self) -> list[Zone]: ...
    async def list_service_offerings(self) -> list[ServiceOffering]: ...
    async def list_templates(self) -> list[Template]: ...
    async def list_networks(self) -> list[Network]: ...
    async def list_keypairs(self) -> list[str]: ...
    async def list_disk_offerings(self) -> list[DiskOffering]: ...
    async def list_vms(self) -> list[VirtualMachine]: ...
    async def get_vm(self, vm_id: str) -> VirtualMachine: ...
    async def start_vm(self, vm_id: str) -> ActionOutcome: ...
    async def stop_vm(self, vm_id: str) -> ActionOutcome: ...
    async def reboot_vm(self, vm_id: str) -> ActionOutcome: ...


def _encode(value: str) -> str:
    # Same output as Java's URLEncoder with spaces as %20, which is what
    # CloudStack uses when it recomputes the signature.
    return quote(value, safe="*").replace("~", "%7E")


def sign_params(params: dict[str, str], secret_key: str) -> str:
    """CloudStack request signature: sort by key, URL-encode values,
    lowercase the whole query string, HMAC-SHA1, base64."""
    query = "&".join(f"{k}={_encode(v)}" for k, v in sorted(params.items(), key=lambda kv: kv[0].lower()))
    digest = hmac.new(secret_key.encode(), query.lower().encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def _vm(d: dict[str, Any]) -> VirtualMachine:
    nics = d.get("nic") or []
    return VirtualMachine(
        id=d["id"],
        name=d.get("name", ""),
        state=d.get("state", "Unknown"),
        zone_name=d.get("zonename"),
        service_offering_name=d.get("serviceofferingname"),
        template_name=d.get("templatename"),
        ip_address=nics[0].get("ipaddress") if nics else None,
    )


class SignedCloudStackClient:
    def __init__(
        self,
        api_url: str,
        api_key: str,
        secret_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
        action_timeout: float = 30.0,
        poll_interval: float = 1.0,
        page_size: int = 500,
    ) -> None:
        self._action_timeout = action_timeout
        self._poll_interval = poll_interval
        self._page_size = page_size
        self._url = api_url
        self._api_key = api_key
        self._secret_key = secret_key
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(self, command: str, **params: str) -> dict[str, Any]:
        query = {"command": command, "response": "json", "apiKey": self._api_key, **params}
        query["signature"] = sign_params(query, self._secret_key)
        try:
            resp = await self._http.get(self._url, params=query)
        except httpx.HTTPError as exc:
            # Never include the exception text: it carries the signed URL.
            raise CloudStackError(f"{command}: request failed ({type(exc).__name__})") from None
        try:
            body = resp.json()
            payload = next(iter(body.values()))
        except (ValueError, StopIteration, AttributeError):
            raise CloudStackError(f"{command}: unexpected response (HTTP {resp.status_code})") from None
        if resp.status_code >= 400 or (isinstance(payload, dict) and "errorcode" in payload):
            text = payload.get("errortext", "unknown error") if isinstance(payload, dict) else "unknown error"
            raise CloudStackError(f"{command}: {text}")
        return payload

    async def _list(self, command: str, key: str, **params: str) -> list[dict[str, Any]]:
        """List calls are paged by CloudStack; fetch every page."""
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            data = await self._call(command, page=str(page), pagesize=str(self._page_size), **params)
            batch = data.get(key, [])
            items.extend(batch)
            if len(batch) < self._page_size or len(items) >= data.get("count", 0):
                return items
            page += 1

    async def _action(self, command: str, vm_id: str) -> ActionOutcome:
        """Start an async CloudStack job and wait for its result, up to action_timeout."""
        job_id = (await self._call(command, id=vm_id)).get("jobid")
        if job_id is None:
            return ActionOutcome(None)
        deadline = time.monotonic() + self._action_timeout
        while True:
            result = await self._call("queryAsyncJobResult", jobid=job_id)
            status = result.get("jobstatus", 0)
            if status == 1:
                return ActionOutcome(job_id)
            if status == 2:
                detail = result.get("jobresult") or {}
                raise CloudStackError(f"{command}: {detail.get('errortext', 'the job failed')}")
            if time.monotonic() >= deadline:
                return ActionOutcome(job_id, completed=False)
            await asyncio.sleep(self._poll_interval)

    async def list_zones(self) -> list[Zone]:
        return [Zone(id=z["id"], name=z["name"]) for z in await self._list("listZones", "zone", available="true")]

    async def list_service_offerings(self) -> list[ServiceOffering]:
        return [
            ServiceOffering(id=o["id"], name=o["name"], cpu_number=o.get("cpunumber"), memory_mb=o.get("memory"))
            for o in await self._list("listServiceOfferings", "serviceoffering")
        ]

    async def list_templates(self) -> list[Template]:
        return [
            Template(id=t["id"], name=t["name"], zone_id=t.get("zoneid"))
            for t in await self._list("listTemplates", "template", templatefilter="executable")
        ]

    async def list_networks(self) -> list[Network]:
        return [
            Network(id=n["id"], name=n["name"], zone_id=n["zoneid"])
            for n in await self._list("listNetworks", "network")
        ]

    async def list_keypairs(self) -> list[str]:
        return [k["name"] for k in await self._list("listSSHKeyPairs", "sshkeypair")]

    async def list_disk_offerings(self) -> list[DiskOffering]:
        return [
            DiskOffering(
                id=o["id"],
                name=o["name"],
                customized=bool(o.get("iscustomized")),
                size_gb=None if o.get("iscustomized") else o.get("disksize"),
            )
            for o in await self._list("listDiskOfferings", "diskoffering")
        ]

    async def list_vms(self) -> list[VirtualMachine]:
        return [_vm(v) for v in await self._list("listVirtualMachines", "virtualmachine")]

    async def get_vm(self, vm_id: str) -> VirtualMachine:
        data = await self._call("listVirtualMachines", id=vm_id)
        vms = data.get("virtualmachine", [])
        if not vms:
            raise VmNotFound(f"VM {vm_id} not found")
        return _vm(vms[0])

    async def start_vm(self, vm_id: str) -> ActionOutcome:
        return await self._action("startVirtualMachine", vm_id)

    async def stop_vm(self, vm_id: str) -> ActionOutcome:
        return await self._action("stopVirtualMachine", vm_id)

    async def reboot_vm(self, vm_id: str) -> ActionOutcome:
        return await self._action("rebootVirtualMachine", vm_id)


# --- DRY_RUN implementation -------------------------------------------------

_Z1, _Z2 = "11111111-0000-4000-8000-000000000001", "11111111-0000-4000-8000-000000000002"

FAKE_ZONES = [Zone(id=_Z1, name="zrh-1"), Zone(id=_Z2, name="fra-1")]
FAKE_OFFERINGS = [
    ServiceOffering(
        id="22222222-0000-4000-8000-000000000001", name="Small (1 vCPU, 2 GB)", cpu_number=1, memory_mb=2048
    ),
    ServiceOffering(
        id="22222222-0000-4000-8000-000000000002", name="Medium (2 vCPU, 4 GB)", cpu_number=2, memory_mb=4096
    ),
    ServiceOffering(
        id="22222222-0000-4000-8000-000000000003", name="Large (4 vCPU, 8 GB)", cpu_number=4, memory_mb=8192
    ),
]
FAKE_TEMPLATES = [
    Template(id="33333333-0000-4000-8000-000000000001", name="Ubuntu 24.04 LTS"),
    Template(id="33333333-0000-4000-8000-000000000002", name="Debian 12"),
    Template(id="33333333-0000-4000-8000-000000000003", name="Rocky Linux 9"),
]
FAKE_NETWORKS = [
    Network(id="44444444-0000-4000-8000-000000000001", name="zrh-default", zone_id=_Z1),
    Network(id="44444444-0000-4000-8000-000000000002", name="zrh-dmz", zone_id=_Z1),
    Network(id="44444444-0000-4000-8000-000000000003", name="fra-default", zone_id=_Z2),
]


FAKE_KEYPAIRS = ["ops-team", "deploy-ci"]
FAKE_DISK_OFFERINGS = [
    DiskOffering(id="55555555-0000-4000-8000-000000000001", name="Standard 50 GB", size_gb=50),
    DiskOffering(id="55555555-0000-4000-8000-000000000002", name="Standard 200 GB", size_gb=200),
    DiskOffering(id="55555555-0000-4000-8000-000000000003", name="Custom size", customized=True),
]


class FakeCloudStackClient:
    """In-memory CloudStack. VMs created by FakeTerraform show up here."""

    def __init__(self) -> None:
        self._vms: dict[str, VirtualMachine] = {}
        self._next_ip = 10
        self.add_vm("demo-web-1", "zrh-1", "Small (1 vCPU, 2 GB)", "Ubuntu 24.04 LTS")
        self.add_vm("demo-db-1", "zrh-1", "Medium (2 vCPU, 4 GB)", "Debian 12", state="Stopped")

    def add_vm(self, name: str, zone: str, offering: str, template: str, state: str = "Running") -> VirtualMachine:
        # Accepts ids or names so FakeTerraform can pass the form values straight through.
        names = {x.id: x.name for x in (*FAKE_ZONES, *FAKE_OFFERINGS, *FAKE_TEMPLATES)}
        vm = VirtualMachine(
            id=str(uuid.uuid4()),
            name=name,
            state=state,
            zone_name=names.get(zone, zone),
            service_offering_name=names.get(offering, offering),
            template_name=names.get(template, template),
            ip_address=f"10.1.1.{self._next_ip}",
        )
        self._next_ip += 1
        self._vms[vm.id] = vm
        return vm

    def remove_vm(self, vm_id: str) -> None:
        self._vms.pop(vm_id, None)

    async def list_keypairs(self) -> list[str]:
        return list(FAKE_KEYPAIRS)

    async def list_disk_offerings(self) -> list[DiskOffering]:
        return list(FAKE_DISK_OFFERINGS)

    async def list_zones(self) -> list[Zone]:
        return list(FAKE_ZONES)

    async def list_service_offerings(self) -> list[ServiceOffering]:
        return list(FAKE_OFFERINGS)

    async def list_templates(self) -> list[Template]:
        return list(FAKE_TEMPLATES)

    async def list_networks(self) -> list[Network]:
        return list(FAKE_NETWORKS)

    async def list_vms(self) -> list[VirtualMachine]:
        return list(self._vms.values())

    async def get_vm(self, vm_id: str) -> VirtualMachine:
        try:
            return self._vms[vm_id]
        except KeyError:
            raise VmNotFound(f"VM {vm_id} not found") from None

    async def _set(self, vm_id: str, allowed_from: str, to: str, verb: str) -> ActionOutcome:
        vm = await self.get_vm(vm_id)
        if vm.state != allowed_from:
            raise CloudStackError(f"Cannot {verb} a VM in state {vm.state}")
        self._vms[vm_id] = vm.model_copy(update={"state": to})
        return ActionOutcome(str(uuid.uuid4()))

    async def start_vm(self, vm_id: str) -> ActionOutcome:
        return await self._set(vm_id, "Stopped", "Running", "start")

    async def stop_vm(self, vm_id: str) -> ActionOutcome:
        return await self._set(vm_id, "Running", "Stopped", "stop")

    async def reboot_vm(self, vm_id: str) -> ActionOutcome:
        return await self._set(vm_id, "Running", "Running", "reboot")
