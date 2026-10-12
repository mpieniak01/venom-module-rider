import asyncio
import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from venom_core.services.module_registry import include_optional_api_routers

from venom_module_rider.api.routes import router
from venom_module_rider.client import PiClient
from venom_module_rider.config import Device, configured_devices
from venom_module_rider.demo import DemoPi, create_app, envelope
from venom_module_rider.errors import RiderError
from venom_module_rider.service import RiderService

DEVICE = Device("demo", "Demo", "http://127.0.0.1:18880", "demo")


def domain(pi=None):
    pi = pi or DemoPi()
    transport = httpx.ASGITransport(app=create_app(pi))
    return RiderService(
        (DEVICE,),
        client_factory=lambda device: PiClient(
            device,
            transport=transport,
        ),
    ), pi


def host_app(service):
    app = FastAPI()
    app.state.rider = service
    app.include_router(router)
    return app


def command(pi, action, **fields):
    return {
        "action": action,
        "request_id": f"test-{time.monotonic_ns()}",
        "source": "manual",
        "execution_context": "panel",
        "generation": pi.generation,
        "seq": pi.seq + 1,
        "ts": time.time(),
        "ttl_ms": 500,
        "settings_revision": pi.controls["revision"],
        **fields,
    }


async def arm(service, pi):
    return await service.control(
        command(
            pi,
            "arm",
            operator_consent=True,
            operator_plan="Fixture manual test",
            owner="manual",
            arming_mode="operator",
        ),
        service.session,
    )


@pytest.mark.parametrize(
    "raw,code",
    [
        ("{}", "invalid_device_configuration"),
        ("not-json", "invalid_device_configuration"),
        (
            json.dumps([{"id": "a", "label": "A", "url": "file:///tmp"}]),
            "invalid_device_configuration",
        ),
        (
            json.dumps(
                [{"id": "a", "label": "A", "url": "http://user:secret@localhost"}]
            ),
            "invalid_device_configuration",
        ),
        (
            json.dumps(
                [
                    {
                        "id": "a",
                        "label": "A",
                        "url": "http://robot.invalid",
                        "mode": "demo",
                    }
                ]
            ),
            "demo_requires_loopback",
        ),
        (
            json.dumps([{"id": "a", "label": "A", "url": "http://localhost?url=bad"}]),
            "invalid_device_configuration",
        ),
        (
            json.dumps([{"id": "../a", "label": "A", "url": "http://localhost"}]),
            "invalid_device_configuration",
        ),
    ],
)
def test_invalid_config(raw, code):
    with pytest.raises(RiderError, match=code):
        configured_devices(raw)


async def test_unconfigured_does_not_create_observations():
    service = RiderService(())
    for section in ("overview", "control", "calibration", "resources", "face"):
        assert (await service.status(section))["connection"]["status"] == "unconfigured"
    assert service.client is None and not service.inflight
    await service.close()


async def test_status_preserves_domain_versions_and_measurement_units():
    service, pi = domain()
    await service.select("demo", service.session)
    result = await service.status()
    assert result["connection"]["mode"] == "demo"
    assert result["connection"]["status"] == "online"
    assert result["settings"]["contract_version"] == 2
    assert result["control"]["server_time"] > 0
    descriptor = result["settings"]["parameter_schema"]["actuator_fields"]["height"]
    assert descriptor["unit"] == "sdk_height_command"
    assert result["system"]["hist_ts"] and result["system"]["hist_cpu_peak"]
    sensors = result["telemetry"]["devices"]["xgo"]["sensor_quality"]["sensors"]
    assert sensors["yaw"]["unit"] == "sdk_raw"
    assert all(row["method"] == "GET" for row in pi.trace)
    assert pi.state == "DISARMED" and not pi.camera_on and not pi.lcd_on
    await service.close()


async def test_reads_deduplicate_and_idle_has_no_background_collector():
    service, pi = domain()
    await service.select("demo", service.session)
    await asyncio.gather(*[service.read("/sysinfo", 2) for _ in range(12)])
    assert sum(row["path"] == "/sysinfo" for row in pi.trace) == 1
    count = len(pi.trace)
    await asyncio.sleep(0.02)
    assert len(pi.trace) == count and not service.inflight
    await service.close()
    assert service.client is None and not service.cache


