"""Host timing, persistence and measurement contracts use no robot hardware."""

import asyncio
import threading
from unittest.mock import patch

import httpx
import pytest
from test_rider import domain, host_app

from venom_module_rider.errors import RiderError
from venom_module_rider.loop_timing import (
    DEFAULTS,
    LoopTiming,
    ReadMetrics,
    distribution,
    validate,
)


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {**DEFAULTS, "control_ms": 501},
        {**DEFAULTS, "control_ms": True},
        {**DEFAULTS, "preview_ms": 499},
        {**DEFAULTS, "telemetry_ms": 5001},
        {**DEFAULTS, "extra": 1000},
        {**DEFAULTS, "overview_ms": 1000.0},
        {**DEFAULTS, "idle_ms": float("nan")},
    ],
)
def test_invalid_profiles(bad):
    with pytest.raises(RiderError, match="loop_timing_invalid"):
        validate(bad)


async def test_persistent_roundtrip_conflict_and_no_aliasing(tmp_path):
    path = tmp_path / "state" / "timing.json"
    timing = LoopTiming(path)
    values = {**DEFAULTS, "control_ms": 800, "telemetry_ms": 500}
    result = await timing.update(0, values)
    values["control_ms"] = 999
    result["effective"]["control_ms"] = 998
    restored = LoopTiming(path)
    assert restored.revision == timing.revision == 1
    assert restored.values["control_ms"] == timing.values["control_ms"] == 800
    with pytest.raises(RiderError, match="loop_timing_conflict"):
        await timing.update(0, DEFAULTS)
    with pytest.raises(RiderError, match="loop_timing_conflict"):
        await timing.update(True, DEFAULTS)
    assert len(list(path.parent.iterdir())) == 1


@pytest.mark.parametrize(
    "content",
    [
        "{",
        "[]",
        '{"version":true,"revision":0,"values":{}}',
        " " * 4097,
        '{"version":1,"revision":-1,"values":{}}',
        '{"version":1,"revision":0,"values":{}}',
    ],
)
def test_restore_invalid_file_does_not_overwrite_it(tmp_path, content):
    path = tmp_path / "timing.json"
    path.write_text(content)
    timing = LoopTiming(path)
    assert timing.snapshot()["warning"] == "loop_timing_restore_failed"
    assert timing.values == DEFAULTS
    assert path.read_text() == content


async def test_failed_persistence_does_not_publish_effective_revision(tmp_path):
    timing = LoopTiming(tmp_path / "timing.json")
    with patch("venom_module_rider.loop_timing.persist", side_effect=OSError("disk")):
        with pytest.raises(RiderError, match="loop_timing_persistence_failed"):
            await timing.update(0, {**DEFAULTS, "control_ms": 1000})
    assert timing.revision == 0
    assert timing.values == DEFAULTS


async def test_concurrent_updates_have_one_winner():
    timing = LoopTiming()
    results = await asyncio.gather(
        timing.update(0, {**DEFAULTS, "control_ms": 600}),
        timing.update(0, {**DEFAULTS, "control_ms": 700}),
        return_exceptions=True,
    )
    assert sum(isinstance(row, RiderError) for row in results) == 1
    assert timing.revision == 1


async def test_cancelled_save_finishes_atomic_write_before_next_revision(tmp_path):
    from venom_module_rider.loop_timing import persist

    timing = LoopTiming(tmp_path / "timing.json")
    entered, release = threading.Event(), threading.Event()

    def slow_write(path, state):
        entered.set()
        assert release.wait(5)
        persist(path, state)

    with patch("venom_module_rider.loop_timing.persist", side_effect=slow_write):
        task = asyncio.create_task(timing.update(0, {**DEFAULTS, "control_ms": 600}))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert timing.revision == LoopTiming(timing.path).revision == 1
    assert timing.values["control_ms"] == 600


async def test_revision_overflow_is_rejected():
    timing = LoopTiming()
    timing.revision = 2**53 - 1
    with pytest.raises(RiderError, match="loop_timing_conflict"):
        await timing.update(timing.revision, DEFAULTS)


