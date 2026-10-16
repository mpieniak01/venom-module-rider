"""Real API/domain + durable storage, with only loopback Pi fixtures."""

import asyncio
import hashlib
import io
import threading
import time

import httpx
from fastapi.testclient import TestClient
from PIL import Image, ImageOps
from test_rider import command, domain, host_app

from venom_module_rider.samples_registry import SamplesRegistry
from venom_module_rider.vision_loop import VisionLoop

BASE = "/api/v1/rider"


def jpeg(colour):
    stream = io.BytesIO()
    Image.new("RGB", (64, 48), colour).save(stream, "JPEG")
    return stream.getvalue()


async def rig(tmp_path):
    service, pi = domain()
    await service.select("demo", service.session)
    registry = SamplesRegistry(tmp_path / "samples")
    registry.device_modes = {"demo": "demo"}
    service.samples = registry
    app = host_app(service)
    app.state.rider_samples = registry
    app.state.rider_vision = VisionLoop(
        samples=registry, source_session=lambda: service.session
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )
    return service, pi, registry, app, client


async def outcome(client, request_id):
    for _ in range(200):
        response = await client.get(BASE + "/samples/requests/" + request_id)
        result = response.json()
        if result.get("state") in {"saved", "failed"}:
            return result
        await asyncio.sleep(0.005)
    raise AssertionError("capture outcome missing")


async def test_manual_capture_and_offline_browse_never_need_inference_or_arm(tmp_path):
    service, pi, registry, app, client = await rig(tmp_path)
    try:
        headers = {"X-Rider-Session": service.session}
        created = await client.post(
            BASE + "/samples/sessions",
            json={"request_id": "start", "source_device_id": "demo"},
        )
        assert created.status_code == 201
        sid = created.json()["session_id"]
        assert pi.state == "DISARMED" and not pi.camera_on
        # The existing explicit preview goal owns the producer, not the collector.
        camera_on = await client.post(
            BASE + "/goal/s2_camera_preview", json={"enabled": True}, headers=headers
        )
        assert camera_on.status_code == 200
        raw = await client.get(BASE + "/camera/raw")
        assert raw.status_code == 200, raw.text[:100]
        key = raw.headers["x-rider-capture-key"]
        posts_before = [row for row in pi.trace if row["method"] == "POST"]
        response = await client.post(
            BASE + "/samples/capture",
            json={"session_id": sid, "capture_request_id": "capture", "frame_key": key},
        )
        assert response.status_code == 202
        saved = await outcome(client, "capture")
        assert saved["state"] == "saved"
        item = await client.get(BASE + "/samples/items/" + saved["sample_id"])
        assert item.json()["raw"]["sha256"] == hashlib.sha256(raw.content).hexdigest()
        assert item.json()["prediction"] is None
        assert item.json()["source_context"]["device_mode"] == "demo"
        await client.post(BASE + "/samples/sessions/" + sid + "/end", json={})
        assert [row for row in pi.trace if row["method"] == "POST"] == posts_before
        # Disconnect Pi at the transport, then browse and annotate the archive.
        pi.offline = True
        trace_before = len(pi.trace)
        for path in [
            "/samples/sessions",
            "/samples/sessions/" + sid,
            "/samples/items/" + saved["sample_id"],
            "/samples/items/" + saved["sample_id"] + "/original",
        ]:
            assert (await client.get(BASE + path)).status_code == 200
        labelled = await client.post(
            BASE + "/samples/items/" + saved["sample_id"] + "/labels",
            json={
                "expected_revision": 0,
                "values": {"scene": "dim room", "review": "pending"},
            },
        )
        assert labelled.status_code == 200
        exported = await client.get(BASE + "/samples/sessions/" + sid + "/export")
        assert exported.status_code == 200
        assert exported.headers["content-type"] == "application/zip"
        assert len(pi.trace) == trace_before
        assert pi.state == "DISARMED"
    finally:
        await client.aclose()
        await app.state.rider_vision.close()
        await registry.close()
        await service.close()


