"""All recording tests use synthetic HTTP peers, never the selected physical Pi."""

import hashlib
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from venom_module_rider import video_capture as video
from venom_module_rider.samples_archive import SampleArchive


@pytest.fixture
def payload():
    stream = io.BytesIO()
    Image.new("RGB", (64, 48), (70, 90, 100)).save(stream, "JPEG")
    return stream.getvalue()


def headers(payload, seq=1, camera="camera", timestamp=None):
    return {
        "x-rider-source-json": json.dumps(
            {
                "contract_version": 1,
                "camera_session": camera,
                "sequence": seq,
                "transport_epoch": 2,
                "pipeline_revision": 3,
                "frame_id": f"{camera}-{seq}",
                "sha256": "a" * 64,
                "frame_age_ms": 10.0,
                "captured_monotonic_ns": timestamp or seq * 100000000,
                "encoded_width": 64,
                "encoded_height": 48,
                "image_transform": {"rotation": 0, "flip_h": False, "flip_v": False},
            }
        ),
        "x-rider-placeholder": "false",
        "content-type": "image/jpeg",
        "content-length": str(len(payload)),
    }


def part(payload, seq=1, **kwargs):
    h = headers(payload, seq, **kwargs)
    return (
        b"--rider\r\n"
        + b"".join((k + ": " + v + "\r\n").encode() for k, v in h.items())
        + b"\r\n"
        + payload
        + b"\r\n"
    )


PIPELINE = {
    "state": "active",
    "camera_session": "camera",
    "transport_epoch": 2,
    "revision": 3,
    "active_profile": {"remote_transport": "mjpeg"},
}


class Stream(httpx.AsyncByteStream):
    def __init__(self, wire, stall=False):
        self.wire, self.stall = wire, stall

    async def __aiter__(self):
        import asyncio

        for start in range(0, len(self.wire), 73):
            yield self.wire[start : start + 73]
        if self.stall:
            await asyncio.sleep(10)


def client_for(wire, calls, pipeline=None, stall=False):
    async def handle(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/camera/pipeline"):
            return httpx.Response(200, json=pipeline or PIPELINE)
        if request.url.path.endswith("/navigation/state"):
            return httpx.Response(
                200, json={"goals": {"drive": False}, "config": {"revision": 132}}
            )
        assert request.url.path.endswith("/camera/stream/raw")
        return httpx.Response(
            200,
            headers={"content-type": "multipart/x-mixed-replace; boundary=rider"},
            stream=Stream(wire, stall),
        )

    return httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(handle)
    )


@pytest.mark.asyncio
async def test_get_only_record_extract_identical_bytes_and_import_contract(
    tmp_path, payload
):
    calls = []
    wire = part(payload) + part(payload, 3) + b"--rider--\r\n"
    async with client_for(wire, calls) as client:
        root = await video.record(client, tmp_path / "recorded", study_session="a" * 32)
    meta = json.loads((root / "recording.json").read_text())
    assert meta["state"] == "stream_ended"
    assert meta["frames"] == 2
    assert meta["sequence_gaps"] == 1
    assert meta["continuous_telemetry_recorded"] is False
    assert (root / "capture.mjpeg").read_bytes() == payload + payload
    assert all((method == "GET" for method, _ in calls))
    assert len(calls) == 3
    out = video.extract(root, tmp_path / "extracted")
    manifest = json.loads((out / "manifest.json").read_text())
    SampleArchive._validate_manifest(manifest)
    assert len(manifest["samples"]) == 2
    assert (out / "frames/000000.jpg").read_bytes() == payload
    assert (out / "frames/000001.jpg").read_bytes() == payload
    rows = [json.loads(s) for s in (root / "frames.jsonl").read_text().splitlines()]
    assert rows[0]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert rows[0]["source"]["sha256"] != rows[0]["sha256"]
    assert rows[0]["offset"] == 0
    assert rows[1]["offset"] == len(payload)
    assert (
        json.loads((out / "transport.json").read_text())["stream"][1]["captured_at"]
        == 0.3
    )


@pytest.mark.asyncio
async def test_camera_off_does_not_open_media_or_create_output(tmp_path):
    calls = []
    async with client_for(b"", calls, {"state": "stopped"}) as client:
        with pytest.raises(ValueError, match="camera_not_active"):
            await video.record(client, tmp_path / "no")
    assert len(calls) == 1
    assert not (tmp_path / "no").exists()


@pytest.mark.asyncio
async def test_timeout_finalizes_completed_frames(tmp_path, payload):
    async with client_for(part(payload), [], stall=True) as client:
        await video.record(client, tmp_path / "rec", seconds=1)
    meta = json.loads((tmp_path / "rec/recording.json").read_text())
    # The last network chunk can be partial; the duration still terminates.
    assert meta["state"] == "duration_limit"


@pytest.mark.asyncio
async def test_stream_error_preserves_committed_evidence(tmp_path, payload):
    async with client_for(part(payload) + part(payload, 2)[:-30], []) as client:
        with pytest.raises(Exception, match="stream_truncated"):
            await video.record(client, tmp_path / "rec")
    meta = json.loads((tmp_path / "rec/recording.json").read_text())
    assert meta["state"] == "error"
    assert meta["frames"] == 1


