from __future__ import annotations

import asyncio

from app.check import check_cloudstack, check_terraform
from app.services.cloudstack_api import CloudStackError, FakeCloudStackClient

from .conftest import make_settings


def test_all_lists_are_checked_against_a_working_client():
    results = asyncio.run(check_cloudstack(FakeCloudStackClient()))
    assert [label for label, _, _ in results][:4] == ["zones", "compute offerings", "templates", "networks"]
    assert all(ok for _, ok, _ in results)


def test_bad_credentials_and_empty_required_lists_fail():
    class Broken(FakeCloudStackClient):
        async def list_zones(self):
            raise CloudStackError("listZones: unable to verify user credentials")

        async def list_networks(self):
            return []

        async def list_keypairs(self):
            return []

    results = {label: (ok, detail) for label, ok, detail in asyncio.run(check_cloudstack(Broken()))}
    assert results["zones"] == (False, "listZones: unable to verify user credentials")
    assert results["networks"][0] is False and "needs at least one" in results["networks"][1]
    assert results["SSH keypairs"][0] is True  # optional


def test_missing_terraform_binary_is_reported(tmp_path):
    settings = make_settings(tmp_path, terraform_bin="/nonexistent/terraform")
    label, ok, detail = asyncio.run(check_terraform(settings))
    assert (label, ok) == ("terraform binary", False) and "cannot run" in detail
