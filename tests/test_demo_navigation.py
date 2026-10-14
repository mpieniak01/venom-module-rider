import asyncio
import io
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from venom_module_rider.api.routes import router
from venom_module_rider.config import Device
from venom_module_rider.demo import DemoPi, create_app
from venom_module_rider.demo_navigation import ReplayNavigation, load_pi_lease
from venom_module_rider.service import RiderService
from venom_module_rider.vision_loop import VisionLoop


class PermitFixture:
    def __init__(self, config, run, drive, stop, clock):
        self.latched, self.reason = False, "waiting"
        self.stop = stop

    def halt(self, reason):
        self.latched, self.reason = True, reason
        self.stop()

    def tick(self):
        return not self.latched

    def accept(self, *args):
        return False


def jpeg():
    out = io.BytesIO()
    Image.new("RGB", (32, 32), "black").save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture
def replay():
    nav = ReplayNavigation(jpeg(), PermitFixture, model_sha="a" * 64)
    pi = DemoPi(navigation=nav)
    return pi, nav


async def test_replay_observation_has_no_arm_or_actuation_and_preserves_recording(
    replay,
):
    pi, nav = replay
    calls = []

    async def receive(meta, payload):
        calls.append((meta, payload))
        return {"state": "unknown", "reason": "vision_image_quality", "action": "stop"}

    nav.request = receive
    await nav.start("s15_venom_perception")
    await asyncio.sleep(0.01)
    assert calls[0][1] == nav.payload
    assert calls[0][0]["mode"] == "observe"
    assert pi.state == "DISARMED" and not pi.safety()["moving"]
    assert nav.status["decision"]["state"] == "unknown"
    nav.recorded_at = 100.5
    image = nav.image()
    assert image.body == nav.payload and image.headers["x-rider-source"] == "replay"
    assert image.headers["x-rider-recorded-at"] == "100.5"
    assert all(row["action"] != "go" for row in nav.events)
    await nav.close()


async def test_replay_configuration_revision_bounds_and_active_guard(replay):
    _, nav = replay
    for body in [
        {"revision": 2},
        {"revision": 0, "values": {"unknown": 1}},
        {"revision": 0, "values": {"speed": 1}},
    ]:
        with pytest.raises(ValueError):
            nav.configure(body)
    assert nav.configure({"revision": 0, "values": {"speed": 0.05}})["revision"] == 1
    await nav.start("s15_venom_perception")
    with pytest.raises(ValueError, match="stop_goals"):
        nav.configure({"revision": 1})
    await nav.close()


async def test_replay_drive_requires_authority_and_model_binding(replay):
    pi, nav = replay
    with pytest.raises(ValueError, match="authority"):
        await nav.start("s14_venom_navigation")
    pi.state, pi.owner = "ARMED", "navigation"
    with pytest.raises(ValueError, match="calibration"):
        await nav.start("s14_venom_navigation")
    nav.configure(
        {
            "revision": 0,
            "values": {
                "mode": "drive",
                "verified": True,
                "calibration_id": "simulation",
                "model_sha256": "a" * 64,
            },
        }
    )
    nav.request = lambda *_: asyncio.sleep(
        0, result={"state": "clear", "action": "stop"}
    )
    await nav.start("s14_venom_navigation")
    await asyncio.sleep(0.01)
    nav.motion_generation = pi.generation
    nav.drive(0.1, 0.15)
    assert pi.safety()["moving"]
    pi.owner, pi.generation = "manual", "new-manual"
    pi.deadline = nav.clock() + 1
    nav.brake()
    assert pi.safety()["moving"]
    with pytest.raises(ValueError, match="authority"):
        nav.drive(0.1, 0.15)
    await nav.close()


