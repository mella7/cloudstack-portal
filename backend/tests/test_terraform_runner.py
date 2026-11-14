"""RealTerraform against a stub `terraform` executable, so the argument lists,
the environment and the error handling are tested without CloudStack."""

from __future__ import annotations

import asyncio
import json
import stat
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.provision import ProvisionRequest
from app.services.cloudstack_api import FakeCloudStackClient
from app.services.terraform import PLAN_FILE, TFVARS_FILE, RealTerraform, TerraformError, summarize_plan_json

from .conftest import API_KEY, SECRET_KEY, make_settings, wait_for_status

STUB = f"""#!{sys.executable}
import json, os, pathlib, sys
args = sys.argv[1:]
with open("calls.jsonl", "a") as f:
    f.write(json.dumps(args) + "\\n")
cmd = args[0]
if pathlib.Path("FAIL_" + cmd).exists():
    print("Error: stub failure in " + cmd)
    sys.exit(1)
if cmd == "init":
    print("Initializing provider plugins...")
    print("provider configured with api_key=" + os.environ["CLOUDSTACK_API_KEY"])
    print("debug " + os.environ["CLOUDSTACK_SECRET_KEY"], file=sys.stderr)
elif cmd == "plan":
    out = [a for a in args if a.startswith("-out=")][0][5:]
    pathlib.Path(out).write_text("binary plan")
    print("Plan: 2 to add, 0 to change, 0 to destroy.")
elif cmd == "show":
    after = {{"name": "web-1", "root_disk_size": 20, "keypair": None, "tags": {{}}}}
    rc = lambda i: {{"address": f"module.vm.cloudstack_instance.this[{{i}}]", "type": "cloudstack_instance",
                    "name": "this", "change": {{"actions": ["create"], "after": after}}}}
    print(json.dumps({{"resource_changes": [rc(0), rc(1)]}}))
elif cmd == "apply":
    print("Apply complete! Resources: 2 added, 0 changed, 0 destroyed.")
"""


@pytest.fixture
def env(tmp_path: Path):
    tf_dir = tmp_path / "terraform"
    tf_dir.mkdir()
    (tf_dir / "main.tf").write_text("# root config\n")
    (tf_dir / "leftover.tfstate").write_text("{}")
    stub = tmp_path / "terraform-stub"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    settings = make_settings(tmp_path, dry_run=False, terraform_dir=tf_dir, terraform_bin=str(stub))
    workdir = settings.workdirs_dir / "job-1"
    workdir.mkdir(parents=True)
    return settings, workdir


def run_plan(settings, workdir, variables, state_id="job-1", destroy=False):
    lines: list[str] = []

    async def log(line: str) -> None:
        lines.append(line)

    runner = RealTerraform(settings)
    summary = asyncio.run(runner.plan("job-1", workdir, variables, log, state_id=state_id, destroy=destroy))
    return summary, lines


def calls(workdir: Path) -> list[list[str]]:
    return [json.loads(line) for line in (workdir / "calls.jsonl").read_text().splitlines()]


def test_plan_runs_init_validate_plan_show_and_saves_the_plan(env):
    settings, workdir = env
    summary, lines = run_plan(settings, workdir, {"name": "web", "instance_count": 2})
    assert [c[0] for c in calls(workdir)] == ["init", "validate", "plan", "show"]
    assert f"-out={PLAN_FILE}" in calls(workdir)[2]
    assert (workdir / PLAN_FILE).read_text() == "binary plan"
    assert summary.add == 2 and summary.resources[0].address == "module.vm.cloudstack_instance.this[0]"
    # Only the attributes worth reviewing, and no nulls.
    assert summary.resources[0].details == {"name": "web-1", "root_disk_size": "20"}
    assert (workdir / "main.tf").exists() and not (workdir / "leftover.tfstate").exists()
    assert "Plan: 2 to add, 0 to change, 0 to destroy." in lines


def test_state_is_kept_outside_the_workdir(env):
    settings, workdir = env
    run_plan(settings, workdir, {"name": "web"})
    backend_arg = next(a for a in calls(workdir)[0] if a.startswith("-backend-config="))
    assert backend_arg == f"-backend-config=path={settings.state_dir / 'job-1' / 'terraform.tfstate'}"
    assert workdir not in (settings.state_dir / "job-1").parents


def test_user_values_go_into_tfvars_and_never_onto_the_command_line(env):
    settings, workdir = env
    hostile = 'web"; rm -rf / #$(id)'
    run_plan(settings, workdir, {"name": hostile})
    assert json.loads((workdir / TFVARS_FILE).read_text()) == {"name": hostile}
    assert all(hostile not in arg and "rm -rf" not in arg for call in calls(workdir) for arg in call)
    assert f"-var-file={TFVARS_FILE}" in calls(workdir)[2]


