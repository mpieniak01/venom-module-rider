"""Capture admission and slow filesystem work stay outside the control loop."""

import asyncio
import threading

import pytest
from pydantic import ValidationError
from test_samples_store import frame, setup

from venom_module_rider.errors import RiderError
from venom_module_rider.samples_frame import FrameBuffer
from venom_module_rider.samples_registry import SamplesRegistry


async def settled(registry, request_id):
    for _ in range(200):
        result = await registry.request(request_id)
        if result["state"] in {"saved", "failed"}:
            return result
        await asyncio.sleep(0.005)
    raise AssertionError("bounded capture did not complete")


async def registry(tmp_path, **kwargs):
    store, _unused_session = setup(tmp_path)
    # No real camera: source receipts are supplied directly to the existing lane.
    instance = SamplesRegistry(store.root, store=store, **kwargs)
    session = await instance.start("registry-create", "robot")
    return instance, session["session_id"]


async def test_exact_ticket_saves_and_retry_survives_eviction_and_close(tmp_path):
    instance, session = await registry(tmp_path)
    try:
        source = frame(context={"frame_id": "floor"})
        key = instance.offer("robot", source.payload, source.mime, source.context())
        queued = instance.capture(session, "request", key)
        assert queued["state"] == "queued" and queued["saved_at"] is None
        saved = await settled(instance, "request")
        assert saved["state"] == "saved"
        assert await instance.read(instance.store.original, saved["sample_id"]) == (
            source.payload,
            source.mime,
        )
        assert instance.capture(session, "request", key) == saved
        with pytest.raises(RiderError, match="samples_request_conflict"):
            instance.capture(session, "request", "different")
        await instance.end(session)
        # A different process can read persisted outcomes without starting Pi.
        other = SamplesRegistry(instance.store.root)
        try:
            assert (await other.request("request"))["sample_id"] == saved["sample_id"]
            again = other.capture(session, "request", key)
            assert again["state"] == "queued"
            assert (await settled(other, "request"))["state"] == "saved"
        finally:
            await other.close()
    finally:
        await instance.close()


async def test_full_writer_queue_does_not_block_event_loop_or_replay_requests(
    tmp_path, monkeypatch
):
    instance, session = await registry(tmp_path, queue_slots=2)
    blocked = threading.Event()
    started = threading.Event()
    original = instance.store.capture

    def slow(*args, **kwargs):
        started.set()
        if not blocked.wait(3):
            raise RuntimeError("test did not release writer")
        return original(*args, **kwargs)

    monkeypatch.setattr(instance.store, "capture", slow)
    try:
        source = frame()
        key = instance.offer("robot", source.payload, source.mime)
        instance.capture(session, "first", key)
        while not started.is_set():
            await asyncio.sleep(0.001)
        instance.capture(session, "second", key)
        with pytest.raises(RiderError, match="samples_queue_full"):
            instance.capture(session, "third", key)
        with pytest.raises(RiderError, match="samples_queue_full"):
            await instance.status()
        # A separate control task must run while both IO slots are occupied.
        ending = asyncio.create_task(instance.end(session))
        await asyncio.sleep(0.01)
        assert not ending.done()
        control = asyncio.Event()
        asyncio.get_running_loop().call_soon(control.set)
        await asyncio.wait_for(control.wait(), 0.1)
        assert (await instance.request("first"))["state"] == "queued"
        blocked.set()
        assert (await settled(instance, "first"))["state"] == "saved"
        assert (await settled(instance, "second"))["state"] == "saved"
        assert len(instance.store.samples(session)) == 2
        assert (await ending)["state"] == "closed"
        assert instance.lease is None
    finally:
        blocked.set()
        await instance.close()


async def test_end_preserves_accepted_queue_and_releases_only_data_lease(tmp_path):
    instance, session = await registry(tmp_path)
    source = frame()
    try:
        key = instance.offer("robot", source.payload, source.mime)
        instance.capture(session, "before-end", key)
        ended = await instance.end(session)
        assert ended["state"] == "closed"
        assert (await settled(instance, "before-end"))["state"] == "saved"
        other = SamplesRegistry(instance.store.root)
        try:
            opened = await other.start("new-collector", "robot")
            assert opened["state"] == "collecting"
        finally:
            await other.close()
    finally:
        await instance.close()


async def test_second_collector_cannot_recover_or_interrupt_live_writer(tmp_path):
    first, session = await registry(tmp_path)
    other = SamplesRegistry(first.store.root)
    try:
        with pytest.raises(RiderError, match="samples_registry_busy"):
            await other.start("other", "robot")
        assert (await other.read(other.store.session, session))["state"] == "collecting"
        await other.close()
        assert first.store.session(session)["state"] == "collecting"
        assert len(first.active) == 1
    finally:
        await other.close()
        await first.close()


