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
from venom_module_rider.client import PiClient, json_timeout
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


def test_face_fixture_matches_nested_pi_runtime_contract():
    with TestClient(create_app(DemoPi())) as client:
        path = "/api/face-studio/runtime"
        idle = client.get(path).json()["data"]
        assert idle["state"]["running"] is False
        assert "running" not in idle
        assert idle["actuation"] is False
        assert client.post(path + "/play", json={"sink": "lcd"}).status_code == 200
        active = client.get(path).json()["data"]
        assert active["state"] == {
            "running": True,
            "playing": True,
            "sink": "lcd",
        }
        assert client.post(path + "/stop", json={}).status_code == 200
        assert client.get(path).json()["data"]["state"]["running"] is False


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


def test_service_operations_have_separate_bounded_timeouts():
    assert (
        json_timeout("/api/logic/feature/s2_camera_preview", "POST", False).read == 20
    )
    assert json_timeout("/api/resource/lcd", "POST", False).read == 20
    assert json_timeout("/api/face-studio/runtime/play", "POST", False).read == 20
    assert json_timeout("/api/services/graph", "GET", False).read == 10
    for path, method, stop in [
        ("/api/control", "POST", False),
        ("/api/control", "POST", True),
        ("/api/resource/camera", "GET", False),
        ("/api/logic/feature/s2_camera_preview", "POST", True),
    ]:
        timeout = json_timeout(path, method, stop)
        assert timeout.read == 1.5
        assert timeout.connect == timeout.pool == 0.5


async def test_timed_out_operation_is_unconfirmed_and_never_retried():
    requests = []

    async def timeout(request):
        requests.append(request)
        raise httpx.ReadTimeout("late response", request=request)

    client = PiClient(DEVICE, transport=httpx.MockTransport(timeout))
    with pytest.raises(RiderError, match="pi_operation_timeout") as error:
        await client.json(
            "/api/logic/feature/s2_camera_preview",
            method="POST",
            body={"enabled": True},
        )
    assert error.value.status == 504
    assert error.value.details == {"outcome": "unknown"}
    assert len(requests) == 1
    await client.close()


async def test_timed_out_mutation_discards_old_observations(monkeypatch):
    service, pi = domain()
    await service.select("demo", service.session)
    await service.read("/api/resource/camera", 30)
    assert service.cache

    async def completed_without_ack(*args, **kwargs):
        pi.camera_on = True
        raise RiderError("pi_operation_timeout", 504, {"outcome": "unknown"})

    with monkeypatch.context() as patch:
        patch.setattr(service.client, "json", completed_without_ack)
        with pytest.raises(RiderError, match="pi_operation_timeout"):
            await service.mutate(
                "/api/logic/feature/s2_camera_preview",
                {"enabled": True},
                service.session,
            )
    assert not service.cache
    assert (await service.read("/api/resource/camera", 30))["data"]["activity"]["on"]
    await service.close()


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


async def test_concurrent_stops_both_complete_and_old_ack_keeps_newest_cache(
    monkeypatch,
):
    service, pi = domain()
    await service.select("demo", service.session)
    await service.read("/api/control/state", 30)
    original = service.client.json
    accepted = asyncio.Event()
    finish = asyncio.Event()
    count = 0

    async def delayed_stop(*args, **kwargs):
        nonlocal count
        count += 1
        order = count
        result = await original(*args, **kwargs)
        if order == 1:
            accepted.set()
            await finish.wait()
        return result

    monkeypatch.setattr(service.client, "json", delayed_stop)
    first = asyncio.create_task(service.control({"action": "stop"}, service.session))
    await asyncio.wait_for(accepted.wait(), 1)
    second = await service.control({"action": "disarm"}, service.session)
    finish.set()
    earlier = await first
    assert earlier["ok"] and second["ok"]
    latest = service.cache["/api/control/state"][1]["data"]["safety"]
    assert latest["generation"] == second["data"]["safety"]["generation"]
    assert not service.stops and not service.commands
    await service.close()


