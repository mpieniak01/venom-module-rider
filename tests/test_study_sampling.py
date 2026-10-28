"""Fixture-only regression for explicit recording cadence and terminal boundaries."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from test_rider import domain, host_app

from venom_module_rider.errors import RiderError
from venom_module_rider.samples_registry import SamplesRegistry
from venom_module_rider.study_sampling import StudySampler, collecting


async def test_start_records_without_status_poll_and_end_stops_reading(tmp_path):
    service, pi = domain()
    await service.select("demo", service.session)
    registry = SamplesRegistry(tmp_path)
    service.samples = registry
    app = host_app(service)
    app.state.rider_samples = registry
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            response = await client.post(
                "/api/v1/rider/samples/sessions",
                json={
                    "request_id": "one",
                    "source_device_id": "demo",
                    "policy": {"kind": "manual", "scene_group": "motion-study-v1:wood"},
                },
            )
            assert response.status_code == 201, response.text
            sid = response.json()["session_id"]
            task = service.study_sampler.task
            service.study_sampler.ensure()
            assert service.study_sampler.task is task
            async with asyncio.timeout(2):
                while service.study_sampler.reads < 3:
                    await asyncio.sleep(0.01)
            reads = [x for x in pi.trace if x["path"] == "/state"]
            assert 3 <= len(reads) <= 4
            gaps = service.read_metrics.snapshot()["telemetry"]["start_gaps_ms"]
            assert gaps["p50"] >= 90
            assert all(x["method"] == "GET" for x in pi.trace)
            assert service.study_sampler.snapshot()["reads"] >= 3
            assert (
                await client.post(f"/api/v1/rider/samples/sessions/{sid}/end", json={})
            ).status_code == 200
            await asyncio.wait_for(task, 1)
            before = len(pi.trace)
            await asyncio.sleep(0.12)
            assert len(pi.trace) == before
            assert not service.study_sampler.snapshot()["active"]
    finally:
        await service.close()
        await registry.close()


async def test_ordinary_collection_never_starts_sampler(tmp_path):
    service, pi = domain()
    await service.select("demo", service.session)
    registry = SamplesRegistry(tmp_path)
    service.samples = registry
    try:
        await registry.start("ordinary", "demo")
        service.study_sampler.ensure()
        assert service.study_sampler.task is None
        assert not collecting(service)
        assert not any(x["path"] == "/state" for x in pi.trace)
    finally:
        await service.close()
        await registry.close()


@pytest.mark.parametrize("terminal", ["stop", "device", "closed", "quota"])
async def test_terminal_boundaries_stop_sampling(tmp_path, terminal):
    service, _ = domain()
    await service.select("demo", service.session)
    registry = SamplesRegistry(tmp_path)
    service.samples = registry
    try:
        row = await registry.start("study", "demo", {"scene_group": "motion-study-v1:"})
        service.study_sampler.ensure()
        task = service.study_sampler.task
        await asyncio.sleep(0.02)
        if terminal == "stop":
            service.stop_sample_events("auto_global_stop")
        elif terminal == "device":
            service.session = "changed"
        elif terminal == "closed":
            await registry.close()
        else:
            registry.evidence.retire("evidence_limit", row["session_id"])
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)
        assert task.done()
        assert not service.study_sampler.snapshot()["active"]
    finally:
        await service.close()
        if not registry.closed:
            await registry.close()


async def test_slow_reads_do_not_overlap_and_failures_back_off():
    calls = []

    class Domain:
        session = "same"

        async def read(self, path, ttl):
            calls.append(asyncio.get_running_loop().time())
            if len(calls) == 1:
                await asyncio.sleep(0.16)
                raise RiderError("pi_unreachable")
            raise RuntimeError("fixture recorder failure")

    domain = Domain()
    sampler = StudySampler(domain)
    from unittest.mock import patch

    with patch("venom_module_rider.study_sampling.collecting", return_value=True):
        sampler.ensure()
        await asyncio.sleep(1.2)
        await sampler.close()
    assert len(calls) == 2
    assert calls[1] - calls[0] >= 0.99
    assert sampler.errors == 2
    assert sampler.last_error == "study_sampling_failed"
    assert not collecting(SimpleNamespace(samples=None))


async def test_priority_read_invalidation_recovers_at_sampling_cadence():
    from unittest.mock import patch

    read_again = asyncio.Event()

    class Domain:
        session = "same-device"
        count = 0

        async def read(self, path, ttl):
            self.count += 1
            if self.count == 1:
                raise RiderError("stale_device_session")
            read_again.set()
            return {}

    sampler = StudySampler(Domain())
    with patch("venom_module_rider.study_sampling.collecting", return_value=True):
        sampler.ensure()
        await asyncio.wait_for(read_again.wait(), 0.5)
        await sampler.close()
    assert sampler.errors == 1
    assert sampler.reads >= 1


async def test_cancelled_shared_transport_recovers_without_view_poll(
    tmp_path, monkeypatch
):
    service, pi = domain()
    await service.select("demo", service.session)
    registry = SamplesRegistry(tmp_path)
    service.samples = registry
    entered = asyncio.Event()
    original = service.client.json
    state_reads = 0

    async def delayed_first_state(path, **kwargs):
        nonlocal state_reads
        if path == "/state":
            state_reads += 1
            if state_reads == 1:
                entered.set()
                await asyncio.Future()
        return await original(path, **kwargs)

    monkeypatch.setattr(service.client, "json", delayed_first_state)
    try:
        await registry.start("study", "demo", {"scene_group": "motion-study-v1:"})
        service.study_sampler.ensure()
        task = service.study_sampler.task
        await asyncio.wait_for(entered.wait(), 1)
        service.inflight["/state"].cancel()
        async with asyncio.timeout(1):
            while service.study_sampler.reads == 0:
                await asyncio.sleep(0.01)
        assert service.study_sampler.task is task
        assert not task.done()
        assert service.study_sampler.errors == 1
        assert service.study_sampler.last_error is None
        assert all(row["method"] == "GET" for row in pi.trace)
        await service.study_sampler.close()
        assert task.cancelled()
    finally:
        await service.close()
        await registry.close()
