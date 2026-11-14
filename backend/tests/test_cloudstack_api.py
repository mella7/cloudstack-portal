from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.vm import VirtualMachine
from app.services.cloudstack_api import (
    ActionOutcome,
    CloudStackError,
    SignedCloudStackClient,
    VmNotFound,
    sign_params,
)
from app.services.terraform import FakeTerraform

from .conftest import API_KEY, SECRET_KEY

VM_ID = "aaaaaaaa-0000-4000-8000-000000000001"


def test_signature_matches_the_documented_algorithm():
    params = {"command": "listVirtualMachines", "response": "json", "apiKey": "MyKey", "name": "My VM~1"}
    # Sorted by key, values URL-encoded with %20 for spaces, everything lowercased.
    expected_string = "apikey=mykey&command=listvirtualmachines&name=my%20vm%7e1&response=json"
    expected = base64.b64encode(hmac.new(b"s3cret", expected_string.encode(), hashlib.sha1).digest()).decode()
    assert sign_params(params, "s3cret") == expected
    assert sign_params(dict(reversed(params.items())), "s3cret") == expected
    assert sign_params(params, "other") != expected


def signed_client(handler, **kwargs) -> SignedCloudStackClient:
    return SignedCloudStackClient(
        "https://cloudstack.test/client/api",
        API_KEY,
        SECRET_KEY,
        transport=httpx.MockTransport(handler),
        poll_interval=0,
        **kwargs,
    )


def async_job_handler(*statuses: dict):
    """Answers the action with a job id, then each queryAsyncJobResult with the next status."""
    seen: list[httpx.Request] = []
    replies = iter(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params["command"] == "queryAsyncJobResult":
            assert request.url.params["jobid"] == "job-1"
            return httpx.Response(200, json={"queryasyncjobresultresponse": next(replies)})
        return httpx.Response(200, json={"startvirtualmachineresponse": {"jobid": "job-1"}})

    return handler, seen


def test_requests_are_signed_and_never_carry_the_secret():
    handler, seen = async_job_handler({"jobstatus": 1})
    assert asyncio.run(signed_client(handler).start_vm(VM_ID)) == ActionOutcome("job-1", completed=True)
    params = dict(seen[0].url.params)
    assert params["command"] == "startVirtualMachine" and params["id"] == VM_ID
    assert params["apiKey"] == API_KEY
    unsigned = {k: v for k, v in params.items() if k != "signature"}
    assert params["signature"] == sign_params(unsigned, SECRET_KEY)
    assert SECRET_KEY not in str(seen[0].url)


def test_power_action_waits_for_the_async_job():
    handler, seen = async_job_handler({"jobstatus": 0}, {"jobstatus": 0}, {"jobstatus": 1})
    assert asyncio.run(signed_client(handler).start_vm(VM_ID)).completed is True
    assert [r.url.params["command"] for r in seen] == ["startVirtualMachine"] + ["queryAsyncJobResult"] * 3


def test_failed_async_job_is_an_error_not_a_success():
    handler, _ = async_job_handler(
        {"jobstatus": 2, "jobresult": {"errorcode": 530, "errortext": "Insufficient capacity"}}
    )
    with pytest.raises(CloudStackError, match="Insufficient capacity"):
        asyncio.run(signed_client(handler).start_vm(VM_ID))


def test_slow_async_job_is_reported_as_not_completed():
    handler, _ = async_job_handler({"jobstatus": 0})
    outcome = asyncio.run(signed_client(handler, action_timeout=0).start_vm(VM_ID))
    assert outcome == ActionOutcome("job-1", completed=False)


def test_list_calls_fetch_every_page():
    pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        pages.append(request.url.params["page"])
        assert request.url.params["pagesize"] == "2"
        vms = [{"id": f"vm-{i}", "name": f"vm-{i}", "state": "Running"} for i in range(5)][(page - 1) * 2 : page * 2]
        return httpx.Response(200, json={"listvirtualmachinesresponse": {"count": 5, "virtualmachine": vms}})

    vms = asyncio.run(signed_client(handler, page_size=2).list_vms())
    assert [vm.name for vm in vms] == [f"vm-{i}" for i in range(5)]
    assert pages == ["1", "2", "3"]


def test_keypairs_are_listed_by_name():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"listsshkeypairsresponse": {"count": 1, "sshkeypair": [{"name": "ops"}]}})

    assert asyncio.run(signed_client(handler).list_keypairs()) == ["ops"]


