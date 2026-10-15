"""The panel sends one unchanged operator intent to the selected Pi."""

import asyncio
import json
import time

import httpx
import pytest
from fastapi import FastAPI

from venom_module_rider.api.routes import router
from venom_module_rider.client import PiClient, expected_contract, json_timeout
from venom_module_rider.config import Device
from venom_module_rider.demo import DemoPi, create_app, envelope
from venom_module_rider.demo_navigation import ReplayNavigation
from venom_module_rider.service import RiderService


def body(**changes):
    return {
        "request_id": "click-1",
        "generation": "generation",
        "ts": time.time(),
        "ttl_ms": 500,
        "config_revision": 4,
        "settings_revision": 3,
        "operator_consent": True,
        "operator_plan": "Forward for up to 10 seconds at current low speed.",
        "source": "manual",
        "execution_context": "panel",
        **changes,
    }


async def setup_proxy(monkeypatch, handler):
    monkeypatch.setattr(
        "venom_module_rider.api.routes.ensure_module_mutation_allowed", lambda **_: None
    )
    device = Device("pi", "Fixture", "http://127.0.0.1:18880", "prod")
    service = RiderService(
        (device,),
        client_factory=lambda selected: PiClient(
            selected, transport=httpx.MockTransport(handler)
        ),
    )
    await service.select("pi", service.session)
    app = FastAPI()
    app.state.rider = service
    app.include_router(router)
    return service, httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://panel"
    )


async def test_single_post_preserves_original_intent_and_pi_response(monkeypatch):
    calls = []
    expected = {
        "ok": True,
        "config": {"revision": 5, "mode": "drive"},
        "status": {"run_id": "confirmed-run", "running": True},
        "authority": {"generation": "generation", "owner": "navigation"},
    }

    def receive(request):
        calls.append(request)
        response = envelope(expected, "rider.navigation_loop")
        return httpx.Response(200, content=response.body, headers=response.headers)

    service, client = await setup_proxy(monkeypatch, receive)
    original = body()
    try:
        response = await client.post(
            "/api/v1/rider/navigation/trial",
            json=original,
            headers={"X-Rider-Session": service.session},
        )
        assert response.status_code == 200
        assert response.json()["data"] == expected
        assert len(calls) == 1 and calls[0].url.path == "/api/navigation/trial"
        assert json.loads(calls[0].content) == original
        assert calls[0].extensions["timeout"]["read"] == 6.0
    finally:
        await client.aclose()
        await service.close()


async def test_stale_browser_session_never_reaches_pi(monkeypatch):
    calls = []
    service, client = await setup_proxy(
        monkeypatch, lambda request: calls.append(request)
    )
    try:
        response = await client.post(
            "/api/v1/rider/navigation/trial",
            json=body(),
            headers={"X-Rider-Session": "previous-session"},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "stale_device_session"
        assert calls == []
    finally:
        await client.aclose()
        await service.close()


@pytest.mark.parametrize(
    "change",
    [
        {"operator_consent": False},
        {"operator_consent": 1},
        {"ts": "1"},
        {"ttl_ms": True},
        {"ttl_ms": 501},
        {"settings_revision": True},
        {"operator_plan": " "},
        {"source": "navigation"},
        {"request_id": ""},
        {"extra": 1},
    ],
)
async def test_invalid_panel_intent_is_rejected_before_proxy(monkeypatch, change):
    calls = []
    service, client = await setup_proxy(
        monkeypatch, lambda request: calls.append(request)
    )
    try:
        response = await client.post(
            "/api/v1/rider/navigation/trial",
            json=body(**change),
            headers={"X-Rider-Session": service.session},
        )
        assert response.status_code == 422
        assert not calls
    finally:
        await client.aclose()
        await service.close()


async def test_pi_cancellation_reason_is_returned_without_retry_or_rearm(monkeypatch):
    calls = []

    def receive(request):
        calls.append(request)
        response = envelope(
            {"error": "navigation_trial_cancelled"}, "rider.navigation_loop", status=409
        )
        return httpx.Response(409, content=response.body, headers=response.headers)

    service, client = await setup_proxy(monkeypatch, receive)
    try:
        response = await client.post(
            "/api/v1/rider/navigation/trial",
            json=body(),
            headers={"X-Rider-Session": service.session},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "navigation_trial_cancelled"
        assert len(calls) == 1
    finally:
        await client.aclose()
        await service.close()


def test_trial_uses_existing_navigation_contract_and_bounded_timeout():
    assert expected_contract("/api/navigation/trial") == ("rider.navigation_loop", 1)
    assert json_timeout("/api/navigation/trial", "POST", False).read == 6.0
    assert json_timeout("/api/safety", "POST", True).read == 1.5


class Permit:
    def __init__(self, config, run, drive, stop, clock, *, pause=None):
        self.latched, self.reason, self.stop = False, "waiting", stop

    def halt(self, reason):
        self.latched, self.reason = True, reason
        self.stop()

    def tick(self):
        return not self.latched

    def accept(self, *args):
        return False


async def test_demo_pi_supports_same_one_start_and_rejects_replayed_identifier():
    nav = ReplayNavigation(b"recorded-fixture", Permit, model_sha="a" * 64)
    pi = DemoPi(navigation=nav)
    nav.policy.update(verified=True, calibration_id="demo-only", model_sha256="a" * 64)
    nav.request = lambda *_: asyncio.sleep(
        0, result={"state": "unknown", "action": "stop"}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(pi)), base_url="http://fixture"
    ) as client:
        request = body(
            generation=pi.generation,
            config_revision=nav.policy["revision"],
            settings_revision=pi.controls["revision"],
        )
        response = await client.post("/api/navigation/trial", json=request)
        assert response.status_code == 200
        result = response.json()["data"]
        assert result["status"]["running"] and result["status"]["run_id"]
        assert result["config"]["mode"] == "drive"
        assert result["authority"]["owner"] == "navigation"
        assert pi.camera_on
        replay = body(
            generation=pi.generation,
            config_revision=nav.policy["revision"],
            settings_revision=pi.controls["revision"],
        )
        rejected = await client.post("/api/navigation/trial", json=replay)
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "navigation_trial_replay"
    await nav.close()


async def test_demo_scoped_stop_preserves_new_manual_generation():
    pi = DemoPi()
    before = pi.generation
    pi.control({"action": "stop"})
    pi.state, pi.owner = "ARMED", "manual"
    response = pi.control({"action": "stop", "expected_generation": before})
    assert response.status_code == 409
    assert pi.state == "ARMED" and pi.owner == "manual"


async def test_failed_demo_trial_revokes_its_own_arm():
    nav = ReplayNavigation(b"fixture", Permit, model_sha="a" * 64)
    pi = DemoPi(navigation=nav)
    nav.policy.update(verified=True, calibration_id="demo-only", model_sha256="b" * 64)
    try:
        with pytest.raises(ValueError, match="navigation_calibration_required"):
            await nav.start_trial(
                body(
                    generation=pi.generation,
                    config_revision=nav.policy["revision"],
                    settings_revision=pi.controls["revision"],
                )
            )
        assert pi.state == "DISARMED" and not pi.safety()["moving"]
    finally:
        await nav.close()
