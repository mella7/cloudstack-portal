from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.catalog import Catalog, Limits
from app.models.provision import ProvisionRequest
from app.services.cloudstack_api import FAKE_DISK_OFFERINGS, FAKE_NETWORKS, FAKE_OFFERINGS, FAKE_TEMPLATES, FAKE_ZONES

CATALOG = Catalog(
    zones=FAKE_ZONES,
    service_offerings=FAKE_OFFERINGS,
    templates=FAKE_TEMPLATES,
    networks=FAKE_NETWORKS,
    keypairs=["ops-team"],
    disk_offerings=FAKE_DISK_OFFERINGS,
    limits=Limits(min_disk_gb=10, max_disk_gb=500, max_instances=5),
)


def validate(form, **overrides):
    return ProvisionRequest.model_validate({**form, **overrides}, context={"catalog": CATALOG})


def test_valid_form_passes(form):
    req = validate(form)
    assert req.instance_names() == ["web-1", "web-2"]
    assert req.to_tfvars()["root_disk_size_gb"] == 20


@pytest.mark.parametrize(
    "name",
    ["Web", "1web", "web_1", "web-", "-web", "", "a" * 51, "web; rm -rf /", "$(id)", "web`id`", "web name", "wéb"],
)
def test_bad_names_are_rejected(form, name):
    with pytest.raises(ValidationError):
        validate(form, name=name)


@pytest.mark.parametrize("size", [0, 9, 501, -5, "big"])
def test_disk_size_out_of_range(form, size):
    with pytest.raises(ValidationError):
        validate(form, disk_size_gb=size)


@pytest.mark.parametrize("count", [0, 6, -1, 1.5])
def test_instance_count_out_of_range(form, count):
    with pytest.raises(ValidationError):
        validate(form, instance_count=count)


@pytest.mark.parametrize("field", ["zone", "service_offering", "template", "network"])
def test_values_outside_the_catalog_are_rejected(form, field):
    with pytest.raises(ValidationError) as exc:
        validate(form, **{field: "99999999-0000-4000-8000-000000000000"})
    assert exc.value.errors()[0]["ctx"]["field"] == field


def test_network_must_belong_to_the_zone(form):
    with pytest.raises(ValidationError, match="not in this zone"):
        validate(form, network=FAKE_NETWORKS[2].id)


def test_keypair_is_optional_but_must_exist(form):
    assert validate(form).keypair is None
    assert validate(form, keypair="").keypair is None
    assert validate(form, keypair="ops-team").to_tfvars()["keypair"] == "ops-team"
    for bad in ["someone-elses-key", "ops team", "../etc/passwd"]:
        with pytest.raises(ValidationError):
            validate(form, keypair=bad)


def test_defaults_for_the_optional_settings(form):
    tfvars = validate(form).to_tfvars()
    assert tfvars["start_vm"] is True and tfvars["tags"] == {}
    assert all(tfvars[k] is None for k in ("group", "user_data", "data_disk_offering", "data_disk_size_gb"))


def test_tags_group_and_start_vm_reach_terraform(form):
    tfvars = validate(form, tags={"env": "prod", "cost-center": "A 12"}, group="web-tier", start_vm=False).to_tfvars()
    assert tfvars["tags"] == {"env": "prod", "cost-center": "A 12"}
    assert tfvars["group"] == "web-tier" and tfvars["start_vm"] is False


@pytest.mark.parametrize(
    "bad",
    [
        {"tags": {"1env": "x"}},
        {"tags": {"env prod": "x"}},
        {"tags": {"env": ""}},
        {"tags": {"env": "line\nbreak"}},
        {"tags": {f"k{i}": "v" for i in range(11)}},
        {"tags": {"env": 5}},
        {"group": "web tier"},
        {"group": "x" * 65},
        {"start_vm": "maybe"},
    ],
)
def test_bad_optional_settings_are_rejected(form, bad):
    with pytest.raises(ValidationError):
        validate(form, **bad)


def test_user_data_is_passed_through_untouched_but_limited_in_size(form):
    script = '#cloud-config\npackages:\n  - nginx\nruncmd:\n  - echo "$(id); rm -rf /tmp/x" > /root/out'
    assert validate(form, user_data=script).to_tfvars()["user_data"] == script
    assert validate(form, user_data="").user_data is None
    with pytest.raises(ValidationError, match="16 KB"):
        validate(form, user_data="x" * (16 * 1024 + 1))


def test_data_disk_rules(form):
    fixed, custom = FAKE_DISK_OFFERINGS[0].id, FAKE_DISK_OFFERINGS[2].id
    assert validate(form, data_disk_offering=fixed).to_tfvars()["data_disk_offering"] == fixed
    assert validate(form, data_disk_offering=custom, data_disk_size_gb=80).to_tfvars()["data_disk_size_gb"] == 80
    for bad in [
        {"data_disk_offering": "99999999-0000-4000-8000-000000000000"},  # not in the catalog
        {"data_disk_offering": fixed, "data_disk_size_gb": 80},  # fixed-size offering
        {"data_disk_offering": custom},  # custom offering needs a size
        {"data_disk_offering": custom, "data_disk_size_gb": 501},  # above the limit
        {"data_disk_size_gb": 80},  # size without an offering
    ]:
        with pytest.raises(ValidationError):
            validate(form, **bad)


def test_unknown_fields_are_rejected(form):
    with pytest.raises(ValidationError):
        validate(form, admin_password="hunter2")


def test_missing_field_is_rejected(form):
    del form["template"]
    with pytest.raises(ValidationError):
        validate(form)


def test_api_returns_field_errors(client, form):
    resp = client.post("/api/jobs", json={**form, "name": "Bad Name"})
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["field"] == "name"


def test_api_checks_allowed_values_and_creates_no_job(client, form):
    resp = client.post("/api/jobs", json={**form, "zone": "not-a-real-zone"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == [{"field": "zone", "message": "unknown zone"}]
    assert client.get("/api/jobs").json() == []


def test_catalog_endpoint_lists_allowed_values(client):
    catalog = client.get("/api/catalog").json()
    assert {z["name"] for z in catalog["zones"]} == {"zrh-1", "fra-1"}
    assert catalog["limits"] == {"min_disk_gb": 10, "max_disk_gb": 500, "max_instances": 5}
