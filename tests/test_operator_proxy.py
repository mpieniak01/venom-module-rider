"""Operator priority crosses the real proxy, using only isolated Pi fixtures."""

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError
from test_navigation_trial_api import Permit, body, setup_proxy
from test_rider import command

from venom_module_rider.api.schemas import Command, NavigationTrialRequest
from venom_module_rider.demo import DemoPi, envelope
from venom_module_rider.demo_navigation import ReplayNavigation
from venom_module_rider.errors import RiderError


def packet(data=None):
    response = envelope(data or {"safety": {}}, "rider.safety")
    return httpx.Response(200, content=response.body, headers=response.headers)


def intent(action, **changes):
    return {
        "action": action,
        "request_id": "operator-1",
        "generation": "generation",
        "seq": 3,
        "ts": 1.0,
        "ttl_ms": 500,
        "settings_revision": 2,
        "source": "manual",
        "execution_context": "panel",
        "panel_id": "panel-one",
        **changes,
    }


@pytest.mark.parametrize("action", ["navigation_pause", "navigation_resume"])
async def test_new_actions_preserve_authoritative_envelope(monkeypatch, action):
    received = []

    def handle(request):
        received.append(request)
        return packet()

    service, client = await setup_proxy(monkeypatch, handle)
    try:
        original = intent(action)
        response = await client.post(
            "/api/v1/rider/control",
            json=original,
            headers={"X-Rider-Session": service.session},
        )
        assert response.status_code == 200
        assert len(received) == 1
        assert received[0].url.path == "/api/control"
        assert json.loads(received[0].content) == original
    finally:
        await client.aclose()
        await service.close()


@pytest.mark.parametrize(
    "action,extra",
    [
        ("navigation_pause", {}),
        (
            "move",
            {
                "intervene": True,
                "direction": "left",
                "mode": "pulse",
                "device_settings": True,
            },
        ),
    ],
)
async def test_intervention_cancels_pending_resume_and_uses_reserved_lane(
    monkeypatch, action, extra
):
    started = asyncio.Event()
    received = []

    async def handle(request):
        data = json.loads(request.content)
        received.append(data)
        if data["action"] == "navigation_resume":
            started.set()
            await asyncio.Event().wait()
        return packet()

    service, client = await setup_proxy(monkeypatch, handle)
    try:
        resume = asyncio.create_task(
            service.control(intent("navigation_resume"), service.session)
        )
        await asyncio.wait_for(started.wait(), 1)
        original = intent(action, **extra)
        result = await service.control(original, service.session)
        assert result["data"] == {"safety": {}}
        with pytest.raises(RiderError, match="command_superseded"):
            await resume
        assert received[-1] == original
        assert service.client.operator_lane.locked() is False
    finally:
        await client.aclose()
        await service.close()


async def test_global_stop_does_not_wait_for_pending_intervention(monkeypatch):
    entered = asyncio.Event()

    async def handle(request):
        if json.loads(request.content)["action"] == "navigation_pause":
            entered.set()
            await asyncio.Event().wait()
        return packet()

    service, client = await setup_proxy(monkeypatch, handle)
    try:
        pending = asyncio.create_task(
            service.control(intent("navigation_pause"), service.session)
        )
        await asyncio.wait_for(entered.wait(), 1)
        stopped = await asyncio.wait_for(
            service.control({"action": "stop"}, "stale-session"), 1
        )
        assert stopped["data"] == {"safety": {}}
        with pytest.raises(RiderError, match="command_superseded"):
            await pending
        assert not service.client.operator_lane.locked()
    finally:
        await client.aclose()
        await service.close()