async def test_camera_instance_logs_use_valid_systemd_names():
    service, pi = domain()
    await service.select("demo", service.session)
    with TestClient(host_app(service)) as client:
        for instance in ["raw", "edge", "ssd"]:
            response = client.get(
                f"/api/v1/rider/logs/camera-capture@{instance}.service"
            )
            assert response.status_code == 200
        for unit in [".hidden.service", "camera.service.bad", "camera;echo.service"]:
            assert client.get(f"/api/v1/rider/logs/{unit}").status_code == 400
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


@pytest.mark.parametrize(
    "path,family,version",
    [
        ("/state", "rider.telemetry", 1),
        ("/sysinfo", "rider.system", 1),
        ("/api/logic/catalog", "rider.catalog", 2),
        ("/api/services/graph", "rider.service_graph", 2),
        ("/api/resource/lcd", "rider.resources", 1),
        ("/api/face-studio/gestures", "rider.face_studio", 1),
    ],
)
async def test_transport_rejects_unknown_versions(path, family, version):
    payload = json.loads(envelope({}, family, version + 99).body)
    client = PiClient(
        DEVICE,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    with pytest.raises(RiderError, match="unsupported_pi_contract"):
        await client.json(path)
    await client.close()


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(200, content=b"not-json"), "invalid_pi_response"),
        (httpx.Response(200, content=b'{"value":NaN}'), "invalid_pi_response"),
        (httpx.Response(200, json={"safety": {}}), "unsupported_pi_contract"),
        (
            httpx.Response(302, headers={"Location": "http://other.invalid"}),
            "pi_redirect_refused",
        ),
        (httpx.Response(404, json={"error": "not_found"}), "not_found"),
    ],
)
async def test_transport_invalid_or_error_response(response, code):
    client = PiClient(DEVICE, transport=httpx.MockTransport(lambda _: response))
    with pytest.raises(RiderError, match=code):
        await client.json("/state")
    await client.close()


async def test_binary_transport_rejects_non_image():
    client = PiClient(
        DEVICE,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, content=b"html", headers={"Content-Type": "text/html"}
            )
        ),
    )
    with pytest.raises(RiderError, match="invalid_image_type"):
        await client.image("/camera/raw")
    await client.close()


def test_demo_only_configuration_cannot_select_real_mode(monkeypatch):
    monkeypatch.setenv("RIDER_DEMO_ONLY", "true")
    raw = json.dumps(
        [{"id": "real", "label": "Real", "url": "http://localhost", "mode": "device"}]
    )
    with pytest.raises(RiderError, match="invalid_device_configuration"):
        configured_devices(raw)


@pytest.mark.parametrize(
    "url", ["http://[", "http://localhost:noport", "http://localhost:0"]
)
def test_malformed_ports_are_configuration_errors(url):
    with pytest.raises(RiderError, match="invalid_device_configuration"):
        configured_devices(json.dumps([{"id": "a", "label": "A", "url": url}]))


