"""Pi media transport is bounded and independent of JSON/STOP connection slots."""

import json

import httpx
import pytest
from test_stream_contract import jpeg, source

from venom_module_rider.client import PiClient, expected_contract
from venom_module_rider.config import Device
from venom_module_rider.errors import RiderError

DEVICE = Device("pi", "Pi fixture", "http://127.0.0.1", "device")


def wire(payload=None, metadata=None):
    payload = jpeg() if payload is None else payload
    metadata = source(payload) if metadata is None else metadata
    return (
        b"--rider\r\nContent-Type: image/jpeg\r\nContent-Length: "
        + str(len(payload)).encode()
        + b"\r\nX-Rider-Source: "
        + json.dumps(metadata, separators=(",", ":")).encode()
        + b"\r\n\r\n"
        + payload
        + b"\r\n--rider--\r\n"
    )


async def test_pi_client_accepts_exact_source_without_starting_camera_or_redirects():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "multipart/x-mixed-replace; boundary=rider"},
            content=wire(),
        )

    client = PiClient(DEVICE, transport=httpx.MockTransport(handler))
    frames = [item async for item in client.mjpeg()]
    assert len(frames) == 1
    assert frames[0].payload == jpeg()
    assert frames[0].source.sha256 == source()["sha256"]
    assert requests[0].url.path == "/api/resource/camera/stream.mjpg"
    assert requests[0].method == "GET"
    assert client.http is not client.media_http
    assert client.stop_http is not client.media_http
    assert expected_contract("/api/resource/camera/pipeline") == (
        "rider.camera_pipeline",
        1,
    )
    await client.close()
    assert client.media_http.is_closed


@pytest.mark.parametrize(
    "status,headers,body,error",
    [
        (302, {"location": "/other"}, b"", "pi_redirect_refused"),
        (503, {}, b"unavailable", "stream_unavailable"),
        (200, {"content-type": "image/jpeg"}, b"jpeg", "stream_invalid_format"),
        (
            200,
            {"content-type": "multipart/x-mixed-replace; boundary=rider"},
            wire(metadata={}),
            "stream_invalid_source",
        ),
        (
            200,
            {"content-type": "multipart/x-mixed-replace; boundary=rider"},
            wire(metadata={**source(), "sha256": "b" * 64}),
            "stream_hash_mismatch",
        ),
    ],
)
async def test_bad_peer_metadata_or_transport_is_not_a_fresh_frame(
    status, headers, body, error
):
    client = PiClient(
        DEVICE,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, headers=headers, content=body)
        ),
    )
    with pytest.raises(RiderError, match=error):
        _frames = [frame async for frame in client.mjpeg()]
    await client.close()


async def test_read_timeout_is_reported_without_fallback_or_camera_mutation():
    calls = []

    def failing(request):
        calls.append(request)
        raise httpx.ReadTimeout("fixture", request=request)

    client = PiClient(DEVICE, transport=httpx.MockTransport(failing))
    with pytest.raises(RiderError, match="stream_unavailable"):
        _frames = [frame async for frame in client.mjpeg()]
    assert len(calls) == 1
    assert calls[0].method == "GET"
    await client.close()


async def test_legacy_ram_alias_keeps_capture_metadata_without_changing_source_json():
    metadata = source()
    headers = {
        "Content-Type": "image/jpeg",
        "X-Rider-Source": "pi-camera",
        "X-Rider-Source-Json": json.dumps(metadata, separators=(",", ":")),
        "X-Rider-Frame-Age-Ms": "20.4",
        "X-Rider-Frame-Age-Source": "capture",
        "X-Rider-Camera-Session": metadata["camera_session"],
        "X-Rider-Sequence": str(metadata["sequence"]),
        "X-Rider-Transport-Epoch": str(metadata["transport_epoch"]),
        "X-Rider-Pipeline-Revision": str(metadata["pipeline_revision"]),
        "X-Rider-Captured-Monotonic-Ns": str(metadata["captured_monotonic_ns"]),
        "Set-Cookie": "private=must-not-leak",
    }
    client = PiClient(
        DEVICE,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers=headers, content=jpeg())
        ),
    )
    try:
        payload, mime, propagated = await client.image("/vision/cam")
        assert payload == jpeg()
        assert mime == "image/jpeg"
        assert propagated["x-rider-source"] == "pi-camera"
        assert json.loads(propagated["x-rider-source-json"]) == metadata
        assert propagated["x-rider-frame-age-ms"] == "20.4"
        assert propagated["x-rider-captured-monotonic-ns"] == str(
            metadata["captured_monotonic_ns"]
        )
        assert "set-cookie" not in propagated
    finally:
        await client.close()