async def test_metrics_count_actual_requests_and_bound_observations():
    now = [1.0]
    metrics = ReadMetrics(clock=lambda: now[0])

    async def operation():
        now[0] += 0.025
        return {"ok": True}

    for _ in range(80):
        assert await metrics.measure("telemetry", operation) == {"ok": True}
        now[0] += 0.075
    metrics.count("telemetry", "cache_hits")
    metrics.count("telemetry", "coalesced")
    row = metrics.snapshot()["telemetry"]
    assert row["requests"] == 80
    assert row["cache_hits"] == row["coalesced"] == 1
    assert row["duration_ms"]["count"] == row["start_gaps_ms"]["count"] == 64
    assert row["duration_ms"]["p95"] == pytest.approx(25)
    assert row["start_gaps_ms"]["p50"] == pytest.approx(100)
    assert distribution([])["p50"] is None

    async def failure():
        raise RuntimeError("transport")

    with pytest.raises(RuntimeError):
        await metrics.measure("telemetry", failure)
    assert metrics.snapshot()["telemetry"]["errors"] == 1


async def test_api_session_validation_conflict_and_no_pi_mutation():
    service, pi = domain()
    await service.select("demo", service.session)
    trace = len(pi.trace)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=host_app(service)), base_url="http://test"
    )
    try:
        initial = (await client.get("/api/v1/rider/loop-timing")).json()
        assert initial["scope"] == "venom-host"
        assert initial["persistent"] is False
        body = {"revision": 0, "values": {**DEFAULTS, "calibration_ms": 500}}
        assert (
            await client.put("/api/v1/rider/loop-timing", json=body)
        ).status_code == 409
        headers = {"X-Rider-Session": service.session}
        response = await client.put(
            "/api/v1/rider/loop-timing", json=body, headers=headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["effective"]["calibration_ms"] == 500
        assert (
            await client.put("/api/v1/rider/loop-timing", json=body, headers=headers)
        ).status_code == 409
        assert (
            await client.put(
                "/api/v1/rider/loop-timing", json={"revision": 1}, headers=headers
            )
        ).status_code == 422
        assert (
            await client.put(
                "/api/v1/rider/loop-timing",
                content="{",
                headers={**headers, "Content-Type": "application/json"},
            )
        ).status_code == 400
        assert len(pi.trace) == trace
    finally:
        await client.aclose()
        await service.close()


async def test_status_uses_profile_cache_without_adding_calibration_telemetry():
    import time

    service, pi = domain()
    await service.select("demo", service.session)
    try:
        await service.timing.update(
            0,
            {**DEFAULTS, "calibration_ms": 500, "idle_ms": 10000, "telemetry_ms": 500},
        )
        status = await service.status("calibration")
        assert status["polling"]["calibration_ms"] == 500
        assert status["polling"]["face_ms"] == 10000
        assert status["telemetry"] is None
        assert not any(item["path"] == "/state" for item in pi.trace)
        await service.read("/state", service.timing.values["telemetry_ms"] / 1000)
        await service.status("overview")
        assert service.read_metrics.snapshot()["telemetry"]["cache_hits"] >= 1
        service.cache["/state"] = (time.monotonic() - 1, service.cache["/state"][1])
        count = service.read_metrics.snapshot()["telemetry"]["requests"]
        await service.status("overview")
        assert service.read_metrics.snapshot()["telemetry"]["requests"] == count + 1
        assert all(item["method"] == "GET" for item in pi.trace)
    finally:
        await service.close()


async def test_coalesced_reads_have_one_transport_measurement():
    service, _ = domain()
    await service.select("demo", service.session)
    try:
        await asyncio.gather(service.read("/state", 2), service.read("/state", 2))
        row = service.read_metrics.snapshot()["telemetry"]
        assert row["requests"] == row["coalesced"] == 1
    finally:
        await service.close()
