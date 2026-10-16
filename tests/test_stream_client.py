"""Pi media transport is bounded and independent of JSON/STOP connection slots."""

import json

import httpx
import pytest
from test_stream_contract import jpeg, source

from venom_module_rider.client import PiClient, expected_contract
from venom_module_rider.config import Device
from venom_module_rider.errors import RiderError

DEVICE = Device("pi", "Pi fixture", "http://127.0.0.1:18880", "device")


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
    assert len(frames) == 1 and frames[0].payload == jpeg()
    assert frames[0].source.sha256 == source()["sha256"]
    assert requests[0].url.path == "/api/resource/camera/stream.mjpg"
    assert requests[0].method == "GET"
    assert (
        client.http is not client.media_http
        and client.stop_http is not client.media_http
    )
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
    assert len(calls) == 1 and calls[0].method == "GET"
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
        assert payload == jpeg() and mime == "image/jpeg"
        assert propagated["x-rider-source"] == "pi-camera"
        assert json.loads(propagated["x-rider-source-json"]) == metadata
        assert propagated["x-rider-frame-age-ms"] == "20.4"
        assert propagated["x-rider-captured-monotonic-ns"] == str(
            metadata["captured_monotonic_ns"]
        )
        assert "set-cookie" not in propagated
    finally:
        await client.close()
