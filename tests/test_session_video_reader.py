"""Offline replay reads source ranges; no inference, extraction or capture writes."""

import hashlib

import pytest
from test_samples_api import BASE, rig
from test_session_video import collection, jpeg

from venom_module_rider.analysis_contract import AnalysisSpec
from venom_module_rider.errors import RiderError
from venom_module_rider.samples_store import SampleStore
from venom_module_rider.session_video_reader import SessionVideoReader


def movie(store, name="movie", count=3):
    sid = collection(store, name)
    frames = [jpeg(i, i / 4) for i in range(count)]
    for f in frames:
        store.video.append(sid, f)
    store.video.finish(sid)
    store.end(sid)
    return sid, frames


def test_indexed_replay_pages_and_seeks_original_bytes_without_copies(tmp_path):
    store = SampleStore(tmp_path)
    sid, frames = movie(store)
    before = sorted(
        str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file()
    )
    reader = SessionVideoReader(store)
    page = reader.page(sid, limit=2)
    assert page["total"] == 3 and page["next_offset"] == 2
    assert [f["source_time_s"] for f in page["frames"]] == [0, 0.25]
    assert [f["sequence"] for f in page["frames"]] == [0, 1]
    assert reader.page(sid, offset=2)["next_offset"] is None
    assert reader.page(sid, offset=200)["frames"] == []
    for i in (2, 0, 1):
        payload, sha = reader.frame(sid, i)
        assert payload == frames[i].payload
        assert sha == hashlib.sha256(payload).hexdigest()
    assert store.session(sid)["counts"].get("saved", 0) == 0
    assert (
        sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())
        == before
    )


def test_replay_fails_closed_on_active_or_changed_source(tmp_path):
    store = SampleStore(tmp_path)
    reader = SessionVideoReader(store)
    active = collection(store)
    store.video.append(active, jpeg())
    with pytest.raises(RiderError, match="samples_video_recording"):
        reader.page(active)
    store.video.finish(active)
    reader.page(active)
    path = store.video.path(active, "capture.mjpeg")
    path.write_bytes(path.read_bytes()[:-1] + b"x")
    with pytest.raises(RiderError, match="samples_video_invalid"):
        reader.frame(active, 0)


@pytest.mark.parametrize("offset,limit", [(-1, 1), (True, 1), (0, 0), (0, 501)])
def test_invalid_replay_pagination(tmp_path, offset, limit):
    with pytest.raises(RiderError, match="samples_video_page_invalid"):
        SessionVideoReader(SampleStore(tmp_path)).page(
            "a" * 32, offset=offset, limit=limit
        )


def test_missing_frame_and_bounded_cache(tmp_path):
    store = SampleStore(tmp_path)
    reader = SessionVideoReader(store)
    for i in range(5):
        sid, _ = movie(store, str(i), 1)
        reader.page(sid)
    assert len(reader.cache) == 4
    for invalid in (-1, True):
        with pytest.raises(RiderError, match="samples_video_frame_invalid"):
            reader.frame(sid, invalid)
    with pytest.raises(RiderError, match="samples_video_frame_not_found"):
        reader.frame(sid, 1)


def test_metadata_limit_is_explicit_not_silent_truncation(tmp_path, monkeypatch):
    store = SampleStore(tmp_path)
    sid, _ = movie(store)
    monkeypatch.setattr("venom_module_rider.session_video_reader.MAX_FRAMES", 2)
    with pytest.raises(RiderError, match="samples_video_index_limit"):
        SessionVideoReader(store).page(sid)
    assert store.video.summary(sid)["frames"] == 3


async def test_http_playback_never_reads_pi_or_occupies_writer(tmp_path):
    import asyncio
    import threading

    service, pi, registry, app, client = await rig(tmp_path)
    gate = threading.Event()
    writing = []
    try:
        sid, frames = movie(registry.store)
        writing = [registry._submit(lambda: gate.wait(3)) for _ in range(4)]
        page = await client.get(BASE + f"/samples/sessions/{sid}/video/frames?limit=2")
        assert page.status_code == 200 and page.json()["next_offset"] == 2
        response = await client.get(BASE + f"/samples/sessions/{sid}/video/frames/1")
        assert response.status_code == 200 and response.content == frames[1].payload
        assert response.headers["x-rider-raw-sha256"] == frames[1].sha256
        wrong = await client.get(
            BASE + f"/samples/sessions/{sid}/video/frames/1?sha256=" + "0" * 64
        )
        assert wrong.status_code == 409
        valid = await client.get(
            BASE + f"/samples/sessions/{sid}/video/frames/1?sha256=" + frames[1].sha256
        )
        assert valid.status_code == 200 and valid.content == frames[1].payload
        assert pi.state == "DISARMED" and not pi.camera_on
        assert not any(job.done() for job in writing)
    finally:
        gate.set()
        await asyncio.gather(*(asyncio.wrap_future(job) for job in writing))
        await client.aclose()
        await registry.close()
        await service.close()


def test_video_request_is_explicit_and_rejects_duplicate_dense_evidence():
    assert AnalysisSpec().input_source == "samples"
    assert AnalysisSpec(input_source="video").count == 200
    with pytest.raises(ValueError, match="analysis_video_evidence_unsupported"):
        AnalysisSpec(input_source="video", capture_evidence=True)
