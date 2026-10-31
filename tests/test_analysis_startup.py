"""Slow archive recovery must not disable the manual operator panel."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from test_rider import domain, host_app

from venom_module_rider import analysis_startup
from venom_module_rider.analysis_manager import AnalysisManager
from venom_module_rider.api.routes import lifespan


async def test_panel_ready_while_real_analysis_recovery_is_pending(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = AnalysisManager.initialize

    async def slow_recovery(self):
        entered.set()
        await release.wait()
        return await original(self)

    monkeypatch.setattr(AnalysisManager, "initialize", slow_recovery)
    service, _pi = domain()
    await service.select(next(iter(service.devices)), service.session)
    app = host_app(service)
    context = lifespan(app)
    await asyncio.wait_for(context.__aenter__(), timeout=2)
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            assert (await client.get("/api/v1/rider/config")).status_code == 200
            state = await client.get("/api/v1/rider/status")
            assert state.status_code == 200
            assert state.json()["connection"]["status"] == "online"
            assert (await client.get("/api/v1/rider/analysis/runs")).status_code == 503
            release.set()
            async with asyncio.timeout(2):
                while app.state.rider_analysis is None:
                    await asyncio.sleep(0)
            ready = await client.get("/api/v1/rider/analysis/runs")
            assert ready.status_code == 200
            assert ready.json()["recovery_pending"] is False
    finally:
        release.set()
        await context.__aexit__(None, None, None)
    assert app.state.rider_analysis is None
    assert app.state.rider_samples.closed


def host():
    return SimpleNamespace(
        state=SimpleNamespace(
            rider_analysis=None,
            rider_analysis_error=None,
            rider_vision=SimpleNamespace(busy=None, depth_busy=None, priority=None),
        )
    )


@pytest.mark.parametrize("cancel_shutdown", [False, True])
async def test_shutdown_drains_recovery_and_closes_manager_once(
    monkeypatch, cancel_shutdown
):
    entered, release = asyncio.Event(), asyncio.Event()
    closed = []

    class Manager:
        def __init__(self, *_args, **_kwargs):
            pass

        async def initialize(self):
            entered.set()
            await release.wait()

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(analysis_startup, "AnalysisManager", Manager)
    app = host()
    startup = analysis_startup.AnalysisStartup(app, object())
    await asyncio.wait_for(entered.wait(), 2)
    shutdown = asyncio.create_task(startup.close())
    await asyncio.sleep(0)
    assert not shutdown.done()
    assert not closed
    if cancel_shutdown:
        shutdown.cancel()
        with pytest.raises(asyncio.CancelledError):
            await shutdown
        assert not startup.task.cancelled()
    release.set()
    await asyncio.wait_for(startup.task, 2)
    if not cancel_shutdown:
        await shutdown
    assert closed == [True]
    assert app.state.rider_analysis is None


@pytest.mark.parametrize("failure", ["constructor", "recovery"])
async def test_failed_archive_initialization_is_observed_and_closed(
    monkeypatch, failure, caplog
):
    closed = []

    class Manager:
        def __init__(self, *_args, **_kwargs):
            if failure == "constructor":
                raise OSError("unavailable archive")

        async def initialize(self):
            raise ValueError("invalid archive")

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(analysis_startup, "AnalysisManager", Manager)
    app = host()
    startup = analysis_startup.AnalysisStartup(app, object())
    await asyncio.wait_for(startup.task, 2)
    await startup.close()
    assert app.state.rider_analysis is None
    assert app.state.rider_analysis_error == "analysis_unavailable"
    assert len(closed) == (failure == "recovery")
    assert "Stored Rider analysis recovery failed" in caplog.text