def test_all_readonly_and_mutation_routes_use_the_same_fixture(monkeypatch):
    monkeypatch.delenv("RIDER_DEFAULT_DEVICE", raising=False)
    pi = DemoPi()
    service, _ = domain(pi)
    with TestClient(host_app(service)) as client:
        initial = client.get("/api/v1/rider/config").json()
        config = client.post(
            "/api/v1/rider/config",
            json={"device_id": "demo"},
            headers={"X-Rider-Session": initial["session"]},
        ).json()
        h = {"X-Rider-Session": config["session"]}
        for section in ("overview", "control", "calibration", "resources", "face"):
            result = client.get("/api/v1/rider/status?section=" + section)
            assert result.status_code == 200
            assert result.json()["connection"]["status"] == "online"
        assert client.get("/api/v1/rider/status?section=bad").status_code == 400
        assert client.get("/api/v1/rider/diagnostics").status_code == 200
        assert client.get("/api/v1/rider/logs/rider-api.service").status_code == 200
        assert client.get("/api/v1/rider/face/library").status_code == 200
        assert client.get("/api/v1/rider/face/runtime").status_code == 200
        spec = copy.deepcopy(pi.library["gestures"][2])
        spec.pop("id")
        spec.pop("builtin")
        saved = client.post("/api/v1/rider/face/library", json=spec, headers=h)
        assert saved.status_code == 200
        key = saved.json()["data"]["id"]
        assert (
            client.delete("/api/v1/rider/face/library/" + key, headers=h).status_code
            == 200
        )
        settings = client.get("/api/v1/rider/face/runtime").json()
        assert (
            client.post(
                "/api/v1/rider/face/runtime",
                json={"if_revision": settings["settings_revision"], "tempo": 1.1},
                headers=h,
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/v1/rider/face/event",
                json={"type": "speech", "active": True},
                headers=h,
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/v1/rider/resource/lcd", json={"action": "on"}, headers=h
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/v1/rider/resource/lcd", json={"action": "off"}, headers=h
            ).status_code
            == 200
        )
        assert not pi.lcd_on
        assert (
            client.post(
                "/api/v1/rider/resource/unknown", json={"action": "stop"}, headers=h
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/v1/rider/goal/s2_camera_preview",
                json={"enabled": True},
                headers=h,
            ).status_code
            == 200
        )
        assert pi.camera_on
        assert (
            client.get("/api/v1/rider/camera/raw").headers["content-type"]
            == "image/png"
        )
        assert (
            client.post(
                "/api/v1/rider/goal/s2_camera_preview",
                json={"enabled": False},
                headers=h,
            ).status_code
            == 200
        )
        assert not pi.camera_on
        assert (
            client.post(
                "/api/v1/rider/goal/unknown", json={"enabled": True}, headers=h
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/v1/rider/control",
                content="{broken",
                headers={**h, "Content-Type": "application/json"},
            ).status_code
            == 422
        )
        pi.state = "ESTOP"
        response = client.post(
            "/api/v1/rider/control",
            json=command(
                pi,
                "clear_estop",
                operator_consent=True,
                operator_plan="Clear fixture latch",
                owner="manual",
                arming_mode="operator",
            ),
            headers=h,
        )
        assert response.status_code == 200 and pi.state == "DISARMED"


def test_access_token_and_environment_guard(monkeypatch):
    monkeypatch.setenv("RIDER_ACCESS_TOKEN", "fixture-token")
    service, _ = domain()
    with TestClient(host_app(service)) as client:
        assert client.get("/api/v1/rider/config").status_code == 403
        assert (
            client.get(
                "/api/v1/rider/config",
                headers={"Authorization": "Bearer fixture-token"},
            ).status_code
            == 200
        )


async def test_pending_ack_is_preserved_as_pending_not_physical_success():
    pi = DemoPi()

    async def reply(request):
        if request.method == "GET":
            return httpx.Response(
                200, json=json.loads(envelope({"safety": pi.safety()}).body)
            )
        return httpx.Response(
            202,
            json=json.loads(
                envelope(
                    {
                        "pending": True,
                        "confirmed": False,
                        "physical_completion": "unverified",
                    },
                    status=202,
                    operation={
                        "status": "pending",
                        "confirmed": False,
                        "physical_completion": "unverified",
                    },
                ).body
            ),
        )

    service = RiderService(
        (DEVICE,),
        client_factory=lambda device: PiClient(
            device, transport=httpx.MockTransport(reply)
        ),
    )
    await service.select("demo", service.session)
    result = await arm(service, pi)
    assert result["operation"]["status"] == "pending"
    assert result["data"]["confirmed"] is False
    await service.close()


