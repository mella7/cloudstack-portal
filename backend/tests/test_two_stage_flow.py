from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.jobs.store import JobStore
from app.main import create_app
from app.models.provision import ProvisionRequest
from app.services.cloudstack_api import FAKE_DISK_OFFERINGS
from app.services.terraform import PLAN_FILE, FakeTerraform

from .conftest import wait_for_status


def test_stage_one_stops_at_confirmation_with_a_plan_summary(client, planned, terraform, settings):
    job = planned()
    assert job["status"] == "awaiting_confirmation"
    assert job["plan_summary"]["add"] == 2
    assert [r["action"] for r in job["plan_summary"]["resources"]] == ["create", "create"]
    assert (settings.workdirs_dir / job["id"] / PLAN_FILE).is_file()
    # Nothing was applied and no VM exists yet.
    assert terraform.calls == [("plan", job["id"])]
    assert all(not vm["name"].startswith("web") for vm in client.get("/api/vms").json())


def test_confirm_applies_the_saved_plan(client, planned, terraform):
    job = planned()
    resp = client.post(f"/api/jobs/{job['id']}/confirm")
    assert resp.status_code == 202 and resp.json()["status"] == "applying"
    done = wait_for_status(client, job["id"], "done", "failed")
    assert done["status"] == "done" and done["finished_at"]
    assert terraform.calls == [("plan", job["id"]), ("apply", job["id"])]
    assert {"web-1", "web-2"} <= {vm["name"] for vm in client.get("/api/vms").json()}


def test_confirm_before_the_plan_exists_is_refused(client, form, terraform):
    # A job that has not been planned yet: created directly in the store.
    job = client.app.state.manager.store.create(ProvisionRequest.model_validate(form))
    resp = client.post(f"/api/jobs/{job.id}/confirm")
    assert resp.status_code == 409
    assert terraform.calls == []


def test_confirm_twice_applies_once(client, planned, terraform):
    job = planned()
    assert client.post(f"/api/jobs/{job['id']}/confirm").status_code == 202
    assert client.post(f"/api/jobs/{job['id']}/confirm").status_code == 409
    wait_for_status(client, job["id"], "done")
    assert terraform.calls.count(("apply", job["id"])) == 1


def test_missing_plan_file_fails_the_job_instead_of_applying(client, planned, terraform, settings):
    job = planned()
    (settings.workdirs_dir / job["id"] / PLAN_FILE).unlink()
    assert client.post(f"/api/jobs/{job['id']}/confirm").status_code == 409
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "failed"
    assert ("apply", job["id"]) not in terraform.calls


def test_modified_plan_file_is_never_applied(client, planned, terraform, settings):
    job = planned()
    (settings.workdirs_dir / job["id"] / PLAN_FILE).write_text('{"variables": {"instance_count": 99}}')
    resp = client.post(f"/api/jobs/{job['id']}/confirm")
    assert resp.status_code == 409 and "does not match" in resp.json()["detail"]
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "failed"
    assert ("apply", job["id"]) not in terraform.calls


def test_cancel_rejects_the_plan(client, planned, terraform):
    job = planned()
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/jobs/{job['id']}/confirm").status_code == 409
    assert ("apply", job["id"]) not in terraform.calls


def test_failed_plan_stops_the_job_and_shows_the_error(client, planned, terraform):
    job = planned(name="failplan-web")
    assert job["status"] == "failed"
    assert "terraform plan failed" in job["error"]
    assert job["plan_summary"] is None
    logs = [line["line"] for line in client.get(f"/api/jobs/{job['id']}/logs").json()]
    assert any("Error retrieving ID of template" in line for line in logs)
    assert client.post(f"/api/jobs/{job['id']}/confirm").status_code == 409
    assert ("apply", job["id"]) not in terraform.calls


def test_plan_summary_shows_what_each_instance_will_look_like(planned, form):
    job = planned(keypair="ops-team")
    details = job["plan_summary"]["resources"][1]["details"]
    assert details["name"] == "web-2" and details["root_disk_size"] == "20"
    assert details["keypair"] == "ops-team" and details["zone"] == form["zone"]


SECRET_SCRIPT = "#cloud-config\nwrite_files:\n  - content: db-password-123"