async def test_overlay_captures_its_original_not_the_next_pi_frame(
    tmp_path, monkeypatch
):
    service, pi, registry, app, client = await rig(tmp_path)
    source_payloads = []

    async def camera(path):
        colour = ["red", "blue", "yellow"][len(source_payloads) % 3]
        payload = jpeg(colour)
        source_payloads.append(payload)
        return (
            payload,
            "image/jpeg",
            {
                "x-rider-frame-id": "source-" + str(len(source_payloads)),
                "x-rider-stale": "false",
            },
        )

    class Detector:
        def status(self):
            return {"sha256": "a" * 64}

        def detect(self, payload, corridor):
            stream = io.BytesIO()
            with Image.open(io.BytesIO(payload)) as image:
                ImageOps.invert(image.convert("RGB")).save(stream, "JPEG")
            return {
                "state": "clear",
                "reason": "fixture",
                "floor_fraction": 1.0,
                "_preview": stream.getvalue(),
            }

    monkeypatch.setattr(service.client, "image", camera)
    app.state.rider_vision.detector = Detector()
    pi.camera_on = True
    try:
        started = await client.post(
            BASE + "/samples/sessions",
            json={"request_id": "start", "source_device_id": "demo"},
        )
        sid = started.json()["session_id"]
        processed = await client.get(BASE + "/camera/venom")
        assert processed.status_code == 200, processed.text[:150]
        exact_original = source_payloads[-1]
        assert processed.content != exact_original
        key = processed.headers["x-rider-capture-key"]
        later = await client.get(BASE + "/camera/raw")
        assert later.content != exact_original
        result = await client.post(
            BASE + "/samples/capture",
            json={"session_id": sid, "capture_request_id": "overlay", "frame_key": key},
        )
        assert result.status_code == 202
        saved = await outcome(client, "overlay")
        assert saved["state"] == "saved"
        actual = await client.get(
            BASE + "/samples/items/" + saved["sample_id"] + "/original"
        )
        assert actual.content == exact_original
        metadata = registry.store.sample(saved["sample_id"])
        assert metadata["prediction"]["model_sha256"] == "a" * 64
        assert metadata["frame_id"] == "source-1"
    finally:
        await client.aclose()
        await app.state.rider_vision.close()
        await registry.close()
        await service.close()


async def test_slow_full_writer_does_not_block_real_control_stop_path(
    tmp_path, monkeypatch
):
    service, pi, registry, app, client = await rig(tmp_path)
    event, entered = threading.Event(), threading.Event()
    original = registry.store.capture

    def slow(*args, **kwargs):
        entered.set()
        event.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(registry.store, "capture", slow)
    pi.camera_on = True
    headers = {"X-Rider-Session": service.session}
    try:
        armed = await client.post(
            BASE + "/control",
            headers=headers,
            json=command(
                pi,
                "arm",
                owner="manual",
                arming_mode="operator",
                operator_consent=True,
                operator_plan="Fixture only",
                panel_id="test-panel",
            ),
        )
        assert armed.status_code == 200
        hold = command(
            pi,
            "move",
            direction="forward",
            mode="hold",
            device_settings=True,
            panel_id="test-panel",
        )
        assert (
            await client.post(BASE + "/control", headers=headers, json=hold)
        ).status_code == 200
        deadline = pi.deadline
        sid = (
            await client.post(
                BASE + "/samples/sessions",
                json={"request_id": "start", "source_device_id": "demo"},
            )
        ).json()["session_id"]
        raw = await client.get(BASE + "/camera/raw")
        key = raw.headers["x-rider-capture-key"]
        response = await client.post(
            BASE + "/samples/capture",
            json={
                "session_id": sid,
                "capture_request_id": "blocked-write",
                "frame_key": key,
            },
        )
        assert response.status_code == 202
        while not entered.is_set():
            await asyncio.sleep(0.001)
        assert pi.deadline == deadline  # capture neither renews nor cancels motion
        started = time.monotonic()
        stopped = await asyncio.wait_for(
            client.post(BASE + "/control", json={"action": "stop"}), 0.3
        )
        assert time.monotonic() - started < 0.3 and stopped.status_code == 200
        assert pi.state == "DISARMED" and pi.deadline == 0
        event.set()
        assert (await outcome(client, "blocked-write"))["state"] == "saved"
    finally:
        event.set()
        await client.aclose()
        await app.state.rider_vision.close()
        await registry.close()
        await service.close()


async def test_storage_initialization_failure_keeps_operator_routes_available(
    monkeypatch,
):
    service, pi = domain()
    await service.select("demo", service.session)
    monkeypatch.setenv("RIDER_SAMPLE_ROOT", "relative-invalid")
    with TestClient(host_app(service)) as client:
        assert client.get(BASE + "/samples/status").json()["available"] is False
        assert (
            client.post(BASE + "/control", json={"action": "stop"}).status_code == 200
        )
        assert client.get(BASE + "/config").status_code == 200
        assert client.get(BASE + "/samples/sessions").status_code == 503
    assert pi.state == "DISARMED"
