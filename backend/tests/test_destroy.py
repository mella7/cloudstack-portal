from __future__ import annotations

from .conftest import wait_for_status


def created(client, planned, **overrides):
    job = planned(**overrides)
    client.post(f"/api/jobs/{job['id']}/confirm")
    return wait_for_status(client, job["id"], "done", "failed")


def web_vms(client) -> set[str]:
    return {vm["name"] for vm in client.get("/api/vms").json() if vm["name"].startswith(("web", "failapply"))}


def test_destroy_is_also_two_stage(client, planned, terraform):
    source = created(client, planned)
    assert source["destroyable"] is True and web_vms(client) == {"web-1", "web-2"}

    resp = client.post(f"/api/jobs/{source['id']}/destroy")
    assert resp.status_code == 202
    destroy = resp.json()
    assert destroy["kind"] == "destroy" and destroy["source_job_id"] == source["id"]

    destroy = wait_for_status(client, destroy["id"], "awaiting_confirmation", "failed")
    assert destroy["plan_summary"]["destroy"] == 2 and destroy["plan_summary"]["add"] == 0
    assert {r["details"]["name"] for r in destroy["plan_summary"]["resources"]} == {"web-1", "web-2"}
    # Planned only: the instances are still there.
    assert web_vms(client) == {"web-1", "web-2"}
    assert ("apply-destroy", destroy["id"]) not in terraform.calls

    client.post(f"/api/jobs/{destroy['id']}/confirm")
    assert wait_for_status(client, destroy["id"], "done", "failed")["status"] == "done"
    assert web_vms(client) == set()
    source = client.get(f"/api/jobs/{source['id']}").json()
    assert source["destroyed_at"] is not None and source["destroyable"] is False


def test_rejecting_a_destroy_plan_keeps_the_instances(client, planned):
    source = created(client, planned)
    destroy = client.post(f"/api/jobs/{source['id']}/destroy").json()
    wait_for_status(client, destroy["id"], "awaiting_confirmation")
    client.post(f"/api/jobs/{destroy['id']}/cancel")
    assert web_vms(client) == {"web-1", "web-2"}
    source = client.get(f"/api/jobs/{source['id']}").json()
    assert source["destroyed_at"] is None and source["destroyable"] is True
    # And it can be requested again.
    assert client.post(f"/api/jobs/{source['id']}/destroy").status_code == 202


def test_nothing_to_destroy_before_an_apply_has_started(client, planned):
    waiting = planned()
    resp = client.post(f"/api/jobs/{waiting['id']}/destroy")
    assert resp.status_code == 409 and "has not created anything" in resp.json()["detail"]
    failed_plan = planned(name="failplan-web")
    assert client.post(f"/api/jobs/{failed_plan['id']}/destroy").status_code == 409


def test_only_one_destroy_at_a_time_and_only_once(client, planned):
    source = created(client, planned)
    first = client.post(f"/api/jobs/{source['id']}/destroy").json()
    resp = client.post(f"/api/jobs/{source['id']}/destroy")
    assert resp.status_code == 409 and "already in progress" in resp.json()["detail"]
    wait_for_status(client, first["id"], "awaiting_confirmation")
    client.post(f"/api/jobs/{first['id']}/confirm")
    wait_for_status(client, first["id"], "done")
    resp = client.post(f"/api/jobs/{source['id']}/destroy")
    assert resp.status_code == 409 and "already destroyed" in resp.json()["detail"]


def test_a_destroy_job_cannot_itself_be_destroyed(client, planned):
    source = created(client, planned)
    destroy = client.post(f"/api/jobs/{source['id']}/destroy").json()
    assert client.post(f"/api/jobs/{destroy['id']}/destroy").status_code == 409


def test_leftovers_of_a_failed_apply_can_be_destroyed(client, planned):
    """A failed apply usually leaves some instances behind. They must not be stranded."""
    source = created(client, planned, name="failapply-web", instance_count=3)
    assert source["status"] == "failed" and source["destroyable"] is True
    assert web_vms(client) == {"failapply-web-1"}

    destroy = client.post(f"/api/jobs/{source['id']}/destroy").json()
    destroy = wait_for_status(client, destroy["id"], "awaiting_confirmation")
    assert destroy["plan_summary"]["destroy"] == 1
    client.post(f"/api/jobs/{destroy['id']}/confirm")
    wait_for_status(client, destroy["id"], "done")
    assert web_vms(client) == set()


def test_destroy_of_unknown_job_is_404(client):
    assert client.post("/api/jobs/nope/destroy").status_code == 404