async def test_camera_off_skips_frames_and_keeps_binary_headers():
    service, pi = domain()
    await service.select("demo", service.session)
    with pytest.raises(RiderError, match="camera_off"):
        await service.camera("raw")
    assert not any(row["path"].startswith("/camera") for row in pi.trace)
    await service.mutate(
        "/api/logic/feature/s2_camera_preview", {"enabled": True}, service.session
    )
    content, mime, headers = await service.camera("raw")
    assert content.startswith(b"\x89PNG") and mime == "image/png"
    assert headers["x-rider-source"] == "demo"
    await service.close()


async def test_all_directions_pulse_hold_release_and_global_stop():
    service, pi = domain()
    await service.select("demo", service.session)
    await arm(service, pi)
    for direction in ("forward", "backward", "left", "right"):
        for mode in ("pulse", "hold", "release"):
            body = command(
                pi, "move", device_settings=True, direction=direction, mode=mode
            )
            result = await service.control(body, service.session)
            assert pi.trace[-1]["body"] == body
            assert not {"vx", "yaw", "duration"} & pi.trace[-1]["body"].keys()
            assert result["operation"]["physical_completion"] == "not_applicable"
            assert pi.state == "ARMED"
    await service.control({"action": "stop", "source": "manual"}, "old-session")
    assert pi.state == "DISARMED"
    await service.close()


async def test_no_replay_after_device_session_change():
    service, pi = domain()
    await service.select("demo", service.session)
    old = service.session
    await service.select(None, old)
    await service.select("demo", service.session)
    with pytest.raises(RiderError, match="stale_device_session"):
        await service.control(command(pi, "move"), old)
    assert not any(row["method"] == "POST" for row in pi.trace)
    await service.close()


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("generation", "old", "stale_generation"),
        ("source", "navigation", "manual_panel_only"),
        ("execution_context", "test", "manual_panel_only"),
        ("ts", 1, "expired_command"),
        ("seq", -1, "stale_sequence"),
    ],
)
async def test_invalid_envelope_does_not_reach_actuator(field, value, code):
    service, pi = domain()
    await service.select("demo", service.session)
    with pytest.raises(RiderError, match=code):
        await service.control(command(pi, "arm", **{field: value}), service.session)
    assert not any(row["method"] == "POST" for row in pi.trace)
    await service.close()


async def test_disarmed_and_settings_revision_refuse_movement():
    service, pi = domain()
    await service.select("demo", service.session)
    with pytest.raises(RiderError, match="motion_not_armed"):
        await service.control(command(pi, "move"), service.session)
    await arm(service, pi)
    with pytest.raises(RiderError, match="manual_settings_stale"):
        await service.control(
            command(pi, "height", height=95, settings_revision=999), service.session
        )
    await service.close()


async def test_profile_sync_conflict_and_settings_require_idle():
    service, pi = domain()
    await service.select("demo", service.session)
    session = service.session
    await service.settings(
        {
            "revision": 0,
            "values": {},
            "directions": {"backward": {"speed": 0.4}},
            "profile": {"action": "create", "name": "Desk"},
        },
        session,
    )
    profile = pi.controls["active_profile"]
    await service.settings(
        {"revision": 1, "values": {}, "directions": {"backward": {"speed": 0.5}}},
        session,
    )
    assert pi.controls["profile_modified"]
    await service.settings(
        {"revision": 2, "values": {}, "profile": {"action": "apply", "id": profile}},
        session,
    )
    assert pi.controls["directions"]["backward"]["speed"] == 0.4
    with pytest.raises(RiderError, match="manual_settings_conflict"):
        await service.settings({"revision": 0, "values": {}}, session)
    await arm(service, pi)
    await service.control(
        command(pi, "move", device_settings=True, direction="forward", mode="hold"),
        session,
    )
    with pytest.raises(RiderError, match="manual_settings_requires_idle"):
        await service.settings({"revision": 3, "values": {}}, session)
    assert pi.controls["revision"] == 3
    await service.close()


async def test_offline_and_stale_state_do_not_enable_motion():
    service, pi = domain()
    await service.select("demo", service.session)
    pi.offline = True
    result = await service.status()
    assert result["connection"]["status"] == "offline" and result["telemetry"] is None
    pi.offline = False
    pi.stale = True
    assert (await service.status("control"))["control"]["safety"]["stale"]
    with pytest.raises(RiderError, match="motion_not_ready"):
        await arm(service, pi)
    await service.close()


