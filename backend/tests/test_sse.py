from __future__ import annotations

import asyncio
import json

from app.jobs.broker import Broker
from app.routes.jobs import job_events, sse

from .conftest import wait_for_status


def parse(text: str) -> list[dict]:
    events = []
    for block in text.strip().split("\n\n"):
        event: dict = {}
        for line in block.split("\n"):
            if line.startswith(":"):
                continue
            key, _, value = line.partition(": ")
            event[key] = json.loads(value) if key == "data" else value
        if event:
            events.append(event)
    return events


def finished_job(client, planned):
    job = planned()
    client.post(f"/api/jobs/{job['id']}/confirm")
    return wait_for_status(client, job["id"], "done")


def test_sse_wire_format():
    assert sse("log", {"line": "hi"}, 7) == 'id: 7\nevent: log\ndata: {"line": "hi"}\n\n'
    assert sse("end", {}) == "event: end\ndata: {}\n\n"


def test_stream_replays_every_line_in_order_then_ends(client, planned):
    job = finished_job(client, planned)
    resp = client.get(f"/api/jobs/{job['id']}/stream")
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse(resp.text)
    logs = [e for e in events if e["event"] == "log"]
    assert [int(e["id"]) for e in logs] == list(range(1, len(logs) + 1))
    assert [e["data"]["line"] for e in logs] == [
        line["line"] for line in client.get(f"/api/jobs/{job['id']}/logs").json()
    ]
    assert any("Apply complete!" in e["data"]["line"] for e in logs)
    assert events[-2] == {"event": "status", "data": {"status": "done", "error": None}}
    assert events[-1] == {"event": "end", "data": {"status": "done"}}


def test_reconnect_with_last_event_id_sends_only_newer_lines(client, planned):
    job = finished_job(client, planned)
    total = len(client.get(f"/api/jobs/{job['id']}/logs").json())
    events = parse(client.get(f"/api/jobs/{job['id']}/stream", headers={"Last-Event-ID": str(total - 3)}).text)
    assert [int(e["id"]) for e in events if e["event"] == "log"] == [total - 2, total - 1, total]
    assert events[-1]["event"] == "end"


def test_stream_of_a_failed_job_carries_the_error(client, planned):
    job = planned(name="failplan-web")
    events = parse(client.get(f"/api/jobs/{job['id']}/stream").text)
    status = [e for e in events if e["event"] == "status"][-1]
    assert status["data"]["status"] == "failed" and "terraform plan failed" in status["data"]["error"]
    assert events[-1] == {"event": "end", "data": {"status": "failed"}}


def test_stream_follows_a_job_live_across_the_confirmation(client, planned):
    """Connect while the job waits, confirm from the outside, and see the stream finish."""
    job = planned()
    manager = client.app.state.manager

    async def scenario() -> list[dict]:
        seen: list[str] = []
        gen = job_events(manager, job["id"], 0)
        async for chunk in gen:
            seen.append(chunk)
            if '"status": "awaiting_confirmation"' in chunk:
                break
        await manager.confirm(job["id"])
        async for chunk in gen:
            seen.append(chunk)
        return parse("".join(seen))

    events = client.portal.call(scenario)
    statuses = [e["data"]["status"] for e in events if e["event"] == "status"]
    assert statuses[0] == "awaiting_confirmation" and statuses[-1] == "done"
    assert events[-1]["event"] == "end"


def test_broker_wakes_waiters_and_times_out():
    async def scenario() -> tuple[bool, bool]:
        broker = Broker()
        seen = broker.version("job")
        waiter = asyncio.create_task(broker.wait("job", seen, timeout=2))
        await asyncio.sleep(0)
        await broker.publish("job")
        return await waiter, await broker.wait("job", broker.version("job"), timeout=0.01)

    assert asyncio.run(scenario()) == (True, False)
