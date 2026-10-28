"""Real temporary storage, independent sessions and no robot/camera/network IO."""

import io
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PIL import Image

from venom_module_rider.errors import RiderError
from venom_module_rider.samples_archive import SampleArchive
from venom_module_rider.samples_frame import FrameBuffer
from venom_module_rider.samples_import import SampleImporter
from venom_module_rider.samples_registry import SamplesRegistry
from venom_module_rider.samples_store import SampleStore, StorageLimits
from venom_module_rider.study_sampling import StudySampler


def jpeg(sequence=0, received=0, source="camera-a"):
    image = io.BytesIO()
    Image.new("RGB", (64, 48), (sequence % 256, 10, 30)).save(image, "JPEG")
    buffer = FrameBuffer(clock=lambda: received, wall=lambda: 1000 + received)
    key = buffer.offer(
        "robot",
        image.getvalue(),
        "image/jpeg",
        {
            "frame_id": str(sequence),
            "sequence": sequence,
            "source_session": source,
            "host_device_session": "host-a",
            "stale": False,
        },
    )
    return buffer.get(key)


def collection(store, request="one"):
    row = store.start(request, "robot", {"record_video": True})
    store.video.start(row["session_id"])
    return row["session_id"]


def test_two_recordings_remain_independent_after_restart(tmp_path):
    store = SampleStore(tmp_path)
    expected = {}
    for index in range(2):
        sid = collection(store, str(index))
        one, two = jpeg(index * 10, 0), jpeg(index * 10 + 1, 0.25)
        store.video.append(sid, one)
        store.video.append(sid, two)
        store.video.finish(sid)
        store.end(sid)
        expected[sid] = one.payload + two.payload
    reopened = SampleStore(tmp_path, recover=True)
    assert len(reopened.sessions()) == 2
    for session in reopened.sessions():
        assert session["video"]["state"] == "complete"
        assert session["video"]["frames"] == 2
        assert session["video"]["duration_s"] == 0.25
        assert (
            reopened.video.download(session["session_id"]).read_bytes()
            == expected[session["session_id"]]
        )


def test_restart_recovers_committed_index_before_metadata_update(tmp_path):
    store = SampleStore(tmp_path)
    sid = collection(store)
    store.video.append(sid, jpeg())
    row = store.video.summary(sid)
    row.update(frames=0, bytes=0, index_bytes=0)
    store.video._save(sid, row)
    reopened = SampleStore(tmp_path, recover=True)
    session = reopened.session(sid)
    assert session["state"] == "interrupted"
    assert session["video"]["state"] == "interrupted"
    assert session["video"]["frames"] == 1
    assert reopened.video.download(sid).read_bytes() == jpeg().payload


def test_no_frames_never_claims_success_and_download_is_unavailable(tmp_path):
    store = SampleStore(tmp_path)
    sid = collection(store)
    store.video.note(sid, "samples_video_camera_off")
    result = store.video.finish(sid)
    assert result["frames"] == 0
    assert result["state"] == "error"
    assert result["error"] == "samples_video_no_frames"
    with pytest.raises(RiderError, match="samples_video_unavailable"):
        store.video.download(sid)


def test_duplicates_rate_bound_source_change_and_real_gap_are_reported(tmp_path):
    store = SampleStore(tmp_path)
    sid = collection(store)
    first = jpeg()
    store.video.append(sid, first)
    store.video.append(sid, first)
    store.video.append(sid, jpeg(1, 0.05))
    row = store.video.append(sid, jpeg(2, 1))
    assert row["frames"] == 2
    assert row["duplicates"] == 1
    assert row["gaps"] == 1
    assert row["duration_s"] == 1
    row = store.video.append(sid, jpeg(3, 1.3, "other-camera"))
    assert row["state"] == "error"
    assert row["error"] == "samples_video_source_changed"
    assert store.video.append(sid, jpeg(4, 2))["frames"] == 2


