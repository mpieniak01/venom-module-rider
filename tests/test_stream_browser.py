"""Native browser consumers share one source and stop independently."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_pipeline_api import PROFILE, Detector, PiFixture

from venom_module_rider.api.pipeline_routes import browser_stream
from venom_module_rider.config import Device
from venom_module_rider.errors import RiderError
from venom_module_rider.service import RiderService
from venom_module_rider.stream_framing import MultipartJpegParser
from venom_module_rider.stream_hub import NativeStreamHub
from venom_module_rider.vision_loop import VisionLoop


class Browser:
    def __init__(self, state):
        self.app = SimpleNamespace(state=state)
        self.disconnected = False

    async def is_disconnected(self):
        return self.disconnected


async def setup():
    device = Device("pi", "Fixture", "http://127.0.0.1:18880", "device")
    pi = PiFixture(device)
    domain = RiderService((device,), client_factory=lambda _device: pi)
    await domain.select("pi", domain.session)
    hub, loop = NativeStreamHub(), VisionLoop(Detector())
    domain.media_hub = hub
    state = SimpleNamespace(rider=domain, rider_stream=hub, rider_vision=loop)
    return pi, domain, hub, loop, state


def unpack(part):
    return MultipartJpegParser(b"rider").feed(part)[0]


async def test_two_browser_raw_readers_share_one_connection_and_close_independently():
    pi, domain, hub, loop, state = await setup()
    first, second = Browser(state), Browser(state)
    a = (await browser_stream("raw", first)).body_iterator
    b = (await browser_stream("raw", second)).body_iterator
    left, right = await asyncio.gather(anext(a), anext(b))
    headers, payload = unpack(left)
    other_headers, other_payload = unpack(right)
    assert payload == other_payload
    assert headers["x-rider-frame-id"] == other_headers["x-rider-frame-id"]
    assert headers["x-rider-source"] == "pi-camera"
    assert headers["x-rider-frame-id"] == "session-1"
    assert json.loads(headers["x-rider-source-json"])["camera_session"] == "session"
    assert loop.detector.calls == 0 and pi.opened == 1
    await a.aclose()
    assert hub.connection.readers == 1 and pi.ended == 0
    await pi.emit(2)
    next_headers, _payload = unpack(await anext(b))
    assert next_headers["x-rider-frame-id"] == "session-2"
    assert payload and pi.snapshot_requests == 0
    await b.aclose()
    assert hub.connection is None and pi.ended == 1
    await hub.close()
    await loop.close()
    await domain.close()


async def test_two_processed_browsers_never_double_infer_the_same_source():
    pi, domain, hub, loop, state = await setup()
    a = (await browser_stream("venom", Browser(state))).body_iterator
    b = (await browser_stream("venom", Browser(state))).body_iterator
    first, second = await asyncio.gather(anext(a), anext(b))
    a_headers, _ = unpack(first)
    b_headers, _ = unpack(second)
    assert a_headers["x-rider-frame-id"] == b_headers["x-rider-frame-id"] == "session-1"
    assert loop.detector.calls == 1
    analysis = json.loads(a_headers["x-rider-analysis-json"])
    assert analysis["source"]["frame_id"] == "session-1"
    assert a_headers["x-rider-vision-action"] == "stop"
    await a.aclose()
    await pi.emit(2)
    next_headers, _ = unpack(await anext(b))
    assert next_headers["x-rider-frame-id"] == "session-2"
    assert loop.detector.calls == 2
    await b.aclose()
    await hub.close()
    await loop.close()
    await domain.close()


async def test_stop_profile_invalidates_existing_browser_and_no_model_fallback():
    pi, domain, hub, loop, state = await setup()
    browser = Browser(state)
    output = (await browser_stream("venom", browser)).body_iterator
    await anext(output)
    assert loop.detector.calls == 1
    await domain.configure_pipeline(
        {
            "contract_version": 1,
            "request_id": "stop",
            "expected_revision": 3,
            "action": "stop",
        },
        domain.session,
    )
    with pytest.raises(StopAsyncIteration):
        await anext(output)
    assert hub.connection is None
    assert not loop.detector.calls > 1
    assert pi.snapshot_requests == 0
    await hub.close()
    await loop.close()
    await domain.close()


async def test_local_profile_allows_raw_and_rejects_venom_before_model_or_reader():
    pi, domain, hub, loop, state = await setup()
    pi.pipeline["active_profile"] = {**PROFILE, "analysis_location": "local"}
    with pytest.raises(RiderError, match="vision_remote_disabled"):
        await browser_stream("venom", Browser(state))
    assert pi.opened == 0 and loop.detector.calls == 0
    response = await browser_stream("raw", Browser(state))
    iterator = response.body_iterator
    await anext(iterator)
    await iterator.aclose()
    assert pi.opened == 1 and loop.detector.calls == 0
    await hub.close()
    await loop.close()
    await domain.close()


async def test_page_disconnect_retires_last_reader_without_stopping_robot_services():
    pi, domain, hub, loop, state = await setup()
    browser = Browser(state)
    iterator = (await browser_stream("raw", browser)).body_iterator
    await anext(iterator)
    browser.disconnected = True
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)
    assert pi.ended == 1 and hub.connection is None
    assert not any(method != "GET" for method, _path, _body in pi.calls)
    await hub.close()
    await loop.close()
    await domain.close()


async def test_native_raw_photo_saves_exact_displayed_bytes_via_existing_api(tmp_path):
    import hashlib
    import io
    import time

    import httpx
    from fastapi import FastAPI
    from PIL import Image
    from test_samples_api import outcome
    from test_stream_contract import source

    from venom_module_rider.api.routes import router
    from venom_module_rider.samples_registry import SamplesRegistry
    from venom_module_rider.stream_contract import NativeFrame, SourceDescription

    pi, domain, hub, loop, state = await setup()
    registry = SamplesRegistry(tmp_path / "samples")
    registry.device_modes = {"pi": "device"}
    domain.samples = loop.samples = registry
    app = FastAPI()
    app.state.rider = domain
    app.state.rider_samples = registry
    app.state.rider_vision = loop
    app.state.rider_stream = hub
    app.include_router(router)
    browser = Browser(app.state)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )
    iterator = None
    try:
        collection = await client.post(
            "/api/v1/rider/samples/sessions",
            json={"request_id": "native-start", "source_device_id": "pi"},
        )
        assert collection.status_code == 201
        session = collection.json()["session_id"]
        iterator = (await browser_stream("raw", browser)).body_iterator
        first_headers, displayed = unpack(await anext(iterator))
        ticket = first_headers["x-rider-capture-key"]
        image = io.BytesIO()
        Image.new("RGB", (96, 64), "black").save(image, format="JPEG")
        different = image.getvalue()
        await pi.frames.put(
            NativeFrame(
                SourceDescription.model_validate(source(different, sequence=2)),
                different,
                time.monotonic(),
            )
        )
        _next_headers, next_displayed = unpack(await anext(iterator))
        assert next_displayed != displayed
        saved_request = await client.post(
            "/api/v1/rider/samples/capture",
            json={
                "session_id": session,
                "capture_request_id": "native-photo",
                "frame_key": ticket,
            },
        )
        assert saved_request.status_code == 202, saved_request.text
        saved = await outcome(client, "native-photo")
        assert saved["state"] == "saved"
        item = (
            await client.get("/api/v1/rider/samples/items/" + saved["sample_id"])
        ).json()
        original = await client.get(
            "/api/v1/rider/samples/items/" + saved["sample_id"] + "/original"
        )
        assert original.content == displayed
        assert item["raw"]["sha256"] == hashlib.sha256(displayed).hexdigest()
        context = item["source_context"]
        assert context["frame_id"] == "session-1"
        assert context["source_identity"]["frame_id"] == "session-1"
        assert context["transform"]["applied_on"] == "venom"
        assert context["timestamp_source"] == "pi-capture-monotonic-ns"
        assert loop.detector.calls == 0 and pi.snapshot_requests == 0
        assert not any(method == "POST" for method, _path, _body in pi.calls)
    finally:
        if iterator is not None:
            await iterator.aclose()
        await client.aclose()
        await hub.close()
        await loop.close()
        await registry.close()
        await domain.close()