def test_apply_uses_exactly_the_saved_plan_file(env):
    settings, workdir = env
    run_plan(settings, workdir, {"name": "web"})

    async def log(line: str) -> None:
        pass

    asyncio.run(RealTerraform(settings).apply("job-1", workdir, log, state_id="job-1"))
    apply_call = next(c for c in calls(workdir) if c[0] == "apply")
    assert apply_call[-1] == PLAN_FILE
    assert not any(a.startswith(("-var", "-auto-approve", "-destroy")) for a in apply_call)
    assert calls(workdir)[-1][0] == "output"


def test_destroy_plans_against_the_state_of_the_original_job(env):
    settings, workdir = env
    run_plan(settings, workdir, {"name": "web"}, state_id="original-job", destroy=True)
    init_call, plan_call = calls(workdir)[0], calls(workdir)[2]
    assert f"-backend-config=path={settings.state_dir / 'original-job' / 'terraform.tfstate'}" in init_call
    assert plan_call[:2] == ["plan", "-destroy"] and f"-out={PLAN_FILE}" in plan_call

    async def log(line: str) -> None:
        pass

    asyncio.run(RealTerraform(settings).apply("job-1", workdir, log, state_id="original-job", destroy=True))
    # Still just the saved plan; and no `output` after everything is gone.
    assert calls(workdir)[-1] == ["apply", "-input=false", "-no-color", PLAN_FILE]


@pytest.mark.parametrize("step,later", [("init", "validate"), ("validate", "plan"), ("plan", "show")])
def test_a_failing_step_stops_stage_one(env, step, later):
    settings, workdir = env
    (settings.terraform_dir / f"FAIL_{step}").touch()
    with pytest.raises(TerraformError, match=f"terraform {step} failed with exit code 1"):
        run_plan(settings, workdir, {"name": "web"})
    assert later not in [c[0] for c in calls(workdir)]
    assert not (workdir / PLAN_FILE).exists() or step != "plan"


def test_missing_terraform_binary_is_a_clean_error(env):
    settings, workdir = env
    settings.terraform_bin = "/nonexistent/terraform"
    with pytest.raises(TerraformError, match="could not start terraform"):
        run_plan(settings, workdir, {"name": "web"})


def test_secrets_in_terraform_output_are_masked_in_logs_and_stream(env, form):
    settings, _ = env
    with TestClient(create_app(settings, RealTerraform(settings), FakeCloudStackClient())) as c:
        job = c.post("/api/jobs", json=form).json()
        assert wait_for_status(c, job["id"], "awaiting_confirmation", "failed")["status"] == "awaiting_confirmation"
        c.post(f"/api/jobs/{job['id']}/confirm")
        assert wait_for_status(c, job["id"], "done", "failed")["status"] == "done"
        everything = c.get(f"/api/jobs/{job['id']}/logs").text + c.get(f"/api/jobs/{job['id']}/stream").text
    assert API_KEY not in everything and SECRET_KEY not in everything
    assert "api_key=***" in everything and "debug ***" in everything
    db_bytes = settings.db_path.read_bytes()
    assert API_KEY.encode() not in db_bytes and SECRET_KEY.encode() not in db_bytes


def test_plan_json_summary_counts_actions():
    def rc(name: str, actions: list[str]) -> dict:
        return {"address": f"x.{name}", "type": "x", "name": name, "change": {"actions": actions}}

    plan = {
        "resource_changes": [
            rc("a", ["create"]),
            rc("b", ["update"]),
            rc("c", ["delete"]),
            rc("d", ["delete", "create"]),
            rc("e", ["no-op"]),
        ]
    }
    summary = summarize_plan_json(plan)
    assert (summary.add, summary.change, summary.destroy) == (2, 1, 2)
    assert [r.action for r in summary.resources] == ["create", "update", "delete", "replace"]


def test_tfvars_come_from_the_validated_request(form):
    assert ProvisionRequest.model_validate(form).to_tfvars() == {
        "name": "web",
        "zone": form["zone"],
        "service_offering": form["service_offering"],
        "template": form["template"],
        "network_id": form["network"],
        "root_disk_size_gb": 20,
        "instance_count": 2,
        "keypair": None,
        "start_vm": True,
        "group": None,
        "tags": {},
        "user_data": None,
        "data_disk_offering": None,
        "data_disk_size_gb": None,
    }