def test_limit_and_disk_failure_preserve_previous_bytes(tmp_path, monkeypatch):
    store = SampleStore(tmp_path, limits=replace(StorageLimits(), session_bytes=4096))
    sid = collection(store)
    for sequence in range(20):
        row = store.video.append(sid, jpeg(sequence, sequence / 4))
    assert row["state"] == "limit"
    assert 0 < row["frames"] < 20
    preserved = store.video.path(sid, "capture.mjpeg").read_bytes()
    assert len(preserved) == row["bytes"]
    other = collection(store, "second")

    def full(*args, **kwargs):
        raise RiderError("samples_disk_full", 507)

    monkeypatch.setattr(store, "_space", full)
    result = store.video.append(other, jpeg())
    assert result["state"] == "error"
    assert result["error"] == "samples_disk_full"
    assert store.video.path(sid, "capture.mjpeg").read_bytes() == preserved


def test_active_download_and_export_refuse_until_writer_finishes(tmp_path):
    store = SampleStore(tmp_path)
    sid = collection(store)
    store.video.append(sid, jpeg())
    for operation in (store.video.download, SampleArchive(store).export):
        with pytest.raises(RiderError, match="samples_video_recording"):
            operation(sid)


async def test_package_contains_video_index_metadata_and_import_refuses_explicitly(
    tmp_path,
):
    store = SampleStore(tmp_path / "source")
    sid = collection(store)
    source = jpeg()
    store.video.append(sid, source)
    store.video.finish(sid)
    store.end(sid)
    path, _ = SampleArchive(store).export(sid)
    manifest = SampleArchive.validate_zip(path, store.limits.session_bytes)
    assert {row["file"] for row in manifest["video"]["files"]} == {
        "video/capture.mjpeg",
        "video/frames.jsonl",
        "video/video.json",
    }
    registry = SamplesRegistry(tmp_path / "target")
    try:
        with pytest.raises(RiderError, match="samples_video_import_unsupported"):
            await SampleImporter(registry).run(path, "import-video")
        assert registry.store.sessions() == []
    finally:
        await registry.close()


def test_corrupt_video_and_crash_tail_are_not_exported_or_silently_repaired(tmp_path):
    store = SampleStore(tmp_path)
    sid = collection(store)
    store.video.append(sid, jpeg())
    path = store.video.path(sid, "capture.mjpeg")
    damaged = path.read_bytes() + b"partial-uncommitted-frame"
    path.write_bytes(damaged)
    reopened = SampleStore(tmp_path, recover=True)
    assert path.read_bytes() == damaged
    assert (
        reopened.session(sid)["video"]["error"] == "samples_video_recovery_incomplete"
    )
    for operation in (reopened.video.download, SampleArchive(reopened).export):
        with pytest.raises(RiderError, match="samples_video_invalid"):
            operation(sid)


async def test_video_sampler_is_independent_of_still_limit_and_never_enables_camera(
    tmp_path,
):
    registry = SamplesRegistry(tmp_path)
    session = await registry.start(
        "record",
        "robot",
        {
            "scene_group": "motion-study-v1:",
            "record_images": True,
            "record_video": True,
            "max_frames": 1,
        },
    )
    sid = session["session_id"]
    calls = []
    registry.frames.clock = lambda: 0.5
    domain = SimpleNamespace(
        samples=registry,
        device=SimpleNamespace(id="robot"),
        session="host-a",
        data=lambda value: value["data"],
    )

    async def read(path, ttl):
        calls.append(path)
        return {"data": {"activity": {"on": True}}}

    async def camera(stream):
        count = sum(value == "raw" for value in calls)
        calls.append(stream)
        frame = jpeg(count, count / 4)
        key = registry.offer("robot", frame.payload, frame.mime, frame.context())
        # Receipt times belong to the existing frame buffer clock in production.
        registry.frames.frames[key] = replace(
            registry.frames.get(key), received_mono=count / 4
        )
        return frame.payload, frame.mime, {"x-rider-capture-key": key}

    async def pipeline(*, force=False):
        return {"supported": False}

    domain.read, domain.camera, domain.pipeline = read, camera, pipeline
    sampler = StudySampler(domain)
    try:
        for _ in range(3):
            await sampler.image_tick()
        await registry.end(sid)
        row = registry.store.session(sid)
        assert row["counts"]["saved"] == 1
        assert row["video"]["frames"] == 3
        assert row["video"]["state"] == "complete"
        assert calls == ["/api/resource/camera", "raw"] * 3
        await sampler.image_tick()
        assert len(calls) == 6
    finally:
        await registry.close()


