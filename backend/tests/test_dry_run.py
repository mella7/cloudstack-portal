from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import build_backends, create_app
from app.services.cloudstack_api import CloudStackError, FakeCloudStackClient, SignedCloudStackClient
from app.services.terraform import FakeTerraform, RealTerraform

from .conftest import make_settings, wait_for_status


def test_dry_run_selects_the_fake_implementations(tmp_path):
    terraform, cloudstack = build_backends(make_settings(tmp_path, dry_run=True))
    assert isinstance(terraform, FakeTerraform) and isinstance(cloudstack, FakeCloudStackClient)


def test_live_mode_selects_the_real_implementations(tmp_path):
    terraform, cloudstack = build_backends(make_settings(tmp_path, dry_run=False))
    assert isinstance(terraform, RealTerraform) and isinstance(cloudstack, SignedCloudStackClient)


def test_live_mode_refuses_to_start_without_credentials(tmp_path):
    settings = make_settings(tmp_path, dry_run=False, cloudstack_api_key="", cloudstack_secret_key="")
    with pytest.raises(RuntimeError, match="CLOUDSTACK_API_KEY, CLOUDSTACK_SECRET_KEY"):
        build_backends(settings)


def test_dry_run_needs_no_credentials_and_reports_itself(tmp_path):
    settings = make_settings(tmp_path, cloudstack_api_url="", cloudstack_api_key="", cloudstack_secret_key="")
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/health").json() == {"status": "ok", "dry_run": True, "plan_ttl_minutes": 60}


def test_dry_run_flow_never_spawns_a_process_or_opens_a_connection(tmp_path, form, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("DRY_RUN must not touch the outside world")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr("httpx.AsyncClient.send", forbidden)
    with TestClient(create_app(make_settings(tmp_path))) as c:
        job = c.post("/api/jobs", json={**form, "instance_count": 1}).json()
        wait_for_status(c, job["id"], "awaiting_confirmation")
        c.post(f"/api/jobs/{job['id']}/confirm")
        assert wait_for_status(c, job["id"], "done", "failed")["status"] == "done"
        logs = [line["line"] for line in c.get(f"/api/jobs/{job['id']}/logs").json()]
        assert "Plan: 1 to add, 0 to change, 0 to destroy." in logs
        assert "Apply complete! Resources: 1 added, 0 changed, 0 destroyed." in logs
        web = next(vm for vm in c.get("/api/vms").json() if vm["name"] == "web")
        assert web["state"] == "Running" and web["zone_name"] == "zrh-1"


def test_fake_vm_lifecycle(client):
    vm = next(v for v in client.get("/api/vms").json() if v["state"] == "Running")
    assert client.post(f"/api/vms/{vm['id']}/stop").status_code == 202
    assert client.get(f"/api/vms/{vm['id']}").json()["state"] == "Stopped"
    assert client.post(f"/api/vms/{vm['id']}/stop").status_code == 502  # already stopped
    assert client.post(f"/api/vms/{vm['id']}/start").status_code == 202
    assert client.post(f"/api/vms/{vm['id']}/reboot").status_code == 202
    assert client.get(f"/api/vms/{vm['id']}").json()["state"] == "Running"


def test_fake_client_rejects_impossible_actions():
    fake = FakeCloudStackClient()
    stopped = next(v for v in asyncio.run(fake.list_vms()) if v.state == "Stopped")
    with pytest.raises(CloudStackError, match="Cannot reboot"):
        asyncio.run(fake.reboot_vm(stopped.id))
