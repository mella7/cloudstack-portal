from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.cloudstack_api import (
    FAKE_NETWORKS,
    FAKE_OFFERINGS,
    FAKE_TEMPLATES,
    FAKE_ZONES,
    FakeCloudStackClient,
)
from app.services.terraform import FakeTerraform

API_KEY = "test-api-key-AAAA1111"
SECRET_KEY = "test-secret-key-BBBB2222"


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = dict(
        dry_run=True,
        data_dir=tmp_path / "data",
        terraform_dir=tmp_path / "terraform",
        fake_step_delay_seconds=0,
        cloudstack_api_url="https://cloudstack.test/client/api",
        cloudstack_api_key=API_KEY,
        cloudstack_secret_key=SECRET_KEY,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def cloudstack() -> FakeCloudStackClient:
    return FakeCloudStackClient()


@pytest.fixture
def terraform(cloudstack: FakeCloudStackClient) -> FakeTerraform:
    return FakeTerraform(cloudstack)


@pytest.fixture
def client(settings: Settings, terraform: FakeTerraform, cloudstack: FakeCloudStackClient):
    with TestClient(create_app(settings, terraform, cloudstack)) as c:
        yield c


@pytest.fixture
def form() -> dict[str, Any]:
    return {
        "name": "web",
        "zone": FAKE_ZONES[0].id,
        "service_offering": FAKE_OFFERINGS[0].id,
        "template": FAKE_TEMPLATES[0].id,
        "network": FAKE_NETWORKS[0].id,
        "disk_size_gb": 20,
        "instance_count": 2,
    }


def wait_for_status(client: TestClient, job_id: str, *statuses: str, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in statuses:
            return job
        if time.monotonic() > deadline:
            raise AssertionError(f"job stuck in {job['status']}, wanted {statuses}")
        time.sleep(0.01)


@pytest.fixture
def planned(client: TestClient, form: dict[str, Any]) -> Callable[..., dict[str, Any]]:
    """Submit a job and wait until stage 1 has finished."""

    def _planned(**overrides: Any) -> dict[str, Any]:
        resp = client.post("/api/jobs", json={**form, **overrides})
        assert resp.status_code == 202, resp.text
        return wait_for_status(client, resp.json()["id"], "awaiting_confirmation", "failed")

    return _planned
