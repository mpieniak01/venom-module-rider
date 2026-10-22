"""Unified recording uses real host storage, fixture camera and no hardware actions."""

import asyncio
from types import SimpleNamespace

import pytest
from test_samples_store import frame

from venom_module_rider.samples_registry import SamplesRegistry
from venom_module_rider.study_sampling import StudySampler


async def fixture(tmp_path, enabled=True, limit=2):
    registry = SamplesRegistry(tmp_path)
    row = await registry.start(
        "study",
        "robot",
        {
            "scene_group": "motion-study-v1:",
            "record_images": True,
            "max_frames": limit,
            "interval_ms": 1000,
        },
    )
    calls = []
    domain = SimpleNamespace(
        samples=registry, device=SimpleNamespace(id="robot"), session="source"
    )
    domain.data = lambda value: value["data"]

    async def read(path, ttl):
        calls.append(path)
        return {"data": {"activity": {"on": enabled}}}

    async def camera(stream):
        calls.append(stream)
        source = frame(context={"frame_id": str(len(calls)), "stale": False})
        key = registry.offer("robot", source.payload, source.mime, source.context())
        return source.payload, source.mime, {"x-rider-capture-key": key}

    async def pipeline(*, force=False):
        return {"supported": False}

    domain.pipeline = pipeline
    domain.read, domain.camera = read, camera
    return registry, row["session_id"], StudySampler(domain), calls


async def flush(registry):
    await asyncio.wrap_future(registry.io.submit(lambda: None))


async def test_one_session_records_raw_with_telemetry_and_stops_at_limit(tmp_path):
    registry, sid, sampler, calls = await fixture(tmp_path)
    try:
        await sampler.image_tick()
        await sampler.image_tick()
        await flush(registry)
        assert registry.store.session(sid)["counts"]["saved"] == 2
        assert registry.store.session(sid)["state"] == "collecting"
        await sampler.image_tick()
        assert calls == ["/api/resource/camera", "raw"] * 2
        await registry.end(sid)
        await sampler.image_tick()
        assert len(calls) == 4
        assert sid not in registry.study_images
    finally:
        await registry.close()


async def test_camera_off_keeps_study_collecting_and_does_not_request_frame(tmp_path):
    registry, sid, sampler, calls = await fixture(tmp_path, enabled=False)
    try:
        await sampler.image_tick()
        assert calls == ["/api/resource/camera"]
        assert registry.store.session(sid)["state"] == "collecting"
        assert registry.store.session(sid)["counts"] == {}
    finally:
        await registry.close()


async def test_duplicate_ticket_and_retired_study_never_create_extra_images(tmp_path):
    registry, sid, sampler, calls = await fixture(tmp_path)
    try:
        original = sampler.domain.camera
        cached = await original("raw")

        async def same(stream):
            return cached

        sampler.domain.camera = same
        await sampler.image_tick()
        await sampler.image_tick()
        await flush(registry)
        assert registry.store.session(sid)["counts"]["saved"] == 1
        registry.evidence.retire("source_changed", sid)
        before = len(calls)
        await sampler.image_tick()
        assert len(calls) == before
    finally:
        await registry.close()


async def test_end_during_frame_read_cannot_append_to_closed_study(tmp_path):
    registry, sid, sampler, _ = await fixture(tmp_path)
    original = sampler.domain.camera

    async def ending(stream):
        result = await original(stream)
        await registry.end(sid)
        return result

    sampler.domain.camera = ending
    try:
        await sampler.image_tick()
        await flush(registry)
        assert registry.store.session(sid)["counts"] == {}
    finally:
        await registry.close()


async def test_only_explicit_motion_study_opt_in_allocates_image_recorder(tmp_path):
    registry = SamplesRegistry(tmp_path)
    try:
        await registry.start("old", "robot", {"scene_group": "motion-study-v1:"})
        await registry.start("other", "robot", {"record_images": True})
        assert registry.study_images == {}
    finally:
        await registry.close()


async def test_camera_can_be_enabled_during_the_same_study(tmp_path):
    registry, sid, sampler, _ = await fixture(tmp_path, enabled=False)
    try:
        await sampler.image_tick()

        async def enabled(path, ttl):
            return {"data": {"activity": {"on": True}}}

        sampler.domain.read = enabled
        await sampler.image_tick()
        await flush(registry)
        assert registry.store.session(sid)["counts"]["saved"] == 1
    finally:
        await registry.close()


async def test_cancelled_shared_image_read_recovers_without_ui_poll(monkeypatch):
    from unittest.mock import patch

    sampler = StudySampler(SimpleNamespace(session="same"))
    recovered = asyncio.Event()
    calls = 0

    async def tick():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncio.CancelledError()
        recovered.set()

    monkeypatch.setattr(sampler, "image_tick", tick)
    with patch("venom_module_rider.study_sampling.collecting", return_value=True):
        sampler.image_task = asyncio.create_task(sampler.images())
        await asyncio.wait_for(recovered.wait(), 2)
        assert sampler.image_task is not None and not sampler.image_task.done()
        await sampler.close()
    assert calls == 2


@pytest.mark.parametrize(
    "changed", [None, "camera_session", "transport_epoch", "revision", "state"]
)
async def test_fresh_pipeline_identity_rejects_late_source_before_capture(
    tmp_path, changed
):
    from venom_module_rider.errors import RiderError

    registry, sid, sampler, _ = await fixture(tmp_path)
    original = sampler.domain.camera
    source = {
        "camera_session": "camera-a",
        "transport_epoch": 0,
        "revision": 0,
        "state": "active",
        "supported": True,
    }

    async def pipeline(*, force=False):
        assert force is True
        return source

    async def camera(stream):
        payload, mime, headers = await original(stream)
        headers.update(
            {
                "x-rider-camera-session": "camera-a",
                "x-rider-transport-epoch": "0",
                "x-rider-pipeline-revision": "0",
            }
        )
        if changed == "camera_session":
            source[changed] = "camera-b"
        elif changed == "state":
            source[changed] = "stopped"
        elif changed:
            source[changed] = 1
        return payload, mime, headers

    sampler.domain.camera, sampler.domain.pipeline = camera, pipeline
    try:
        if changed:
            with pytest.raises(RiderError, match="stream_epoch_changed"):
                await sampler.image_tick()
        else:
            await sampler.image_tick()
        await flush(registry)
        assert registry.store.session(sid)["counts"].get("saved", 0) == (
            0 if changed else 1
        )
    finally:
        await registry.close()