async def test_sequence_has_count_time_and_gap_bounds_with_actual_receipts(tmp_path):
    clock = [0.0]
    buffer = FrameBuffer(clock=lambda: clock[0], wall=lambda: 1000.0)
    instance = SamplesRegistry(tmp_path / "sequence", frames=buffer)
    try:
        session = await instance.start(
            "start",
            "robot",
            {
                "kind": "sequence",
                "duration_s": 10.0,
                "interval_ms": 200,
                "max_frames": 3,
            },
        )
        sid = session["session_id"]
        source = frame()
        assert instance.offer("robot", b"", "image/jpeg") is None
        instance.offer("other", source.payload, source.mime)
        for now in [0, 0.05, 0.21, 0.9]:
            clock[0] = now
            instance.offer("robot", source.payload, source.mime)
            await asyncio.sleep(0.01)
        assert instance.sequences[sid]["count"] == 3
        clock[0] = 1.11
        instance.offer("robot", source.payload, source.mime)
        for _ in range(100):
            if sid not in instance.active:
                break
            await asyncio.sleep(0.005)
        stored = instance.store.session(sid)
        assert stored["state"] == "interrupted"  # actual receipt gap, no invented FPS
        assert len(instance.store.samples(sid)) == 3
        assert instance.frames.status()["bytes"] <= 8 * 1024 * 1024
    finally:
        await instance.close()


async def test_deadline_without_a_single_input_is_incomplete(tmp_path):
    instance = SamplesRegistry(tmp_path / "empty-sequence")
    try:
        session = await instance.start(
            "start", "robot", {"kind": "sequence", "duration_s": 1.0}
        )
        sid = session["session_id"]
        instance._finish_sequence(sid)  # simulate the existing timer callback
        await asyncio.sleep(0.02)
        assert instance.store.session(sid)["state"] == "interrupted"
    finally:
        await instance.close()


async def test_restart_and_missing_ticket_do_not_substitute_next_frame(tmp_path):
    instance, session = await registry(tmp_path)
    try:
        key = "a" * 32
        instance.capture(session, "expired", key)
        assert (await settled(instance, "expired"))["error"] == "samples_frame_expired"
        instance.capture("b" * 32, "no-session", key)
        assert (await settled(instance, "no-session"))["state"] == "failed"
    finally:
        await instance.close()
    assert instance.store.session(session)["state"] == "interrupted"
    assert instance.offer("robot", b"ignored", "image/png") is None
    with pytest.raises(RiderError, match="samples_closed"):
        instance.capture(session, "later", key)


async def test_policy_validation_and_collection_limits_are_shared(tmp_path):
    instance, _session = await registry(tmp_path)
    try:
        for policy in [
            {"kind": "drive"},
            {"max_frames": 151},
            {"duration_s": 31},
            {"interval_ms": 1},
        ]:
            with pytest.raises(ValidationError):
                await instance.start("bad", "robot", policy)
        for index in range(3):
            await instance.start("registry-extra-" + str(index), "robot")
        # Retrying a known start at the limit is still idempotent.
        again = await instance.start("registry-create", "robot")
        assert again["state"] == "collecting"
        with pytest.raises(RiderError, match="samples_collection_limit"):
            await instance.start("extra", "robot")
    finally:
        await instance.close()


@pytest.mark.parametrize("slots", [0, 17, True])
def test_invalid_queue_bound_fails_before_opening_store(tmp_path, slots):
    with pytest.raises(ValueError):
        SamplesRegistry(tmp_path, queue_slots=slots)


async def test_failed_source_save_marks_sequence_incomplete(tmp_path):
    instance = SamplesRegistry(tmp_path / "failed-sequence")
    try:
        session = await instance.start("start", "robot", {"kind": "sequence"})
        sid = session["session_id"]
        instance.offer("robot", b"not a valid image", "image/png")
        result = await settled(instance, f"sequence-{sid}-0")
        assert result["state"] == "failed"
        ended = await instance.end(sid)
        assert ended["state"] == "interrupted"
        assert ended["counts"] == {"failed": 1}
        assert instance.store.samples(sid) == []
    finally:
        await instance.close()


async def test_abandoned_manual_collection_has_a_bounded_deadline(tmp_path):
    instance, sid = await registry(tmp_path)
    try:
        assert sid in instance.deadlines
        instance._expire_manual(sid)  # the existing timer, without waiting 30 minutes
        for _ in range(100):
            if sid not in instance.active:
                break
            await asyncio.sleep(0.005)
        assert instance.store.session(sid)["state"] == "interrupted"
        assert sid not in instance.deadlines
        assert instance.lease is None
    finally:
        await instance.close()


def test_ten_thousand_camera_receipts_keep_only_the_bounded_host_buffer():
    source = frame()
    buffer = FrameBuffer()
    for index in range(10000):
        buffer.offer("robot", source.payload, source.mime, {"frame_id": str(index)})
    status = buffer.status()
    assert status["frames"] == 32
    assert status["bytes"] == 32 * len(source.payload)
    assert status["evicted"] == 9968