def test_user_data_never_comes_back_from_the_api_or_the_logs(client, planned, settings):
    job = planned(user_data=SECRET_SCRIPT)
    assert job["request"]["user_data"] == "[hidden]"
    client.post(f"/api/jobs/{job['id']}/confirm")
    wait_for_status(client, job["id"], "done")
    everything = "".join(
        client.get(path).text
        for path in (
            "/api/jobs",
            f"/api/jobs/{job['id']}",
            f"/api/jobs/{job['id']}/logs",
            f"/api/jobs/{job['id']}/stream",
        )
    )
    assert "db-password-123" not in everything
    assert "(sensitive value)" in everything
    # Terraform still gets the real thing.
    tfvars = json.loads((settings.workdirs_dir / job["id"] / "terraform.tfvars.json").read_text())
    assert tfvars["user_data"] == SECRET_SCRIPT
    assert planned()["request"]["user_data"] is None


def test_data_disks_are_part_of_the_plan(client, planned, form):
    job = planned(data_disk_offering=FAKE_DISK_OFFERINGS[2].id, data_disk_size_gb=80)
    plan = job["plan_summary"]
    assert plan["add"] == 4
    disks = [r for r in plan["resources"] if r["type"] == "cloudstack_disk"]
    assert [d["details"]["name"] for d in disks] == ["web-1-data", "web-2-data"]
    assert disks[0]["details"]["size"] == "80"
    client.post(f"/api/jobs/{job['id']}/confirm")
    wait_for_status(client, job["id"], "done")
    destroy = client.post(f"/api/jobs/{job['id']}/destroy").json()
    assert wait_for_status(client, destroy["id"], "awaiting_confirmation")["plan_summary"]["destroy"] == 4


def test_instances_can_be_created_stopped(client, planned):
    job = planned(start_vm=False, instance_count=1)
    assert job["plan_summary"]["resources"][0]["details"]["start_vm"] == "false"
    client.post(f"/api/jobs/{job['id']}/confirm")
    wait_for_status(client, job["id"], "done")
    assert next(vm for vm in client.get("/api/vms").json() if vm["name"] == "web")["state"] == "Stopped"


def test_a_running_plan_can_be_cancelled(settings, cloudstack, form):
    slow = FakeTerraform(cloudstack, step_delay=0.05)
    with TestClient(create_app(settings, slow, cloudstack)) as c:
        job = c.post("/api/jobs", json=form).json()
        wait_for_status(c, job["id"], "planning")
        resp = c.post(f"/api/jobs/{job['id']}/cancel")
        assert resp.status_code == 200 and resp.json()["status"] == "cancelled"
        assert c.post(f"/api/jobs/{job['id']}/confirm").status_code == 409
        logs = [line["line"] for line in c.get(f"/api/jobs/{job['id']}/logs").json()]
        assert logs[-1].startswith("Cancelled before the plan finished")


def test_a_running_apply_cannot_be_cancelled(settings, cloudstack, form):
    slow = FakeTerraform(cloudstack, step_delay=0.02)
    with TestClient(create_app(settings, slow, cloudstack)) as c:
        job = c.post("/api/jobs", json={**form, "instance_count": 1}).json()
        wait_for_status(c, job["id"], "awaiting_confirmation")
        c.post(f"/api/jobs/{job['id']}/confirm")
        assert c.post(f"/api/jobs/{job['id']}/cancel").status_code == 409
        assert wait_for_status(c, job["id"], "done", "failed")["status"] == "done"


def test_shutdown_waits_for_a_running_apply(settings, cloudstack, form):
    slow = FakeTerraform(cloudstack, step_delay=0.02)
    with TestClient(create_app(settings, slow, cloudstack)) as c:
        job = c.post("/api/jobs", json={**form, "instance_count": 1}).json()
        wait_for_status(c, job["id"], "awaiting_confirmation")
        assert c.post(f"/api/jobs/{job['id']}/confirm").json()["status"] == "applying"
    # The app has shut down. The apply was allowed to finish instead of being killed.
    store = JobStore(settings.db_path)
    assert store.get(job["id"]).status.value == "done"
    store.close()


def test_failed_apply_marks_the_job_failed(client, planned):
    job = planned(name="failapply-web")
    assert job["status"] == "awaiting_confirmation"
    client.post(f"/api/jobs/{job['id']}/confirm")
    failed = wait_for_status(client, job["id"], "done", "failed")
    assert failed["status"] == "failed"
    assert "terraform apply failed" in failed["error"]


def test_unknown_job_is_404(client):
    for method, path in [("get", ""), ("get", "/logs"), ("get", "/stream"), ("post", "/confirm"), ("post", "/cancel")]:
        assert getattr(client, method)(f"/api/jobs/does-not-exist{path}").status_code == 404


def test_jobs_are_listed_newest_first(client, planned):
    first, second = planned(name="one"), planned(name="two")
    assert [j["id"] for j in client.get("/api/jobs").json()] == [second["id"], first["id"]]