async def test_simulator_http_routes_and_stop_keep_independent_camera(replay):
    pi, nav = replay
    nav.request = lambda *_: asyncio.sleep(
        0, result={"state": "unknown", "action": "stop"}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(pi)), base_url="http://fixture"
    ) as client:
        assert (await client.get("/api/navigation/config")).json()["data"][
            "mode"
        ] == "observe"
        assert (await client.get("/api/navigation/status")).json()["data"][
            "running"
        ] is False
        assert (
            await client.post("/api/navigation/config", json={"revision": 12})
        ).status_code == 409
        assert (
            await client.post(
                "/api/logic/feature/s14_venom_navigation", json={"enabled": True}
            )
        ).status_code == 409
        rows = (await client.get("/api/logic/catalog")).json()["data"]["features"]
        assert any(r["name"] == "s14_venom_navigation" and r["can_move"] for r in rows)
        await client.post(
            "/api/logic/feature/s15_venom_perception", json={"enabled": True}
        )
        assert (await client.get("/camera/raw")).content == nav.payload
        await client.post(
            "/api/logic/feature/s2_camera_preview", json={"enabled": True}
        )
        await client.post(
            "/api/logic/feature/s15_venom_perception", json={"enabled": False}
        )
        assert pi.camera_on and nav.status["running"] is False
    await nav.close()


async def test_transport_serializes_correlated_frame_and_is_loopback_only(replay):
    _, nav = replay
    with pytest.raises(ValueError, match="loopback"):
        ReplayNavigation(b"x", PermitFixture, api_url="http://example.invalid")
    seen = []
    nav.api_url = "http://127.0.0.1:8123"

    def response(request):
        seen.append(request)
        return httpx.Response(200, json={"action": "stop"})

    await nav.http.aclose()
    nav.http = httpx.AsyncClient(transport=httpx.MockTransport(response))
    assert await nav._request({"sequence": 2}, b"jpg") == {"action": "stop"}
    assert json.loads(seen[0].headers["X-Rider-Vision"]) == {"sequence": 2}
    assert seen[0].url.port == 8123
    assert seen[0].content == b"jpg"
    assert seen[0].headers["X-Rider-Device"] == "demo"
    await nav.close()


async def test_transport_failure_and_watchdog_latch_without_restart(replay):
    pi, nav = replay
    nav.drop_link = True
    await nav.start("s15_venom_perception")
    await asyncio.sleep(0.32)
    assert nav.status["running"] is False and nav.status["latched"]
    nav.drop_link = False
    await asyncio.sleep(0.02)
    assert not nav.status["running"] and not pi.safety()["moving"]
    nav.status["running"] = True
    nav.lease.latched = True
    await nav.watch()
    assert nav.status["running"] is False
    await nav.close()


def test_lease_loader_explicit_source_and_missing_module(tmp_path):
    source = tmp_path / "permit.py"
    source.write_text("class DecisionLease: pass")
    assert load_pi_lease(source).__name__ == "DecisionLease"
    with pytest.raises(ValueError):
        load_pi_lease(tmp_path / "missing.no-python")


async def test_timed_navigation_arm_and_manual_takeover_are_fixture_only(replay):
    pi, nav = replay

    def arm(owner):
        return pi.control(
            {
                "action": "arm",
                "owner": owner,
                "generation": pi.generation,
                "seq": pi.seq + 1,
                "ts": __import__("time").time(),
                "ttl_ms": 500,
                "operator_consent": True,
                "operator_plan": "simulation",
                "lease_s": 0.02,
            }
        )

    old = pi.generation
    assert arm("navigation").status_code == 200 and pi.owner == "navigation"
    assert pi.generation != old
    await asyncio.sleep(0.03)
    assert pi.safety()["state"] == "DISARMED"
    assert arm("manual").status_code == 200
    assert pi.safety()["panel_motion_enabled"] is True
    await nav.close()