def test_actual_host_registry_optional_loading_and_cleanup(monkeypatch):
    monkeypatch.delenv("RIDER_DEFAULT_DEVICE", raising=False)
    manifest = Path(__file__).parents[1] / "module.json"
    settings = SimpleNamespace(
        API_OPTIONAL_MODULES="",
        CORE_MODULE_API_VERSION="1.0.0",
        CORE_RUNTIME_VERSION="1.5.0",
    )
    empty = FastAPI()
    assert include_optional_api_routers(empty, settings) == []
    with TestClient(empty) as client:
        assert client.get("/api/v1/rider/config").status_code == 404
    settings.API_OPTIONAL_MODULES = "manifest:" + str(manifest)
    service, pi = domain()
    app = FastAPI()
    app.state.rider = service
    assert include_optional_api_routers(app, settings) == ["rider"]
    with TestClient(app) as client:
        config = client.get("/api/v1/rider/config").json()
        response = client.post(
            "/api/v1/rider/config",
            json={"device_id": "demo"},
            headers={"X-Rider-Session": config["session"]},
        )
        assert response.status_code == 200
        assert (
            client.get("/api/v1/rider/status").json()["connection"]["status"]
            == "online"
        )
        assert all(row["method"] == "GET" for row in pi.trace)
    assert service.closed and service.client is None


def test_origin_session_consent_and_explicit_face_operations(monkeypatch):
    monkeypatch.delenv("RIDER_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("RIDER_DEFAULT_DEVICE", raising=False)
    service, pi = domain()
    with TestClient(host_app(service)) as client:
        initial = client.get("/api/v1/rider/config").json()
        selected = client.post(
            "/api/v1/rider/config",
            json={"device_id": "demo"},
            headers={"X-Rider-Session": initial["session"]},
        ).json()
        headers = {"X-Rider-Session": selected["session"]}
        assert (
            client.post(
                "/api/v1/rider/settings", json={"revision": 0, "values": {}}
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/v1/rider/settings",
                json={"revision": 0, "values": {}},
                headers={**headers, "Origin": "https://foreign.invalid"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/rider/control", json=command(pi, "arm"), headers=headers
            ).status_code
            == 400
        )
        body = command(
            pi,
            "arm",
            operator_consent=True,
            operator_plan="Demo control",
            owner="manual",
            arming_mode="operator",
        )
        assert (
            client.post("/api/v1/rider/control", json=body, headers=headers).status_code
            == 200
        )
        body = command(pi, "move", vx=0.5, duration=0.2)
        assert (
            client.post("/api/v1/rider/control", json=body, headers=headers).status_code
            == 422
        )
        for section in ("resources", "face"):
            assert (
                client.get("/api/v1/rider/status?section=" + section).status_code == 200
            )
        preview = client.post(
            "/api/v1/rider/face/frame",
            json=copy.deepcopy(pi.library["gestures"][2]),
            headers=headers,
        )
        assert preview.headers["content-type"] == "image/png" and not pi.lcd_on
        assert (
            client.post(
                "/api/v1/rider/face/play",
                json={"sink": "lcd", "name": "happy"},
                headers=headers,
            ).status_code
            == 200
        )
        assert pi.lcd_on
        assert (
            client.post("/api/v1/rider/face/stop", json={}, headers=headers).status_code
            == 200
        )
        assert not pi.lcd_on
        assert client.get("/api/v1/rider/camera/not-a-stream").status_code == 404
        assert client.get("/api/v1/rider/logs/unsafe-unit").status_code == 400


async def test_stop_bypasses_pending_command_and_drops_its_result():
    pi = DemoPi()
    entered = asyncio.Event()

    async def handler(request):
        body = json.loads(request.content) if request.content else {}
        if request.url.path == "/api/control/state":
            return httpx.Response(
                200,
                json=json.loads(
                    envelope(
                        {
                            "safety": pi.safety(),
                            "server_time": time.time(),
                        }
                    ).body
                ),
            )
        if body.get("action") == "move":
            entered.set()
            await asyncio.sleep(2)
        return httpx.Response(200, json=json.loads(pi.control(body).body))

    service = RiderService(
        (DEVICE,),
        client_factory=lambda device: PiClient(
            device,
            transport=httpx.MockTransport(handler),
        ),
    )
    await service.select("demo", service.session)
    await arm(service, pi)
    pending = asyncio.create_task(
        service.control(
            command(
                pi,
                "move",
                direction="forward",
                mode="hold",
                device_settings=True,
            ),
            service.session,
        )
    )
    await entered.wait()
    started = time.monotonic()
    await service.control({"action": "stop"}, None)
    assert time.monotonic() - started < 0.2 and pi.state == "DISARMED"
    with pytest.raises(RiderError, match="command_superseded"):
        await pending
    await service.close()