async def test_late_trial_response_cannot_outlive_operator_intervention(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def handle(request):
        if request.url.path == "/api/navigation/trial":
            entered.set()
            await release.wait()
            response = envelope({"ok": True}, "rider.navigation_loop")
            return httpx.Response(200, content=response.body, headers=response.headers)
        return packet()

    service, client = await setup_proxy(monkeypatch, handle)
    try:
        pending = asyncio.create_task(
            service.mutate("/api/navigation/trial", body(), service.session)
        )
        await asyncio.wait_for(entered.wait(), 1)
        await service.control(intent("navigation_pause"), service.session)
        release.set()
        with pytest.raises(RiderError, match="command_superseded"):
            await pending
    finally:
        release.set()
        await client.aclose()
        await service.close()


@pytest.mark.parametrize("version", [True, 0, 2, "1"])
def test_optin_version_is_strict_and_needs_specific_panel(version):
    with pytest.raises(ValidationError):
        NavigationTrialRequest(
            **body(operator_control_version=version, panel_id="panel-one")
        )


def test_optin_requires_panel_and_old_schema_is_unchanged():
    with pytest.raises(ValidationError):
        NavigationTrialRequest(**body(operator_control_version=1))
    original = body()
    assert NavigationTrialRequest(**original).model_dump(exclude_none=True) == original
    assert Command(action="move", intervene=True).intervene is True
    with pytest.raises(ValidationError):
        Command(action="move", intervene=1)


async def test_demo_warm_pause_and_resume_retain_worker_camera_and_original_lease():
    nav = ReplayNavigation(b"fixture", Permit, model_sha="a" * 64)
    pi = DemoPi(navigation=nav)
    nav.policy.update(verified=True, calibration_id="demo-only", model_sha256="a" * 64)
    nav.request = lambda *_: asyncio.sleep(
        0, result={"state": "unknown", "action": "stop"}
    )
    try:
        await nav.start_trial(
            body(
                generation=pi.generation,
                config_revision=nav.policy["revision"],
                settings_revision=pi.controls["revision"],
                panel_id="panel-one",
                operator_control_version=1,
            )
        )
        task, deadline = nav.task, pi.arming_deadline
        assert pi.panel_id == "panel-one"
        assert pi.safety()["operator_control"]["state"] == "qualifying"
        response = pi.control(command(pi, "navigation_pause", panel_id="panel-one"))
        assert response.status_code == 200
        assert nav.operator_state == "paused" and pi.owner == "manual"
        assert nav.task is task and not task.done() and pi.camera_on
        assert pi.arming_deadline == deadline
        response = pi.control(command(pi, "navigation_resume", panel_id="panel-one"))
        assert response.status_code == 200
        assert nav.operator_state == "qualifying" and pi.owner == "navigation"
        assert nav.task is task and pi.camera_on and pi.arming_deadline == deadline
        pi.control({"action": "stop"})
        refused = pi.control(command(pi, "navigation_resume", panel_id="panel-one"))
        assert refused.status_code == 409
        assert pi.state == "DISARMED"
    finally:
        await nav.close()


async def test_priority_keeps_automatic_capture_running(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    service, client = await setup_proxy(monkeypatch, lambda _: packet())
    invalidate = Mock()
    service.samples = SimpleNamespace(automatic=SimpleNamespace(invalidate=invalidate))
    try:
        await service.control(intent("navigation_pause"), service.session)
        await service.control(intent("move", intervene=True), service.session)
        invalidate.assert_not_called()
        await service.control({"action": "stop"}, service.session)
        invalidate.assert_called_once_with("auto_global_stop")
    finally:
        await client.aclose()
        await service.close()


async def test_demo_late_go_does_not_overwrite_operator_or_restart_producer():
    entered, release = asyncio.Event(), asyncio.Event()
    frames, accepted = [], []

    class CountingPermit(Permit):
        def accept(self, *args):
            accepted.append(args)
            return False

    async def request(meta, _):
        frames.append(meta)
        if len(frames) == 1:
            entered.set()
            await release.wait()
        return {"state": "clear", "action": "go", "clear_count": 3}

    nav = ReplayNavigation(
        b"fixture", CountingPermit, model_sha="a" * 64, request=request
    )
    pi = DemoPi(navigation=nav)
    nav.policy.update(verified=True, calibration_id="demo-only", model_sha256="a" * 64)
    try:
        await nav.start_trial(
            body(
                generation=pi.generation,
                config_revision=nav.policy["revision"],
                settings_revision=pi.controls["revision"],
                panel_id="panel-one",
                operator_control_version=1,
            )
        )
        await asyncio.wait_for(entered.wait(), 1)
        task, first_run = nav.task, nav.status["run_id"]
        response = pi.control(
            command(
                pi,
                "move",
                panel_id="panel-one",
                intervene=True,
                device_settings=True,
                direction="left",
                mode="pulse",
            )
        )
        assert response.status_code == 200
        manual_generation = pi.generation
        release.set()
        await asyncio.sleep(0.03)
        assert pi.generation == manual_generation and pi.owner == "manual"
        assert accepted == []
        assert nav.task is task and pi.camera_on and nav.operator_state == "paused"
        pi.control(command(pi, "navigation_resume", panel_id="panel-one"))
        await asyncio.sleep(0.25)
        assert nav.task is task and nav.status["run_id"] != first_run
        assert frames[-1]["run_id"] != frames[0]["run_id"]
    finally:
        release.set()
        await nav.close()


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"panel_id": None}, "operator_panel_required"),
        ({"panel_id": "foreign"}, "panel_owner_mismatch"),
        ({"settings_revision": -1}, "manual_settings_stale"),
        ({"action": "navigation_resume"}, "navigation_already_active"),
    ],
)
async def test_demo_rejects_warm_authority_errors(changes, expected):
    nav = ReplayNavigation(b"fixture", Permit, model_sha="a" * 64)
    pi = DemoPi(navigation=nav)
    nav.policy.update(verified=True, calibration_id="demo-only", model_sha256="a" * 64)
    nav.request = lambda *_: asyncio.sleep(
        0, result={"state": "unknown", "action": "stop"}
    )
    try:
        await nav.start_trial(
            body(
                generation=pi.generation,
                config_revision=nav.policy["revision"],
                settings_revision=pi.controls["revision"],
                panel_id="panel-one",
                operator_control_version=1,
            )
        )
        response = pi.control(
            command(pi, "navigation_pause", panel_id="panel-one") | changes
        )
        assert response.status_code == 409
        assert expected in response.body.decode()
    finally:
        await nav.close()


