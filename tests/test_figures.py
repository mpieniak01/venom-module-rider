"""Figure API and evidence tests use inert Pi responses only."""

import asyncio
import copy
import json

import httpx
import pytest
from pydantic import ValidationError
from test_navigation_trial_api import setup_proxy
from test_pulse_trials import body

from venom_module_rider.api.figure_routes import FigureRequest, identifier
from venom_module_rider.client import expected_contract
from venom_module_rider.demo import envelope
from venom_module_rider.errors import RiderError


def request(**changes):
    row = body()
    for key in ("trial_profile_id", "trial_version", "trial_context"):
        row.pop(key)
    return {**row, "figure_id": "sway", "figure_version": 1, **changes}


def packet(value):
    response = envelope(value, "rider.figures")
    return httpx.Response(200, content=response.body, headers=response.headers)


@pytest.mark.parametrize(
    "changes",
    [
        {"figure_version": True},
        {"figure_version": 2},
        {"figure_id": "native"},
        {"operator_consent": False},
        {"operator_consent": 1},
        {"steps": []},
        {"direction": "forward"},
        {"lease_s": 20},
        {"panel_id": ""},
        {"settings_revision": True},
    ],
)
def test_scope_rejects_native_actions_arbitrary_steps_and_ambiguous_fields(changes):
    with pytest.raises(ValidationError):
        FigureRequest.model_validate(request(**changes))


def test_start_and_heartbeat_share_explicit_panel_scope():
    assert FigureRequest.model_validate(request()).figure_run_id is None
    assert (
        FigureRequest.model_validate(request(figure_run_id="run-own")).figure_run_id
        == "run-own"
    )
    assert expected_contract("/api/motion/figures/runs/run-own/cancel") == (
        "rider.figures",
        1,
    )
    with pytest.raises(Exception, match="figure_run_invalid"):
        identifier("../outside")


async def test_catalog_status_and_export_never_send_commands(monkeypatch):
    calls = []
    run = {
        "version": 1,
        "run_id": "run-own",
        "figure_id": "sway",
        "phase": "completed",
        "physical_completion": "unverified",
        "events": [{"kind": "completed"}],
    }

    def receive(req):
        calls.append((req.method, req.url.path))
        return packet({"version": 1, "run": run, "stale": False})

    service, client = await setup_proxy(monkeypatch, receive)
    try:
        for path in [
            "/motion/figures",
            "/motion/figures/runs/run-own",
            "/motion/figures/runs/run-own/export",
        ]:
            result = await client.get("/api/v1/rider" + path)
            assert result.status_code == 200, result.text
        assert all(method == "GET" for method, _ in calls)
        assert result.json()["run"]["physical_completion"] == "unverified"
        assert 'filename="figure-run-own.json"' in result.headers["content-disposition"]
    finally:
        await client.aclose()
        await service.close()


async def test_start_proxies_one_unchanged_intent_and_keeps_figure_identity(
    monkeypatch,
):
    calls = []

    def receive(req):
        calls.append(req)
        response = envelope(
            {"ok": True, "confirmed": True, "figure_run_id": "run-abc"}, "rider.figures"
        )
        return httpx.Response(200, content=response.body, headers=response.headers)

    service, client = await setup_proxy(monkeypatch, receive)
    try:
        original = request()
        response = await client.post(
            "/api/v1/rider/motion/figures/runs",
            json=original,
            headers={"X-Rider-Session": service.session},
        )
        assert response.status_code == 200, response.text
        assert len(calls) == 1 and calls[0].url.path == "/api/motion/figures/runs"
        assert json.loads(calls[0].content) == original
    finally:
        await client.aclose()
        await service.close()


async def test_stale_session_cannot_start_or_cancel_and_cancel_is_scoped(monkeypatch):
    calls = []
    service, client = await setup_proxy(
        monkeypatch, lambda req: calls.append(req) or packet({"ok": True})
    )
    try:
        for path, payload in [
            ("/motion/figures/runs", request()),
            ("/motion/figures/runs/run-own/cancel", {}),
        ]:
            response = await client.post(
                "/api/v1/rider" + path, json=payload, headers={"X-Rider-Session": "old"}
            )
            assert response.status_code == 409
        assert calls == []
        response = await client.post(
            "/api/v1/rider/motion/figures/runs/run-own/cancel",
            json={},
            headers={"X-Rider-Session": service.session},
        )
        assert response.status_code == 200
        assert len(calls) == 1 and calls[0].url.path.endswith("/run-own/cancel")
    finally:
        await client.aclose()
        await service.close()


def test_recorded_motion_intent_retains_own_recipe_metadata():
    from venom_module_rider.motion_contract import MotionIntent

    row = MotionIntent(
        action="arm", figure_id="sway", figure_version=1, figure_run_id="run-own"
    )
    assert row.model_dump()["figure_id"] == "sway"
    assert copy.deepcopy(row).figure_version == 1


async def test_stop_supersedes_a_dispatched_figure_start(monkeypatch):
    entered = asyncio.Event()
    calls = []

    async def handle(incoming):
        calls.append(incoming.url.path)
        if incoming.url.path == "/api/motion/figures/runs":
            entered.set()
            await asyncio.Event().wait()
        reply = envelope({"ok": True, "confirmed": True})
        return httpx.Response(200, content=reply.body, headers=reply.headers)

    service, client = await setup_proxy(monkeypatch, handle)
    try:
        pending = asyncio.create_task(service.control(request(), service.session))
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(service.control({"action": "stop"}, service.session), 1)
        with pytest.raises(RiderError, match="command_superseded"):
            await pending
        assert calls == ["/api/motion/figures/runs", "/api/safety"]
    finally:
        await client.aclose()
        await service.close()


async def test_older_pi_without_figures_never_receives_generic_arm(monkeypatch):
    calls = []

    def receive(req):
        calls.append(req.url.path)
        return httpx.Response(404, json={"error": "not_found"})

    service, client = await setup_proxy(monkeypatch, receive)
    try:
        response = await client.post(
            "/api/v1/rider/motion/figures/runs",
            json=request(),
            headers={"X-Rider-Session": service.session},
        )
        assert response.status_code >= 400
        assert calls == ["/api/motion/figures/runs"]
    finally:
        await client.aclose()
        await service.close()
