"""Terraform behind a small interface: RealTerraform shells out to the CLI
(argument lists only, never a shell), FakeTerraform is used for DRY_RUN."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from ..config import Settings
from ..models.job import PlannedResource, PlanSummary
from .cloudstack_api import FakeCloudStackClient

LogFn = Callable[[str], Awaitable[None]]

PLAN_FILE = "tfplan"
TFVARS_FILE = "terraform.tfvars.json"


class TerraformError(Exception):
    pass


class TerraformRunner(Protocol):
    async def plan(
        self, job_id: str, workdir: Path, variables: dict[str, Any], log: LogFn, *, state_id: str, destroy: bool = False
    ) -> PlanSummary:
        """init + validate + plan. Must leave the saved plan at workdir/PLAN_FILE.

        `state_id` names the Terraform state to work on. A create job uses its
        own id; a destroy job uses the id of the job whose instances it removes."""
        ...

    async def apply(self, job_id: str, workdir: Path, log: LogFn, *, state_id: str, destroy: bool = False) -> None:
        """Apply exactly the plan saved at workdir/PLAN_FILE."""
        ...


# Attributes shown to the user when reviewing a plan.
REVIEW_KEYS = (
    "name",
    "zone",
    "service_offering",
    "template",
    "network_id",
    "root_disk_size",
    "keypair",
    "group",
    "start_vm",
    "disk_offering",
    "size",
)


def _details(change: dict[str, Any]) -> dict[str, str]:
    values = change.get("after") or change.get("before") or {}
    return {k: _text(values[k]) for k in REVIEW_KEYS if values.get(k) is not None}


def _text(value: Any) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def summarize_plan_json(plan: dict[str, Any]) -> PlanSummary:
    """Build the user-facing summary from `terraform show -json <plan>`."""
    summary = PlanSummary()
    for rc in plan.get("resource_changes", []):
        actions = rc.get("change", {}).get("actions", [])
        if actions in (["no-op"], ["read"]):
            continue
        if "create" in actions and "delete" in actions:
            action = "replace"
            summary.add += 1
            summary.destroy += 1
        elif "create" in actions:
            action = "create"
            summary.add += 1
        elif "delete" in actions:
            action = "delete"
            summary.destroy += 1
        else:
            action = "update"
            summary.change += 1
        summary.resources.append(
            PlannedResource(
                address=rc["address"],
                type=rc["type"],
                name=rc["name"],
                action=action,
                details=_details(rc.get("change", {})),
            )
        )
    return summary


class RealTerraform:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def _env(self) -> dict[str, str]:
        # A minimal environment. Credentials reach the provider only this way.
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", str(self._s.data_dir)),
            "TF_IN_AUTOMATION": "1",
            "TF_INPUT": "0",
            "CLOUDSTACK_API_URL": self._s.cloudstack_api_url,
            "CLOUDSTACK_API_KEY": self._s.cloudstack_api_key.get_secret_value(),
            "CLOUDSTACK_SECRET_KEY": self._s.cloudstack_secret_key.get_secret_value(),
        }
        for passthrough in (
            "TF_PLUGIN_CACHE_DIR",
            "TF_CLI_CONFIG_FILE",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "NO_PROXY",
            "SSL_CERT_FILE",
        ):
            if passthrough in os.environ:
                env[passthrough] = os.environ[passthrough]
        return env

    async def _run(self, args: list[str], workdir: Path, log: LogFn | None) -> str:
        """Run one terraform command. Streams lines to `log` if given, returns stdout+stderr."""
        if log:
            await log(f"$ terraform {' '.join(args)}")
        try:
            proc = await asyncio.create_subprocess_exec(
                self._s.terraform_bin,
                *args,
                cwd=workdir,
                env=self._env(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            raise TerraformError(f"could not start terraform: {exc.strerror or exc}") from None
        assert proc.stdout is not None
        captured: list[str] = []
        try:
            async with asyncio.timeout(self._s.terraform_timeout_seconds):
                async for raw in proc.stdout:
                    line = raw.decode(errors="replace").rstrip("\r\n")
                    captured.append(line)
                    if log:
                        await log(line)
                code = await proc.wait()
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise TerraformError(f"terraform {args[0]} timed out") from None
        except asyncio.CancelledError:
            # Let Terraform stop cleanly and write its state before we give up on it.
            proc.terminate()
            try:
                await asyncio.shield(asyncio.wait_for(proc.wait(), 20))
            except (TimeoutError, asyncio.CancelledError):
                proc.kill()
            raise
        if code != 0:
            raise TerraformError(f"terraform {args[0]} failed with exit code {code}")
        return "\n".join(captured)

    async def plan(
        self, job_id: str, workdir: Path, variables: dict[str, Any], log: LogFn, *, state_id: str, destroy: bool = False
    ) -> PlanSummary:
        shutil.copytree(
            self._s.terraform_dir,
            workdir,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".terraform", "*.tfstate*", "*.tfvars*", PLAN_FILE),
        )
        # User input goes into a JSON variables file, never onto a command line.
        (workdir / TFVARS_FILE).write_text(json.dumps(variables, indent=2))
        state_path = self._s.state_dir / state_id / "terraform.tfstate"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        await self._run(["init", "-input=false", "-no-color", f"-backend-config=path={state_path}"], workdir, log)
        await self._run(["validate", "-no-color"], workdir, log)
        plan_args = ["plan", "-input=false", "-no-color", f"-var-file={TFVARS_FILE}", f"-out={PLAN_FILE}"]
        if destroy:
            plan_args.insert(1, "-destroy")
        await self._run(plan_args, workdir, log)
        # Not streamed: the JSON plan can contain sensitive values.
        raw = await self._run(["show", "-json", PLAN_FILE], workdir, None)
        try:
            return summarize_plan_json(json.loads(raw[raw.index("{") :]))
        except ValueError:
            raise TerraformError("could not read the saved plan") from None

    async def apply(self, job_id: str, workdir: Path, log: LogFn, *, state_id: str, destroy: bool = False) -> None:
        await self._run(["apply", "-input=false", "-no-color", PLAN_FILE], workdir, log)
        if not destroy:
            await self._run(["output", "-no-color"], workdir, log)


class FakeTerraform:
    """DRY_RUN stand-in. Produces Terraform-looking output and never spawns a process.

    To exercise error handling, a name starting with "failplan" fails stage 1
    and a name starting with "failapply" fails stage 2 after the first instance,
    which leaves a partial result behind like a real failed apply would."""

    def __init__(self, cloudstack: FakeCloudStackClient | None = None, step_delay: float = 0.0) -> None:
        self._cs = cloudstack
        self._delay = step_delay
        self.calls: list[tuple[str, str]] = []
        # The fake "state": what each state_id owns, as (address, type, id, name). In memory only.
        self._state: dict[str, list[tuple[str, str, str, str]]] = {}

    async def _emit(self, log: LogFn, *lines: str) -> None:
        for line in lines:
            await log(line)
            if self._delay:
                await asyncio.sleep(self._delay)

    @staticmethod
    def _names(v: dict[str, Any]) -> list[str]:
        n = v["instance_count"]
        return [v["name"]] if n == 1 else [f"{v['name']}-{i + 1}" for i in range(n)]

    @staticmethod
    def _planned(v: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
        """(address, type, attributes) of everything the configuration would create."""
        names = FakeTerraform._names(v)
        out: list[tuple[str, str, dict[str, Any]]] = []
        for i, name in enumerate(names):
            attrs: dict[str, Any] = {
                "name": name,
                "network_id": v["network_id"],
                "root_disk_size": v["root_disk_size_gb"],
                "service_offering": v["service_offering"],
                "start_vm": v.get("start_vm", True),
                "template": v["template"],
                "zone": v["zone"],
            }
            for key in ("keypair", "group"):
                if v.get(key):
                    attrs[key] = v[key]
            if v.get("tags"):
                attrs["tags"] = v["tags"]
            if v.get("user_data"):
                attrs["user_data"] = "(sensitive value)"
            out.append((f"module.vm.cloudstack_instance.this[{i}]", "cloudstack_instance", attrs))
        if v.get("data_disk_offering"):
            for i, name in enumerate(names):
                attrs = {"name": f"{name}-data", "disk_offering": v["data_disk_offering"], "zone": v["zone"]}
                if v.get("data_disk_size_gb"):
                    attrs["size"] = v["data_disk_size_gb"]
                out.append((f"module.vm.cloudstack_disk.data[{i}]", "cloudstack_disk", attrs))
        return out

    @staticmethod
    def _render(value: Any) -> str:
        return value if value == "(sensitive value)" else json.dumps(value)

    async def plan(
        self, job_id: str, workdir: Path, variables: dict[str, Any], log: LogFn, *, state_id: str, destroy: bool = False
    ) -> PlanSummary:
        self.calls.append(("plan-destroy" if destroy else "plan", job_id))
        flag = " -destroy" if destroy else ""
        await self._emit(
            log,
            "[dry-run] $ terraform init -input=false -no-color",
            "Initializing the backend...",
            "Initializing modules...",
            "- vm in modules/vm",
            "Initializing provider plugins...",
            '- Finding cloudstack/cloudstack versions matching "~> 0.6.0"...',
            "- Installing cloudstack/cloudstack v0.6.0...",
            "Terraform has been successfully initialized!",
            "[dry-run] $ terraform validate -no-color",
            "Success! The configuration is valid.",
            f"[dry-run] $ terraform plan{flag} -input=false -no-color -var-file={TFVARS_FILE} -out={PLAN_FILE}",
        )
        if variables["name"].startswith("failplan"):
            await self._emit(
                log,
                "Error: Error retrieving ID of template: no match found",
                "  with module.vm.cloudstack_instance.this[0],",
                '  on modules/vm/main.tf line 9, in resource "cloudstack_instance" "this":',
            )
            raise TerraformError("terraform plan failed with exit code 1")

        resources: list[PlannedResource] = []
        if destroy:
            owned = self._state.get(state_id, [])
            if owned:
                await self._emit(log, "Terraform will perform the following actions:", "")
            for address, rtype, rid, name in reversed(owned):  # disks go before the instances they hang on
                resources.append(
                    PlannedResource(
                        address=address,
                        type=rtype,
                        name=address.split(".")[-1].split("[")[0],
                        action="delete",
                        details={"name": name},
                    )
                )
                await self._emit(
                    log,
                    f"  # {address} will be destroyed",
                    f'  - resource "{rtype}" "{address.split(".")[-1].split("[")[0]}" {{',
                    f'      - id   = "{rid}" -> null',
                    f'      - name = "{name}" -> null',
                    "    }",
                    "",
                )
            if not owned:
                await self._emit(log, "No changes. No objects need to be destroyed.")
            await self._emit(log, f"Plan: 0 to add, 0 to change, {len(owned)} to destroy.")
            summary = PlanSummary(destroy=len(owned), resources=resources)
        else:
            await self._emit(log, "Terraform will perform the following actions:", "")
            for address, rtype, attrs in self._planned(variables):
                label = address.split(".")[-1].split("[")[0]
                resources.append(
                    PlannedResource(
                        address=address,
                        type=rtype,
                        name=label,
                        action="create",
                        details={k: _text(v) for k, v in attrs.items() if k in REVIEW_KEYS},
                    )
                )
                width = max(len(k) for k in attrs)
                await self._emit(
                    log,
                    f"  # {address} will be created",
                    f'  + resource "{rtype}" "{label}" {{',
                    f"      + {'id'.ljust(width)} = (known after apply)",
                    *[f"      + {k.ljust(width)} = {self._render(v)}" for k, v in sorted(attrs.items())],
                    "    }",
                    "",
                )
            await self._emit(log, f"Plan: {len(resources)} to add, 0 to change, 0 to destroy.")
            summary = PlanSummary(add=len(resources), resources=resources)

        await self._emit(log, f"Saved the plan to: {PLAN_FILE}")
        (workdir / TFVARS_FILE).write_text(json.dumps(variables, indent=2))
        (workdir / PLAN_FILE).write_text(
            json.dumps({"dry_run": True, "job_id": job_id, "destroy": destroy, "variables": variables})
        )
        return summary

    async def apply(self, job_id: str, workdir: Path, log: LogFn, *, state_id: str, destroy: bool = False) -> None:
        saved = json.loads((workdir / PLAN_FILE).read_text())
        destroy = bool(saved.get("destroy"))  # the saved plan decides, like the real thing
        self.calls.append(("apply-destroy" if destroy else "apply", job_id))
        variables = saved["variables"]
        await self._emit(log, f"[dry-run] $ terraform apply -input=false -no-color {PLAN_FILE}")

        if destroy:
            owned = self._state.pop(state_id, [])
            for address, rtype, rid, _name in reversed(owned):
                await self._emit(log, f"{address}: Destroying... [id={rid}]")
                if self._cs is not None and rtype == "cloudstack_instance":
                    self._cs.remove_vm(rid)
                await self._emit(log, f"{address}: Destruction complete after 6s")
            await self._emit(log, "", f"Apply complete! Resources: 0 added, 0 changed, {len(owned)} destroyed.")
            return

        planned = self._planned(variables)
        instance_count = sum(1 for _, rtype, _ in planned if rtype == "cloudstack_instance")
        owned = self._state.setdefault(state_id, [])
        for i, (address, rtype, attrs) in enumerate(planned):
            await self._emit(log, f"{address}: Creating...")
            if variables["name"].startswith("failapply") and (i >= 1 or instance_count == 1):
                await self._emit(
                    log,
                    "Error: Error creating the new instance: CloudStack API error 431 (CSExceptionErrorCode: 4350): "
                    "Maximum number of resources of type 'user_vm' for account has been exceeded",
                )
                raise TerraformError("terraform apply failed with exit code 1")
            rid = f"dry-run-{i}"
            if rtype == "cloudstack_instance":
                await self._emit(log, f"{address}: Still creating... [10s elapsed]")
                ip = "10.1.1.x"
                if self._cs is not None:
                    vm = self._cs.add_vm(
                        attrs["name"],
                        variables["zone"],
                        variables["service_offering"],
                        variables["template"],
                        state="Running" if attrs["start_vm"] else "Stopped",
                    )
                    ip, rid = vm.ip_address or ip, vm.id
                owned.append((address, rtype, rid, attrs["name"]))
                await self._emit(
                    log, f"{address}: Creation complete after 14s [id={rid}]", f"  {attrs['name']} -> {ip}"
                )
            else:
                owned.append((address, rtype, rid, attrs["name"]))
                await self._emit(log, f"{address}: Creation complete after 3s [id={rid}]")
        await self._emit(log, "", f"Apply complete! Resources: {len(planned)} added, 0 changed, 0 destroyed.")