async def test_demo_expired_lease_keeps_observation_without_rearming():
    import time

    frames = []

    async def request(meta, _):
        frames.append(meta)
        return {"state": "unknown", "action": "stop"}

    nav = ReplayNavigation(b"fixture", Permit, model_sha="a" * 64, request=request)
    pi = DemoPi(navigation=nav)
    nav.policy.update(verified=True, calibration_id="demo-only", model_sha256="a" * 64)
    try:
        await nav.start_trial(
            body(
                generation=pi.generation,
                config_revision=nav.policy["revision"],
                settings_revision=pi.controls["revision"],
                panel_id="panel-one",
                operator_control_version=1,
            )
        )
        task = nav.task
        first_run = nav.status["run_id"]
        pi.arming_deadline = time.monotonic() - 1
        await asyncio.sleep(0.03)
        assert frames[-1]["mode"] == "observe"
        assert frames[-1]["run_id"] != first_run
        assert nav.task is task and not task.done() and pi.camera_on
        assert pi.state == "DISARMED" and nav.operator_state == "paused"
        refused = pi.control(command(pi, "navigation_resume", panel_id="panel-one"))
        assert refused.status_code == 409 and pi.state == "DISARMED"
    finally:
        await nav.close()


async def test_proxy_preserves_exact_superseded_resume_identifier(monkeypatch):
    seen = []
    service, client = await setup_proxy(
        monkeypatch, lambda request: seen.append(request) or packet()
    )
    try:
        original = intent(
            "move",
            intervene=True,
            supersedes_operation_id="pending-resume",
            direction="left",
            mode="pulse",
            device_settings=True,
        )
        response = await client.post(
            "/api/v1/rider/control",
            json=original,
            headers={"X-Rider-Session": service.session},
        )
        assert response.status_code == 200
        assert len(seen) == 1 and json.loads(seen[0].content) == original
    finally:
        await client.aclose()
        await service.close()


@pytest.mark.parametrize("value", ["", " ", "x" * 129, True, 4])
def test_superseded_operation_is_a_strict_bounded_identifier(value):
    with pytest.raises(ValidationError):
        Command(action="navigation_pause", supersedes_operation_id=value)


@pytest.mark.parametrize(
    "action,intervene",
    [("navigation_resume", None), ("move", None), ("height", True), ("stop", None)],
)
def test_supersession_is_restricted_to_explicit_operator_intervention(
    action, intervene
):
    with pytest.raises(ValidationError):
        Command(action=action, intervene=intervene, supersedes_operation_id="resume")


@pytest.mark.parametrize("action", ["navigation_pause", "move"])
async def test_demo_exact_pending_resume_can_be_interrupted_before_browser_ack(action):
    nav = ReplayNavigation(b"fixture", Permit, model_sha="a" * 64)
    pi = DemoPi(navigation=nav)
    nav.policy.update(verified=True, calibration_id="demo-only", model_sha256="a" * 64)
    nav.request = lambda *_: asyncio.sleep(
        0, result={"state": "unknown", "action": "stop"}
    )
    try:
        await nav.start_trial(
            body(
                generation=pi.generation,
                config_revision=nav.policy["revision"],
                settings_revision=pi.controls["revision"],
                panel_id="panel-one",
                operator_control_version=1,
            )
        )
        pi.control(command(pi, "navigation_pause", panel_id="panel-one"))
        resume = command(
            pi, "navigation_resume", panel_id="panel-one", request_id="pending-resume"
        )
        assert pi.control(resume).status_code == 200
        pending_generation = pi.generation
        # The pending response has not delivered this generation to the browser.
        correction = command(
            pi,
            action,
            panel_id="panel-one",
            generation=resume["generation"],
            seq=resume["seq"] + 1,
            supersedes_operation_id="pending-resume",
            **(
                {
                    "intervene": True,
                    "direction": "left",
                    "mode": "pulse",
                    "device_settings": True,
                }
                if action == "move"
                else {}
            ),
        )
        bad = pi.control(correction | {"supersedes_operation_id": "another-operation"})
        assert bad.status_code == 409 and pi.generation == pending_generation
        # New navigation sequence numbers do not invalidate the original panel lane.
        pi.seq += 20
        accepted = pi.control(correction)
        assert accepted.status_code == 200
        assert pi.owner == "manual" and nav.operator_state == "paused"
        assert pi.generation != pending_generation
        repeated = pi.control(correction)
        assert repeated.status_code == 409
    finally:
        await nav.close()