def test_disk_offerings_distinguish_fixed_and_custom_size():
    def handler(_: httpx.Request) -> httpx.Response:
        offerings = [
            {"id": "d1", "name": "50 GB", "disksize": 50, "iscustomized": False},
            {"id": "d2", "name": "Custom", "disksize": 0, "iscustomized": True},
        ]
        return httpx.Response(200, json={"listdiskofferingsresponse": {"count": 2, "diskoffering": offerings}})

    fixed, custom = asyncio.run(signed_client(handler).list_disk_offerings())
    assert (fixed.size_gb, fixed.customized) == (50, False)
    assert (custom.size_gb, custom.customized) == (None, True)


def test_vm_list_is_parsed():
    def handler(_: httpx.Request) -> httpx.Response:
        vm = {"id": VM_ID, "name": "web-1", "state": "Running", "zonename": "zrh-1", "nic": [{"ipaddress": "10.0.0.5"}]}
        return httpx.Response(200, json={"listvirtualmachinesresponse": {"count": 1, "virtualmachine": [vm]}})

    vms = asyncio.run(signed_client(handler).list_vms())
    assert vms == [VirtualMachine(id=VM_ID, name="web-1", state="Running", zone_name="zrh-1", ip_address="10.0.0.5")]


def test_api_errors_become_cloudstack_errors():
    def handler(_: httpx.Request) -> httpx.Response:
        body = {"stopvirtualmachineresponse": {"errorcode": 431, "errortext": "Unable to find VM"}}
        return httpx.Response(431, json=body)

    with pytest.raises(CloudStackError, match="Unable to find VM"):
        asyncio.run(signed_client(handler).stop_vm(VM_ID))


def test_network_errors_do_not_leak_credentials():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect to {request.url}")

    with pytest.raises(CloudStackError) as exc:
        asyncio.run(signed_client(handler).list_vms())
    assert API_KEY not in str(exc.value) and "signature" not in str(exc.value)


class RecordingClient:
    """A mock behind the CloudStackClient interface, as the routes see it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.error: Exception | None = None
        self.completed = True

    async def _do(self, action: str, vm_id: str) -> ActionOutcome:
        self.calls.append((action, vm_id))
        if self.error:
            raise self.error
        return ActionOutcome("async-job-1", self.completed)

    async def start_vm(self, vm_id: str) -> ActionOutcome:
        return await self._do("start", vm_id)

    async def stop_vm(self, vm_id: str) -> ActionOutcome:
        return await self._do("stop", vm_id)

    async def reboot_vm(self, vm_id: str) -> ActionOutcome:
        return await self._do("reboot", vm_id)

    async def get_vm(self, vm_id: str) -> VirtualMachine:
        await self._do("get", vm_id)
        return VirtualMachine(id=vm_id, name="web-1", state="Running")

    async def list_vms(self) -> list[VirtualMachine]:
        return [VirtualMachine(id=VM_ID, name="web-1", state="Running")]


@pytest.fixture
def mock_cs():
    return RecordingClient()


@pytest.fixture
def api(settings, mock_cs):
    with TestClient(create_app(settings, FakeTerraform(), mock_cs)) as c:
        yield c


@pytest.mark.parametrize("action", ["start", "stop", "reboot"])
def test_power_actions_call_the_client(api, mock_cs, action):
    resp = api.post(f"/api/vms/{VM_ID}/{action}")
    assert resp.status_code == 202
    assert resp.json() == {"vm_id": VM_ID, "action": action, "async_job_id": "async-job-1", "completed": True}
    assert mock_cs.calls == [(action, VM_ID)]


def test_unfinished_action_is_passed_on_to_the_caller(api, mock_cs):
    mock_cs.completed = False
    assert api.post(f"/api/vms/{VM_ID}/stop").json()["completed"] is False


def test_live_status_comes_from_the_client(api, mock_cs):
    assert api.get(f"/api/vms/{VM_ID}").json()["state"] == "Running"
    assert api.get("/api/vms").json()[0]["name"] == "web-1"
    assert mock_cs.calls == [("get", VM_ID)]


@pytest.mark.parametrize("path", [f"/api/vms/{VM_ID}/destroy", "/api/vms/not-a-uuid/start", "/api/vms/1;reboot/stop"])
def test_unknown_actions_and_bad_ids_never_reach_the_client(api, mock_cs, path):
    assert api.post(path).status_code == 422
    assert mock_cs.calls == []


def test_client_failures_map_to_http_errors(api, mock_cs):
    mock_cs.error = CloudStackError("stopVirtualMachine: boom")
    assert api.post(f"/api/vms/{VM_ID}/stop").status_code == 502
    mock_cs.error = VmNotFound("gone")
    assert api.post(f"/api/vms/{uuid.uuid4()}/stop").status_code == 404