async def test_moving_goal_requires_matching_operator_owner_and_fresh_intent():
    pi = DemoPi()
    asgi = httpx.ASGITransport(app=create_app(pi))
    forwarded = []
    navigation_owner = False

    async def respond(request):
        if request.url.path == "/api/logic/feature/fixture_move":
            forwarded.append(json.loads(request.content))
            return httpx.Response(
                200, content=envelope({"enabled": True}, "rider.tasks", 2).body
            )
        response = await asgi.handle_async_request(request)
        await response.aread()
        if request.url.path == "/api/logic/catalog":
            value = response.json()
            value["data"]["features"].append(
                {
                    "name": "fixture_move",
                    "can_move": True,
                    "control_owner": "navigation",
                }
            )
            return httpx.Response(200, json=value)
        if navigation_owner and request.url.path == "/api/control/state":
            value = response.json()
            value["data"]["safety"]["owner"] = "navigation"
            return httpx.Response(200, json=value)
        return response

    service = RiderService(
        (DEVICE,),
        client_factory=lambda device: PiClient(
            device, transport=httpx.MockTransport(respond)
        ),
    )
    await service.select("demo", service.session)
    with pytest.raises(RiderError):
        await service.goal("fixture_move", {"enabled": True}, service.session)
    await arm(service, pi)
    with pytest.raises(RiderError, match="operator_motion_permission_required"):
        await service.goal(
            "fixture_move",
            command(pi, "arm", enabled=True, source="navigation"),
            service.session,
        )
    assert not forwarded
    navigation_owner = True
    service.cache.clear()
    body = command(pi, "arm", enabled=True, source="navigation")
    await service.goal("fixture_move", body, service.session)
    assert forwarded == [body]
    await service.close()


def test_readonly_environment_allows_stop_but_no_state_changes(monkeypatch):
    import venom_module_rider.api.routes as routes

    def denied(**_):
        raise PermissionError("fixture read-only")

    monkeypatch.setattr(routes, "ensure_module_mutation_allowed", denied)
    service, pi = domain()
    with TestClient(host_app(service)) as client:
        # Select before changing policy through the domain, not the blocked API.
        asyncio.run(service.select("demo", service.session))
        h = {"X-Rider-Session": service.session}
        assert (
            client.post(
                "/api/v1/rider/resource/lcd", json={"action": "on"}, headers=h
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/rider/control", json={"action": "stop"}, headers=h
            ).status_code
            == 200
        )
        assert not pi.lcd_on and pi.state == "DISARMED"
        assert (
            client.post("/api/v1/rider/face/library", json=[], headers=h).status_code
            == 400
        )


async def test_camera_unknown_does_not_request_a_frame():
    async def respond(request):
        return httpx.Response(
            200, content=envelope({"activity": {}}, "rider.resources", 1).body
        )

    transport = httpx.MockTransport(respond)
    service = RiderService(
        (DEVICE,), client_factory=lambda device: PiClient(device, transport=transport)
    )
    await service.select("demo", service.session)
    with pytest.raises(RiderError, match="camera_unknown"):
        await service.camera("raw")
    await service.close()


async def test_every_view_has_pi_settings_needed_for_global_arm():
    service, pi = domain()
    await service.select("demo", service.session)
    for section in ("overview", "control", "calibration", "resources", "face"):
        status = await service.status(section)
        assert status["settings"]["control_modes"]["version"] == 1
    assert sum(row["path"] == "/api/calibration/controls" for row in pi.trace) == 1
    await service.close()


@pytest.mark.asyncio
async def test_navigation_snapshot_skips_diagnostics_and_refreshes_settings():
    service, pi = domain()
    await service.select("demo", service.session)
    original = service.client.json
    paths = []

    async def read(path, **kwargs):
        paths.append(path)
        assert path in {"/api/control/state", "/api/calibration/controls"}
        return await original(path, **kwargs)

    service.client.json = read
    try:
        first = await service.status("navigation")
        pi.controls["revision"] += 1
        second = await service.status("navigation")
        assert first["connection"]["status"] == "online"
        assert second["settings"]["revision"] == pi.controls["revision"]
        assert second["telemetry"] is None and second["history"] is None
        assert paths.count("/api/control/state") == 2
        assert paths.count("/api/calibration/controls") == 2
    finally:
        await service.close()