@pytest.mark.parametrize(
    "field", ["camera_session", "transport_epoch", "pipeline_revision"]
)
def test_native_source_binding_includes_transport_and_pipeline(tmp_path, field):
    store = SampleStore(tmp_path)
    sid = collection(store)
    one, two = jpeg(1, 1), jpeg(2, 2)
    first_context = one.context()
    first_context["source_identity"] = {
        "camera_session": "cam-a",
        "transport_epoch": 1,
        "pipeline_revision": 1,
    }
    next_context = two.context()
    next_context["source_identity"] = {
        **first_context["source_identity"],
        field: "changed" if field == "camera_session" else 2,
    }
    store.video.append(
        sid, replace(one, context_json=json.dumps(first_context).encode())
    )
    result = store.video.append(
        sid, replace(two, context_json=json.dumps(next_context).encode())
    )
    assert result["error"] == "samples_video_source_changed"
    assert result["frames"] == 1


def test_backwards_receipt_time_is_reported_instead_of_rate_limited(tmp_path):
    store = SampleStore(tmp_path)
    sid = collection(store)
    store.video.append(sid, jpeg(1, 2))
    result = store.video.append(sid, jpeg(2, 1))
    assert result["state"] == "error"
    assert result["error"] == "samples_video_source_time"


def test_corrupt_metadata_does_not_hide_other_sessions_or_modify_source(tmp_path):
    store = SampleStore(tmp_path)
    bad = collection(store, "bad")
    good = collection(store, "good")
    path = store.video.path(bad, "video.json")
    path.write_bytes(b"[")
    reopened = SampleStore(tmp_path, recover=True)
    sessions = {row["session_id"]: row for row in reopened.sessions()}
    assert len(sessions) == 2
    assert sessions[bad]["video"]["state"] == "error"
    assert sessions[bad]["video"]["error"] == "samples_video_invalid"
    assert sessions[good]["video"]["error"] != "samples_video_invalid"
    assert path.read_bytes() == b"["
    for operation in (reopened.video.download, SampleArchive(reopened).export):
        with pytest.raises(RiderError, match="samples_video_invalid"):
            operation(bad)


def test_camera_gap_on_stop_is_interrupted_instead_of_complete(tmp_path):
    store = SampleStore(tmp_path)
    sid = collection(store)
    store.video.append(sid, jpeg())
    store.video.note(sid, "samples_video_camera_off")
    assert store.video.finish(sid)["state"] == "interrupted"
    assert store.video.download(sid).read_bytes() == jpeg().payload


async def test_pipeline_failure_stops_video_without_changing_or_losing_session(
    tmp_path,
):
    registry = SamplesRegistry(tmp_path)
    session = await registry.start(
        "record",
        "robot",
        {
            "scene_group": "motion-study-v1:",
            "record_video": True,
        },
    )
    sid = session["session_id"]
    sampler = StudySampler(SimpleNamespace(samples=registry))
    try:
        await sampler.video_error("stream_epoch_changed")
        assert sid not in registry.study_videos
        assert sid in registry.active
        assert registry.store.session(sid)["video"]["state"] == "error"
        assert registry.store.session(sid)["state"] == "collecting"
    finally:
        await registry.close()


async def test_source_retirement_finishes_video_as_interrupted(tmp_path):
    registry = SamplesRegistry(tmp_path)
    session = await registry.start("record", "robot", {"record_video": True})
    sid = session["session_id"]
    try:
        await registry.read(registry.store.video.append, sid, jpeg())
        registry.evidence.retire("source_changed", sid)
        row = await registry.end(sid)
        assert row["state"] == "interrupted"
        assert row["video"]["state"] == "interrupted"
        assert registry.store.video.download(sid).read_bytes() == jpeg().payload
    finally:
        await registry.close()
