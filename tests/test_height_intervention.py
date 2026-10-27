"""Height consent uses the shared domain proxy; all requests are isolated fixtures."""

import asyncio
import json

import pytest
from pydantic import ValidationError
from test_navigation_trial_api import setup_proxy
from test_operator_proxy import intent, packet

from venom_module_rider.api.schemas import Command
from venom_module_rider.errors import RiderError


def height(**changes):
    return intent(
        "height",
        height=95,
        intervene=True,
        device_settings=True,
        operator_intervention_version=1,
        operator_consent=True,
        operator_plan="Set height 95 SDK",
        owner="manual",
        arming_mode="operator",
        **changes,
    )


def test_explicit_height_is_a_typed_single_control_request():
    original = height()
    assert Command.model_validate(original).model_dump(exclude_none=True) == original


@pytest.mark.parametrize(
    "change",
    [
        {"mode": "release"},
        {"mode": "pulse"},
        {"direction": "forward"},
        {"enabled": True},
        {"height": True},
        {"height": 95.0},
        {"operator_consent": False},
        {"operator_intervention_version": True},
    ],
)
def test_height_cannot_smuggle_motion_or_implicit_consent(change):
    with pytest.raises(ValidationError):
        Command.model_validate({**height(), **change})


async def test_height_proxy_keeps_one_original_intent_without_synthesized_arm(
    monkeypatch,
):
    received = []

    def handle(request):
        received.append((request.url.path, json.loads(request.content)))
        return packet()

    service, client = await setup_proxy(monkeypatch, handle)
    try:
        original = height()
        response = await client.post(
            "/api/v1/rider/control",
            json=original,
            headers={"X-Rider-Session": service.session},
        )
        assert response.status_code == 200
        assert received == [("/api/control", original)]
    finally:
        await client.aclose()
        await service.close()


async def test_height_retires_pending_navigation_without_waiting(monkeypatch):
    started = asyncio.Event()
    received = []

    async def handle(request):
        body = json.loads(request.content)
        received.append(body)
        if body["action"] == "navigation_resume":
            started.set()
            await asyncio.Event().wait()
        return packet()

    service, client = await setup_proxy(monkeypatch, handle)
    try:
        pending = asyncio.create_task(
            service.control(intent("navigation_resume"), service.session)
        )
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(service.control(height(), service.session), 1)
        with pytest.raises(RiderError, match="command_superseded"):
            await pending
        assert received[-1] == height()
    finally:
        await client.aclose()
        await service.close()


async def test_stop_retires_a_pending_height_request(monkeypatch):
    started = asyncio.Event()

    async def handle(request):
        if json.loads(request.content)["action"] == "height":
            started.set()
            await asyncio.Event().wait()
        return packet()

    service, client = await setup_proxy(monkeypatch, handle)
    try:
        pending = asyncio.create_task(service.control(height(), service.session))
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(service.control({"action": "stop"}, "old-session"), 1)
        with pytest.raises(RiderError, match="command_superseded"):
            await pending
    finally:
        await client.aclose()
        await service.close()