@pytest.mark.parametrize("first_failure", ["http_unreachable", "missing_anchor"])
async def test_bootstrap_failure_closes_persistent_http_source_before_reconnect(
    first_failure,
):
    import asyncio
    import time
    from types import SimpleNamespace

    from test_stream_contract import native

    from venom_module_rider.age_anchor import age_anchor
    from venom_module_rider.stream_hub import NativeStreamHub

    latest, writers, retained = [None], [], []
    released = asyncio.Event()
    connections = 0

    async def serve(reader, writer):
        nonlocal connections
        connections += 1
        number = connections
        writers.append(writer)
        try:
            await reader.readuntil(b"\r\n\r\n")
            frame = native(number)
            latest[0] = frame
            content = wire(frame.payload, frame.source.model_dump(mode="json"))
            content = content.removesuffix(b"--rider--\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: multipart/x-mixed-replace; boundary=rider\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
                + f"{len(content):x}\r\n".encode()
                + content
                + b"\r\n"
            )
            await writer.drain()
            await reader.read()
        finally:
            if number == 1:
                released.set()
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = PiClient(Device("pi", "HTTP pool fixture", f"http://127.0.0.1:{port}"))
    original = client.mjpeg

    def source():
        # Retaining a generator (e.g. an exception traceback/diagnostic owner)
        # must not make HTTP pool release depend on garbage collection.
        item = original()
        retained.append(item)
        return item

    client.mjpeg = source
    profile = {
        "contract_id": "rider.camera_pipeline",
        "contract_version": 1,
        "state": "active",
        "camera_session": "session",
        "transport_epoch": 2,
        "revision": 3,
    }
    bootstraps = 0

    async def pipeline(*, force=False):
        nonlocal bootstraps
        bootstraps += 1
        if bootstraps == 1:
            if first_failure == "http_unreachable":
                raise RiderError("pi_unreachable", 503)
            return dict(
                profile
            )  # The Pi status has not published its first source yet.
        status = {**profile, "latest_frame": latest[0].source.model_dump(mode="json")}
        status["_age_anchor"] = age_anchor(status, time.monotonic(), time.monotonic())
        return status

    domain = SimpleNamespace(
        session="module", transport=lambda: client, pipeline=pipeline
    )
    hub = NativeStreamHub()
    try:
        async with hub.lease(domain, profile) as connection:
            frame = await hub.frame(connection, timeout=1.2)
            assert frame.source.sequence == 2
            await asyncio.wait_for(released.wait(), 0.2)
            assert connections == 2
            assert connection.received == 1
    finally:
        await hub.close()
        for item in retained:
            await item.aclose()
        await client.close()
        server.close()
        await server.wait_closed()
        for writer in writers:
            writer.close()


async def test_cancel_during_response_close_cannot_poison_the_single_http_slot():
    import asyncio

    from test_stream_contract import native

    entered, release = asyncio.Event(), asyncio.Event()
    writers, calls = [], [0]

    async def serve(reader, writer):
        writers.append(writer)
        try:
            await reader.readuntil(b"\r\n\r\n")
            calls[0] += 1
            frame = native(calls[0])
            payload = wire(frame.payload, frame.source.model_dump(mode="json"))
            payload = payload.removesuffix(b"--rider--\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: multipart/x-mixed-replace; boundary=rider\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
                + f"{len(payload):x}\r\n".encode()
                + payload
                + b"\r\n"
            )
            await writer.drain()
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()

    class CloseBarrier(httpx.AsyncByteStream):
        def __init__(self, inner):
            self.inner = inner

        async def __aiter__(self):
            async for chunk in self.inner:
                yield chunk

        async def aclose(self):
            entered.set()
            await release.wait()
            await self.inner.aclose()

    async def response_hook(response):
        if calls[0] == 1:
            # Pause the actual pool-owned connection close, after the pool marks
            # it closed but before it removes the request and frees its slot.
            pool_stream = response.stream._stream._httpcore_stream
            pool_stream._stream = CloseBarrier(pool_stream._stream)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = PiClient(Device("pi", "HTTP cleanup fixture", f"http://127.0.0.1:{port}"))
    client.media_http.event_hooks["response"] = [response_hook]
    first, second, closing = client.mjpeg(), None, None
    try:
        assert (await anext(first)).source.sequence == 1
        closing = asyncio.create_task(first.aclose())
        await asyncio.wait_for(entered.wait(), 1)
        # Profile teardown may itself be cancelled more than once by its caller.
        for _ in range(2):
            closing.cancel()
            await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        second = client.mjpeg()
        assert (await asyncio.wait_for(anext(second), 1)).source.sequence == 2
    finally:
        release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await first.aclose()
        if second is not None:
            await second.aclose()
        await client.close()
        server.close()
        await server.wait_closed()
        for writer in writers:
            writer.close()