@pytest.mark.parametrize("engine", ["segformer", "depth_plane"])
async def test_full_authenticated_loop_actual_pi_permit_go_obstacle_timeout_and_manual(
    monkeypatch,
    engine,
):
    source = os.getenv("RIDER_TEST_PI_LEASE")
    if not source:
        pytest.skip(
            "Explicit Pi lease source required for cross-repository integration"
        )

    class Detector:
        state = "clear"

        def status(self):
            return {"sha256": "a" * 64}

        def detect(self, *args, **kwargs):
            return {
                "state": self.state,
                "reason": "floor_candidate" if self.state == "clear" else "obstacle",
                "floor_fraction": 1.0,
            }

    detector = Detector()
    loop = VisionLoop(detector)
    loop.depth_detector = detector
    monkeypatch.setenv("RIDER_COMPUTE_TOKEN", "replay-fixture-token")
    app = FastAPI()
    app.state.rider = RiderService(
        (Device("demo", "Replay", "http://127.0.0.1:18880", "demo"),)
    )
    app.state.rider_vision = loop
    app.include_router(router)
    nav = ReplayNavigation(
        jpeg(),
        load_pi_lease(Path(source)),
        model_sha="a" * 64,
        token="replay-fixture-token",
    )
    pi = DemoPi(navigation=nav)
    await nav.http.aclose()
    nav.http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8800"
    )
    nav.policy.update(
        mode="drive",
        verified=True,
        calibration_id="simulation-only",
        model_sha256="a" * 64,
        perception={**nav.policy["perception"], "engine": engine},
    )
    pi.state, pi.owner = "ARMED", "navigation"

    def grant_navigation():
        import time

        reply = pi.control(
            {
                "action": "arm",
                "owner": "navigation",
                "generation": pi.generation,
                "seq": pi.seq + 1,
                "ts": time.time(),
                "ttl_ms": 500,
                "operator_consent": True,
                "operator_plan": "simulation-only",
                "lease_s": 10,
            }
        )
        assert reply.status_code == 200

    async def until(predicate):
        async with asyncio.timeout(1):
            while not predicate():
                await asyncio.sleep(0.005)

    try:
        grant_navigation()
        await nav.start("s14_venom_navigation")
        await until(lambda: any(e["action"] == "go" for e in nav.events))
        assert nav.status["decision"]["clear_count"] >= 3 and pi.safety()["moving"]
        detector.state = "obstacle"
        await until(lambda: nav.status.get("latched"))
        assert not pi.safety()["moving"]
        assert pi.safety()["state"] == "DISARMED"
        assert nav.status["decision"]["action"] == "stop"
        detector.state = "clear"
        await asyncio.sleep(0.05)
        assert not nav.status["running"]
        grant_navigation()
        await nav.start("s14_venom_navigation")
        await until(lambda: nav.status.get("simulated_moving"))
        nav.drop_link = True
        await until(lambda: nav.status.get("latched"))
        assert not pi.safety()["moving"]
        nav.drop_link = False
        await asyncio.sleep(0.05)
        assert not nav.status["running"]
        grant_navigation()
        await nav.start("s14_venom_navigation")
        await until(lambda: nav.status.get("simulated_moving"))
        old = nav.motion_generation
        pi.control(
            {
                "action": "arm",
                "owner": "manual",
                "generation": pi.generation,
                "seq": pi.seq + 1,
                "ts": __import__("time").time(),
                "ttl_ms": 500,
                "operator_consent": True,
                "operator_plan": "fixture-manual",
            }
        )
        assert (
            pi.owner == "manual" and pi.generation != old and not nav.status["running"]
        )
        with pytest.raises(ValueError, match="authority"):
            nav.drive(0.1, 0.1)
    finally:
        await nav.close()
        await loop.close()


@pytest.mark.parametrize("transport_timeout", [False, True])
async def test_observation_retries_busy_model_without_arming(replay, transport_timeout):
    pi, nav = replay
    original = nav.request
    calls = [0]

    async def request(meta, payload):
        calls[0] += 1
        if calls[0] == 1:
            if transport_timeout:
                raise httpx.ReadTimeout("fixture cold start")
            req = httpx.Request("POST", "http://127.0.0.1/fixture")
            response = httpx.Response(429, request=req)
            raise httpx.HTTPStatusError("busy", request=req, response=response)
        return await original(meta, payload)

    nav.request = request
    await nav.start("s15_venom_perception")
    async with asyncio.timeout(1):
        while calls[0] < 2:
            await asyncio.sleep(0.01)
    assert nav.status["running"] is True
    assert pi.safety()["state"] == "DISARMED"
    assert not pi.safety()["moving"]
    await nav.close()
