"""Real stream handoff uses fresh passive results without granting movement."""

import asyncio
import json

import pytest
from test_stream_browser import Browser, setup, unpack

from venom_module_rider.api.pipeline_routes import browser_stream
from venom_module_rider.api.vision_preview import diagnostic_preview_allowed


@pytest.mark.parametrize(
    "phase", ["stop_old_producer", "configure", "start_navigation", "first_status"]
)
async def test_preparing_journey_keeps_passive_preview_in_same_camera_session(phase):
    pi, domain, hub, loop, state = await setup()
    original = pi.json

    async def read(path, **kwargs):
        if path == "/api/navigation/status":
            return {
                "data": {
                    "running": False,
                    "run_id": "previous",
                    "preparation": {
                        "state": "preparing",
                        "fresh": True,
                        "journey_version": 1,
                        "phase": phase,
                    },
                }
            }
        if path == "/api/logic/state":
            return {
                "data": {
                    "features": {
                        "s14_venom_navigation": {"requested": True},
                        "s15_venom_perception": {"requested": False},
                    }
                }
            }
        if path == "/api/control/state":
            return {
                "data": {
                    "safety": {
                        "state": "DISARMED",
                        "can_move": False,
                        "moving": False,
                        "stale": False,
                    }
                }
            }
        return await original(path, **kwargs)

    pi.json = read
    iterator = (await browser_stream("venom", Browser(state))).body_iterator
    task = asyncio.create_task(anext(iterator))
    try:
        headers, _ = unpack(await asyncio.wait_for(task, 1))
        first = json.loads(headers["x-rider-analysis-json"])
        await pi.emit(2)
        headers, _ = unpack(await asyncio.wait_for(anext(iterator), 1))
        second = json.loads(headers["x-rider-analysis-json"])
        assert first["source"]["camera_session"] == second["source"]["camera_session"]
        assert second["source"]["sequence"] > first["source"]["sequence"]
        assert first["preview_only"] is second["preview_only"] is True
        assert "journey" not in second
        assert headers["x-rider-vision-action"] == "stop"
        assert pi.opened == 1 and pi.snapshot_requests == 0
        assert not any(method != "GET" for method, _path, _body in pi.calls)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await iterator.aclose()
        await hub.close()
        await loop.close()
        await domain.close()


@pytest.mark.parametrize(
    "safety,expected",
    [
        (
            {"state": "DISARMED", "can_move": False, "moving": False, "stale": False},
            True,
        ),
        (
            {"state": "DISARMED", "can_move": False, "moving": False, "stale": True},
            False,
        ),
        (
            {
                "state": "ARMED",
                "owner": "navigation",
                "can_move": True,
                "stale": False,
                "arming_mode": "journey",
                "operator_control": {"state": "qualifying"},
            },
            True,
        ),
        (
            {
                "state": "ARMED",
                "owner": "navigation",
                "can_move": True,
                "stale": False,
                "arming_mode": "journey",
                "operator_control": {"state": "active"},
            },
            False,
        ),
    ],
)
async def test_preparation_preview_is_not_permission_for_active_navigation(
    safety, expected
):
    class Domain:
        async def read(self, _path, _ttl):
            return {"data": {"safety": safety}}

    status = {
        "running": False,
        "preparation": {"state": "preparing", "fresh": True, "journey_version": 1},
    }
    assert (
        await diagnostic_preview_allowed(
            Domain(), status, {"observe": False, "drive": True}
        )
        is expected
    )
