"""The reconnect button repairs transport, never retries an actuator request."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from venom_module_rider import connection_recovery
from venom_module_rider.api.routes import router
from venom_module_rider.client import PiClient
from venom_module_rider.config import Device, configured_devices
from venom_module_rider.demo import DemoPi, create_app
from venom_module_rider.errors import RiderError
from venom_module_rider.service import RiderService


def fixture_service(reconnect=None):
    pi = DemoPi()
    app = create_app(pi)
    reachable = [True]
    reads = []

    async def handler(request):
        reads.append(request.method)
        if not reachable[0]:
            raise httpx.ConnectError("disconnected fixture", request=request)
        return await httpx.ASGITransport(app=app).handle_async_request(request)

    device = Device(
        "fixture", "Fixture", "http://127.0.0.1:18881", reconnect_command=reconnect
    )
    service = RiderService(
        (device,),
        client_factory=lambda row: PiClient(
            row, transport=httpx.MockTransport(handler)
        ),
    )
    return service, pi, reachable, reads


async def test_refresh_discards_cached_reads_without_hardware_mutations(monkeypatch):
    service, pi, _, reads = fixture_service("/fixture/recover")
    await service.restore("fixture")
    await service.status("control")
    cached = service.cache["/api/calibration/controls"][1]
    cached["data"]["revision"] = 987

    async def unexpected(_):
        pytest.fail("healthy connection restarted")

    monkeypatch.setattr(connection_recovery, "recover_connection", unexpected)
    session = service.session
    result = await service.refresh(session)
    assert result["recovery"] == {"attempted": False, "state": "not_needed"}
    assert (
        service.data(await service.read("/api/calibration/controls", 30))["revision"]
        != 987
    )
    assert result["connection"]["status"] == "online"
    assert session == service.session
    assert service.device.id == "fixture"
    assert pi.state == "DISARMED"
    assert not pi.camera_on
    assert set(reads) == {"GET"}
    await service.close()


async def test_offline_refresh_runs_one_recovery_for_concurrent_panels(monkeypatch):
    service, pi, reachable, reads = fixture_service("/fixture/recover")
    await service.restore("fixture")
    reachable[0] = False
    assert (await service.status("control"))["connection"]["status"] == "offline"
    entered, release = asyncio.Event(), asyncio.Event()
    recovered = []

    async def reconnect(path):
        recovered.append(path)
        entered.set()
        await release.wait()
        reachable[0] = True

    monkeypatch.setattr(connection_recovery, "recover_connection", reconnect)
    first = asyncio.create_task(service.refresh(service.session))
    await entered.wait()
    second = asyncio.create_task(service.refresh(service.session))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second)
    assert recovered == ["/fixture/recover"]
    assert all(result["connection"]["status"] == "online" for result in results)
    assert all(result["recovery"]["state"] == "recovered" for result in results)
    assert not pi.camera_on
    assert pi.state == "DISARMED"
    assert set(reads) == {"GET"}
    await service.close()


@pytest.mark.parametrize(
    "error", [None, "connection_recovery_failed", "connection_recovery_timeout"]
)
async def test_offline_refresh_reports_failure_instead_of_inventing_ready(
    monkeypatch, error
):
    service, _, reachable, _ = fixture_service("/fixture/recover" if error else None)
    await service.restore("fixture")
    reachable[0] = False

    async def fail(_):
        raise RiderError(error)

    monkeypatch.setattr(connection_recovery, "recover_connection", fail)
    result = await service.refresh(service.session)
    assert result["connection"]["status"] == "offline"
    assert result["recovery"]["state"] == ("failed" if error else "not_configured")
    if error:
        assert result["recovery"]["error"] == error
    await service.close()


async def test_helper_exit_zero_is_not_evidence_that_pi_recovered(monkeypatch):
    service, _, reachable, _ = fixture_service("/fixture/recover")
    await service.restore("fixture")
    reachable[0] = False

    async def no_recovery(_):
        pass

    monkeypatch.setattr(connection_recovery, "recover_connection", no_recovery)
    result = await service.refresh(service.session)
    assert result["connection"]["status"] == "offline"
    assert result["recovery"]["state"] == "failed"
    await service.close()


async def test_refresh_api_requires_current_session_and_rejects_command_arguments():
    service, _, _, reads = fixture_service()
    await service.restore("fixture")
    app = FastAPI()
    app.state.rider = service
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        path = "/api/v1/rider/connection/refresh"
        assert (await client.post(path, json={})).status_code == 409
        headers = {"X-Rider-Session": service.session}
        assert (
            await client.post(path, headers=headers, json={"command": "arm"})
        ).status_code == 422
        result = await client.post(path, headers=headers, json={})
        assert result.status_code == 200
        assert result.json()["connection"]["status"] == "online"
    assert set(reads) == {"GET"}
    await service.close()


async def test_refresh_without_selection_does_not_spawn_transport(monkeypatch):
    service = RiderService(())
    result = await service.refresh(service.session)
    assert result["connection"]["status"] == "unconfigured"
    await service.close()


@pytest.mark.parametrize(
    "command", ["relative/script", ["/bin/sh"], "", "/tmp/bad\nscript"]
)
def test_recovery_is_an_absolute_server_path_and_never_a_browser_command(command):
    row = {
        "id": "fixture",
        "label": "Fixture",
        "url": "http://localhost",
        "reconnect_command": command,
    }
    with pytest.raises(RiderError, match="invalid_device_configuration"):
        configured_devices(json.dumps([row]))
    row.update(reconnect_command="/trusted/transport-recover", mode="demo")
    with pytest.raises(RiderError, match="invalid_device_configuration"):
        configured_devices(json.dumps([row]))
    row["mode"] = "device"
    device = configured_devices(json.dumps([row]))[0]
    assert device.reconnect_command == "/trusted/transport-recover"
    assert "reconnect_command" not in device.public()


async def test_runner_has_no_shell_or_tokens_and_bounded_process_cleanup(monkeypatch):
    calls = []
    monkeypatch.setenv("RIDER_AGENT_TOKEN", "fixture-not-a-real-secret")
    monkeypatch.setenv("SONAR_TOKEN", "fixture-not-a-real-secret")

    async def wait():
        return 0

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, wait=wait)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    await connection_recovery.recover_connection("/trusted/recover")
    args, kwargs = calls[0]
    assert args == ("/trusted/recover",)
    assert "RIDER_AGENT_TOKEN" not in kwargs["env"]
    assert "SONAR_TOKEN" not in kwargs["env"]
    assert kwargs["stdin"] == asyncio.subprocess.DEVNULL
    assert kwargs["stdout"] == kwargs["stderr"] == asyncio.subprocess.DEVNULL


@pytest.mark.parametrize("kind", ["spawn", "exit", "timeout", "cancel"])
async def test_runner_classifies_failures_and_reaps_owned_process(monkeypatch, kind):
    waits = 0
    process = SimpleNamespace(returncode=None, pid=123456789)
    killed = []

    async def wait():
        nonlocal waits
        waits += 1
        if kind == "timeout" and waits == 1:
            await asyncio.Event().wait()
        if kind == "cancel" and waits == 1:
            raise asyncio.CancelledError
        process.returncode = 1
        return 1

    process.wait = wait

    async def spawn(*_args, **_kwargs):
        if kind == "spawn":
            raise FileNotFoundError
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(
        connection_recovery.os, "killpg", lambda *args: killed.append(args)
    )
    monkeypatch.setattr(connection_recovery, "RECOVERY_TIMEOUT", 0.01)
    expected = asyncio.CancelledError if kind == "cancel" else RiderError
    with pytest.raises(expected):
        await connection_recovery.recover_connection("/trusted/recover")
    assert bool(killed) == (kind in {"timeout", "cancel"})
    if killed:
        assert waits == 2


@pytest.mark.parametrize(
    "error", ["connection_recovery_failed", "connection_recovery_timeout"]
)
async def test_final_pi_read_wins_when_helper_failed_after_transport_recovered(
    monkeypatch, error
):
    service, pi, reachable, reads = fixture_service("/fixture/recover")
    await service.restore("fixture")
    reachable[0] = False

    async def late_recovery(_):
        reachable[0] = True
        raise RiderError(error)

    monkeypatch.setattr(connection_recovery, "recover_connection", late_recovery)
    result = await service.refresh(service.session)
    assert result["connection"]["status"] == "online"
    assert result["recovery"] == {
        "attempted": True,
        "state": "recovered",
        "error": error,
    }
    assert pi.state == "DISARMED"
    assert not pi.camera_on
    assert set(reads) == {"GET"}
    await service.close()


async def test_read_only_guard_rejects_transport_recovery_but_allows_status_get(
    monkeypatch,
):
    from venom_module_rider.api import routes

    service, _, _, reads = fixture_service("/fixture/recover")
    await service.restore("fixture")

    def restricted(**_):
        raise PermissionError("fixture read-only environment")

    async def unexpected(_):
        pytest.fail("read-only recovery ran the transport helper")

    monkeypatch.setattr(routes, "ensure_module_mutation_allowed", restricted)
    monkeypatch.setattr(connection_recovery, "recover_connection", unexpected)
    app = FastAPI()
    app.state.rider = service
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        result = await client.post(
            "/api/v1/rider/connection/refresh",
            headers={"X-Rider-Session": service.session},
            json={},
        )
        assert result.status_code == 403
        assert result.json()["detail"]["code"] == "environment_read_only"
        status = await client.get("/api/v1/rider/status?section=navigation")
        assert status.status_code == 200
        assert status.json()["connection"]["status"] == "online"
    assert service.refresh_task is None
    assert set(reads) == {"GET"}
    await service.close()