@pytest.mark.parametrize("change", ["camera", "order", "clock", "placeholder"])
def test_writer_rejects_identity_changes(tmp_path, payload, change):
    writer = video.VideoWriter(tmp_path, ("camera", 2, 3), 10000)
    writer.append(headers(payload, 2), payload)
    h = headers(payload, 3)
    if change == "camera":
        h = headers(payload, 3, camera="other")
    if change == "order":
        h = headers(payload, 1)
    if change == "clock":
        h = headers(payload, 3, timestamp=1)
    if change == "placeholder":
        h["x-rider-placeholder"] = "true"
    with pytest.raises(ValueError):
        writer.append(h, payload)
    writer.close()


def test_duplicate_and_byte_disk_frame_limits(tmp_path, payload, monkeypatch):
    writer = video.VideoWriter(tmp_path, ("camera", 2, 3), len(payload))
    assert writer.append(headers(payload), payload) is None
    assert writer.append(headers(payload), payload) is None
    assert writer.duplicates == 1
    assert writer.append(headers(payload, 2), payload) == "size_limit"
    writer.max_bytes = 100000
    monkeypatch.setattr(video.shutil, "disk_usage", lambda p: SimpleNamespace(free=0))
    assert writer.append(headers(payload, 2), payload) == "disk_limit"
    monkeypatch.setattr(
        video.shutil, "disk_usage", lambda p: SimpleNamespace(free=10**10)
    )
    writer.count = 1000
    assert writer.append(headers(payload, 2), payload) == "frame_limit"
    writer.close()


@pytest.mark.parametrize(
    "seconds,mib,session",
    [(0, 1, None), (301, 1, None), (1, 0, None), (1, 1025, None), (1, 1, "bad")],
)
@pytest.mark.asyncio
async def test_invalid_limits_never_make_http_requests(tmp_path, seconds, mib, session):
    async with client_for(b"", []) as client:
        with pytest.raises(ValueError):
            await video.record(client, tmp_path / "no", seconds, mib, session)


@pytest.mark.asyncio
async def test_cancellation_marks_interrupted(tmp_path, payload):
    import asyncio

    async with client_for(part(payload), [], stall=True) as client:
        task = asyncio.create_task(video.record(client, tmp_path / "rec"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert (
        json.loads((tmp_path / "rec/recording.json").read_text())["state"]
        == "interrupted"
    )


@pytest.mark.asyncio
async def test_existing_output_not_overwritten(tmp_path, payload):
    async with client_for(part(payload), []) as client:
        with pytest.raises(FileExistsError):
            await video.record(client, tmp_path)


@pytest.mark.parametrize(
    "damage", ["hash", "offset", "tail", "active", "count", "format"]
)
@pytest.mark.asyncio
async def test_extract_rejects_corruption(tmp_path, payload, damage):
    async with client_for(part(payload), []) as client:
        root = await video.record(client, tmp_path / "rec")
    meta = json.loads((root / "recording.json").read_text())
    if damage in {"hash", "tail"}:
        with (root / "capture.mjpeg").open("ab" if damage == "tail" else "wb") as f:
            f.write(b"oops")
    elif damage == "offset":
        row = json.loads((root / "frames.jsonl").read_text())
        row["offset"] = 1
        (root / "frames.jsonl").write_text(json.dumps(row) + "\n")
    else:
        if damage == "active":
            meta["state"] = "recording"
        if damage == "count":
            meta["frames"] = 0
        if damage == "format":
            meta["kind"] = "other"
        video.save_json(root / "recording.json", meta)
    with pytest.raises(ValueError):
        video.extract(root, tmp_path / "out")


@pytest.mark.parametrize(
    "url",
    [
        "http://robot:8080",
        "http://127.0.0.1@robot:8080",
        "http://127.0.0.1/?token=x",
        "https://robot:8080",
    ],
)
@pytest.mark.asyncio
async def test_recorder_only_accepts_local_venom_url(url):
    with pytest.raises(ValueError, match="local_venom_url_required"):
        await video.run_record(SimpleNamespace(url=url))


def test_symlink_output_rejected(tmp_path):
    (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="output_symlink"):
        video.new_directory(tmp_path / "link" / "out")


def test_cli_errors_and_offline_extract(monkeypatch, tmp_path, capsys):
    assert (
        video.main(["extract", str(tmp_path), "--output", str(tmp_path / "out")]) == 1
    )
    assert "error" in capsys.readouterr().out

    async def fake(args):
        return {"ok": True}

    monkeypatch.setattr(video, "run_record", fake)
    assert video.main(["check"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True}


async def test_recorder_uses_normalized_loopback_without_proxy(monkeypatch):
    seen = {}

    class Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def snapshot(_client):
        return {"fixture": True}

    monkeypatch.setattr(video.httpx, "AsyncClient", Client)
    monkeypatch.setattr(video, "snapshot", snapshot)
    result = await video.run_record(
        SimpleNamespace(url="http://localhost:8800/", action="check")
    )
    assert result == {"fixture": True}
    assert seen["base_url"] == "http://127.0.0.1:8800"
    assert seen["trust_env"] is False
    assert seen["follow_redirects"] is False
