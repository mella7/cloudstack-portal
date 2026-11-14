"""Preflight check before the first real run:  python -m app.check

Reads the same environment as the backend, then checks that Terraform is
installed and that the CloudStack credentials work, using read-only API calls.
It creates nothing."""

from __future__ import annotations

import asyncio
import sys

from .config import Settings, get_settings
from .services.cloudstack_api import CloudStackClient, CloudStackError, SignedCloudStackClient

Result = tuple[str, bool, str]


async def check_terraform(settings: Settings) -> Result:
    try:
        proc = await asyncio.create_subprocess_exec(
            settings.terraform_bin,
            "version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
    except OSError as exc:
        return ("terraform binary", False, f"cannot run '{settings.terraform_bin}': {exc.strerror or exc}")
    first_line = out.decode(errors="replace").splitlines()[0] if out else ""
    return ("terraform binary", proc.returncode == 0, first_line)


async def check_cloudstack(client: CloudStackClient) -> list[Result]:
    """One read-only call per list the form needs. An empty list is a failure too:
    the form could not be filled in."""
    calls = [
        ("zones", client.list_zones, True),
        ("compute offerings", client.list_service_offerings, True),
        ("templates", client.list_templates, True),
        ("networks", client.list_networks, True),
        ("SSH keypairs", client.list_keypairs, False),
        ("disk offerings", client.list_disk_offerings, False),
        ("virtual machines", client.list_vms, False),
    ]
    results: list[Result] = []
    for label, call, required in calls:
        try:
            items = await call()
        except CloudStackError as exc:
            results.append((label, False, str(exc)))
            continue
        ok = bool(items) or not required
        results.append((label, ok, f"{len(items)} found" + ("" if ok else " - the form needs at least one")))
    return results


async def main() -> int:
    settings = get_settings()
    if settings.dry_run:
        print("DRY_RUN is on: nothing to check. Set DRY_RUN=false and the CLOUDSTACK_* variables first.")
        return 1
    try:
        settings.check_live_config()
    except RuntimeError as exc:
        print(f"FAIL  configuration: {exc}")
        return 1
    client = SignedCloudStackClient(
        settings.cloudstack_api_url,
        settings.cloudstack_api_key.get_secret_value(),
        settings.cloudstack_secret_key.get_secret_value(),
    )
    try:
        results = [await check_terraform(settings), *await check_cloudstack(client)]
    finally:
        await client.aclose()
    for label, ok, detail in results:
        print(f"{'ok  ' if ok else 'FAIL'}  {label}: {detail}")
    failed = sum(1 for _, ok, _ in results if not ok)
    print("All checks passed." if not failed else f"{failed} check(s) failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
