"""Shutdown follows real Pi ownership rules, including an orphan capture."""

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from venom_module_rider.api.routes import router
from venom_module_rider.client import PiClient, expected_contract, json_timeout
from venom_module_rider.config import Device
from venom_module_rider.demo import DemoPi, create_app
from venom_module_rider.errors import RiderError
from venom_module_rider.service import RiderService


@pytest.mark.parametrize("external", [False, True])
async def test_camera_shutdown_retires_goal_and_confirms_hardware_stop(external):
    domain = RiderService(())
    events = []

    async def goal(name, body, session):
        events.append((name, body))
        if external and len(events) == 1:
            raise RiderError("external_services_running", 503)
        return {"ok": True}

    async def mutate(path, body, session):
        events.append((path, body))
        return {"ok": True}

    domain.goal = AsyncMock(side_effect=goal)
    domain.mutate = AsyncMock(side_effect=mutate)
    result = await domain.stop_camera({"action": "stop"}, "session")
    assert events[0] == ("s2_camera_preview", {"enabled": False})
    assert events[1][0] == "/api/resource/camera"
    assert events[1][1]["units"] == [
        "camera-capture@raw.service",
        "camera-capture@edge.service",
        "camera-capture@ssd.service",
    ]
    assert events[2] == ("/svc/frame-distributor.service", {"action": "stop"})
    assert len(events) == (4 if external else 3)
    assert result == {"camera": {"ok": True}, "feed": {"ok": True}}


async def test_unknown_goal_failure_never_attempts_hardware_shutdown():
    domain = RiderService(())
    domain.goal = AsyncMock(side_effect=RiderError("pi_unreachable", 503))
    domain.mutate = AsyncMock()
    with pytest.raises(RiderError, match="pi_unreachable"):
        await domain.stop_camera({"action": "stop"}, "session")
    domain.mutate.assert_not_awaited()


@pytest.mark.parametrize("code", ["resource_in_use", "ownership_unknown"])
async def test_another_owner_or_unknown_ownership_is_not_forcibly_released(code):
    domain = RiderService(())
    domain.goal = AsyncMock(return_value={})
    domain.mutate = AsyncMock(side_effect=RiderError(code, 409))
    with pytest.raises(RiderError, match=code):
        await domain.stop_camera({"action": "stop"}, "session")
    assert domain.mutate.await_count == 1


async def test_feed_stop_failure_is_not_returned_as_success():
    domain = RiderService(())
    domain.goal = AsyncMock(return_value={})
    domain.mutate = AsyncMock(
        side_effect=[{"ok": True}, RiderError("stop_failed", 503)]
    )
    with pytest.raises(RiderError, match="stop_failed"):
        await domain.stop_camera({"action": "stop"}, "session")


def test_frame_feed_stop_negotiates_only_the_existing_catalogue_path():
    assert expected_contract("/svc/frame-distributor.service") == ("rider.services", 2)
    assert json_timeout("/svc/frame-distributor.service", "POST", False).read == 20
    with pytest.raises(RiderError, match="unsupported_pi_path"):
        expected_contract("/svc/unrelated.service")


async def test_camera_stop_http_contract_is_repeatable_in_demo(monkeypatch):
    monkeypatch.setattr(
        "venom_module_rider.api.routes.ensure_module_mutation_allowed", lambda **_: None
    )
    pi = DemoPi()
    device = Device("demo", "Demo", "http://127.0.0.1", "demo")
    domain = RiderService(
        (device,),
        client_factory=lambda selected: PiClient(
            selected, transport=httpx.ASGITransport(app=create_app(pi))
        ),
    )
    await domain.select("demo", domain.session)
    app = FastAPI()
    app.state.rider = domain
    app.include_router(router)
    try:
        await domain.goal("s2_camera_preview", {"enabled": True}, domain.session)
        assert pi.camera_on
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://panel"
        ) as client:
            for _ in range(2):
                response = await client.post(
                    "/api/v1/rider/resource/camera",
                    json={"action": "stop"},
                    headers={"X-Rider-Session": domain.session},
                )
                assert response.status_code == 200, response.text
                assert pi.camera_on is False
    finally:
        await domain.close()
