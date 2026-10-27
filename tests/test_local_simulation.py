"""Built-in Demo runs without any physical transport or background server."""

import asyncio
import time

import httpx

from venom_module_rider.config import Device
from venom_module_rider.service import RiderService


def intent(service, state, action="move", **values):
    return {
        "action": action,
        "source": "manual",
        "execution_context": "panel",
        "request_id": "sim-" + str(time.monotonic_ns()),
        "generation": state["generation"],
        "seq": state["last_seq"] + 1,
        "ts": time.time(),
        "ttl_ms": 500,
        "settings_revision": state["manual_settings"]["revision"],
        "panel_id": "sim-panel",
        "device_settings": True,
        "operator_intervention_version": 1,
        "intervene": True,
        "operator_consent": True,
        "operator_plan": "Explicit simulated " + action,
        "owner": "manual",
        "arming_mode": "operator",
        **values,
    }


async def safety(service):
    return (await service.read("/api/control/state", 0, force=True))["data"]["safety"]


async def test_demo_has_no_network_and_simulates_each_direction_pose_and_stop(
    monkeypatch,
):
    async def no_network(*args, **kwargs):
        raise AssertionError("Demo attempted a real socket")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
    service = RiderService(())
    try:
        await service.restore("demo")
        state = await safety(service)
        assert state["state"] == "DISARMED"
        assert state["simulated"] is True
        for direction, axis, sign in [
            ("forward", "lx", 1),
            ("backward", "lx", -1),
            ("left", "az", -1),
            ("right", "az", 1),
        ]:
            result = await service.control(
                intent(service, state, direction=direction, mode="pulse"),
                service.session,
            )
            state = result["data"]["safety"]
            assert state["output"][axis] * sign > 0
            assert (
                result["data"]["ack"]["generation"]
                != result["data"]["ack"]["command_generation"]
            )
            assert result["data"]["server_time"] > 0
        for action, values in [
            ("height", {"height": 100}),
            ("balance", {"enabled": False}),
        ]:
            result = await service.control(
                intent(service, state, action, **values), service.session
            )
            state = result["data"]["safety"]
            assert state["output"] == {"lx": 0.0, "az": 0.0}
            assert state["actuators"][action][
                "value" if action == "height" else "enabled"
            ] == next(iter(values.values()))
        await service.control({"action": "stop"}, service.session)
        state = await safety(service)
        assert state["state"] == "DISARMED"
        assert state["output"] == {"lx": 0.0, "az": 0.0}
        assert service.client.device.mode == "demo"
    finally:
        await service.close()


async def test_offline_physical_target_never_prevents_demo_and_demo_uses_no_socket(
    monkeypatch,
):
    calls = []

    async def offline(transport, request):
        calls.append(request.url.path)
        raise httpx.ConnectError("Physical fixture is off", request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", offline)
    service = RiderService((Device("prod", "Prod", "http://127.0.0.1:9"),))
    try:
        await service.restore("prod")
        result = await service.select("demo", service.session)
        assert result["device_id"] == "demo"
        assert result["selection_warning"] == "previous_device_stop_unconfirmed"
        after = len(calls)
        state = await safety(service)
        await service.control(
            intent(service, state, direction="forward", mode="pulse"), service.session
        )
        await service.control({"action": "stop"}, service.session)
        assert len(calls) == after
        assert (await safety(service))["state"] == "DISARMED"
    finally:
        await service.close()


async def test_unresponsive_previous_target_has_a_bounded_detach(monkeypatch):
    service = RiderService(())

    async def unresponsive():
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_stop_previous", unresponsive)
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(service.select("demo", service.session), 1.5)
        assert result["device_id"] == "demo"
        assert time.monotonic() - started < 1.5
    finally:
        await service.close()


async def test_quick_release_is_scoped_and_cannot_rearm_or_repeat():
    service = RiderService(())
    try:
        await service.restore("demo")
        state = await safety(service)
        original = intent(service, state, direction="forward", mode="hold")
        await service.control(original, service.session)
        release = {
            **original,
            "mode": "release",
            "request_id": "release",
            "seq": original["seq"] + 1,
            "ts": time.time(),
            "supersedes_operation_id": original["request_id"],
        }
        release.pop("operator_consent")
        release.pop("operator_plan")
        await service.control(release, service.session)
        assert (await safety(service))["output"] == {"lx": 0.0, "az": 0.0}
    finally:
        await service.close()


async def test_builtin_simulation_is_not_reported_as_unselected_physical_inventory():
    from fastapi import FastAPI

    from venom_module_rider.integrations import observe_integration

    service = RiderService(())
    app = FastAPI()
    app.state.rider = service
    try:
        assert (await observe_integration(app))["resources"] == []
        await service.restore("demo")
        await service.status("control")
        resources = (await observe_integration(app))["resources"]
        assert len(resources) == 1
        assert resources[0]["selected"] is True
        assert resources[0]["mode"] == "demo"
        assert resources[0]["reason_code"] == "demo_observation"
    finally:
        await service.close()
