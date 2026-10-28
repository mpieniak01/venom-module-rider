"""Actual bounded writer, durable DB and VisionLoop; never a physical Pi."""

import asyncio
import io
import threading

import pytest
from PIL import Image
from test_samples_api import BASE, rig
from test_vision_loop import Detector, frame

from venom_module_rider.analysis_metrics import approved_label
from venom_module_rider.automatic_capture import scene_hash
from venom_module_rider.errors import RiderError
from venom_module_rider.event_policy import EventProfile
from venom_module_rider.samples_frame import FrameBuffer
from venom_module_rider.samples_registry import SamplesRegistry
from venom_module_rider.vision_loop import VisionLoop


def png(seq=1):
    stream = io.BytesIO()
    Image.new("RGB", (32, 24), (seq % 255, 100, 60)).save(stream, "PNG")
    return stream.getvalue()


async def make(tmp_path, **profile):
    clock = [10.0]
    registry = SamplesRegistry(
        tmp_path / "samples", frames=FrameBuffer(clock=lambda: clock[0])
    )
    session = await registry.start("collection", "robot")
    values = EventProfile(stable_frames=1, stable_ms=0, **profile).model_dump()
    await registry.automatic.start(
        session["session_id"], "enable", "robot", "host", values
    )
    return registry, session["session_id"], clock


def offer(registry, clock, seq=1, state="obstacle", **changes):
    context = {
        "host_device_session": "host",
        "source_session": "run",
        "run_id": "run",
        "sequence": seq,
        "frame_id": f"frame-{seq}",
        "source_age_ms": 0,
    }
    context.update(changes.pop("context", {}))
    key = registry.offer("robot", png(seq), "image/png", context)
    prediction = {
        "state": state,
        "reason": "fixture",
        "model_sha256": "a" * 64,
        "perception_engine": "depth_plane",
        "valid_for_ms": 200,
        "input_frame": {
            "run_id": "run",
            "config_revision": 1,
            "sequence": seq,
            "frame_id": f"frame-{seq}",
        },
        **changes,
    }
    registry.annotate_prediction(key, prediction)
    return key


async def drain(registry):
    await asyncio.wrap_future(registry.io.submit(lambda: None))
    for _ in range(10):
        await asyncio.sleep(0)
        if not registry.automatic.pending:
            return
    raise AssertionError("auto writer callback missing")


async def test_exact_completed_prediction_pending_label_and_manual_same_sources(
    tmp_path,
):
    reg, sid, clock = await make(tmp_path)
    try:
        key = offer(reg, clock)
        await drain(reg)
        auto = reg.automatic.status()
        assert auto["saved"] == 1
        page = await reg.read(reg.automatic.store.page, auto["id"])
        event = page["events"][0]
        assert page["counts"] == {"saved": 1}
        saved = reg.store.sample(event["sample_id"])
        assert saved["raw"]["sha256"] == reg.frames.get(key).sha256
        assert saved["labels"][0]["author"]["kind"] == "auto"
        assert saved["labels"][0]["review"] == "pending"
        assert approved_label(saved) is None
        assert saved["source_context"]["automatic_capture"]["confidence"] is None
        assert saved["prediction"]["state"] == "obstacle"
        # Explicit manual intent is a separate receipt of the exact same RAW.
        reg.capture(sid, "manual-same", key)
        await drain(reg)
        assert (await reg.request("manual-same"))["state"] == "saved"
        assert len(reg.store.samples(sid)) == 2
        assert reg.store.original(saved["sample_id"])[0] == png()
        await reg.automatic.stop()
        assert reg.automatic.current is None
        assert (await reg.read(reg.automatic.store.page, auto["id"]))["state"] == "off"
    finally:
        await reg.close()


async def test_manual_saved_first_is_not_relabelled_by_auto(tmp_path):
    reg, sid, clock = await make(tmp_path)
    try:
        key = reg.offer("robot", png(), "image/png")
        reg.capture(sid, "manual-first", key)
        await drain(reg)
        sample_id = (await reg.request("manual-first"))["sample_id"]
        reg.store.label(
            sample_id,
            {"review": "approved", "expected_state": "clear"},
            expected_revision=0,
        )
        offer(reg, clock)
        await drain(reg)
        assert reg.automatic.status()["skipped"] == 1
        assert len(reg.store.samples(sid)) == 1
        assert approved_label(reg.store.sample(sample_id))["expected_state"] == "clear"
    finally:
        await reg.close()


async def test_real_vision_loop_hook_retains_decision_and_exact_source(tmp_path):
    reg, sid, clock = await make(tmp_path)
    loop = VisionLoop(Detector("obstacle"), samples=reg, source_session=lambda: "host")
    try:
        result = await loop.decide("robot", frame(), png())
        assert result["action"] == "stop" and result["state"] == "obstacle"
        await drain(reg)
        assert reg.automatic.status()["saved"] == 1
        sample = reg.store.samples(sid)[0]
        assert sample["frame_id"] == result["frame_id"]
        # The stored prediction is captured before the final response ages
        # its remaining deadline; time spent afterwards can only shorten it.
        assert result["valid_for_ms"] <= sample["prediction"]["valid_for_ms"]
        assert reg.store.original(sample["sample_id"])[0] == png()
    finally:
        await loop.close()
        await reg.close()


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"context": {"recorded_at_header": "100"}}, "auto_source_stale"),
        ({"context": {"stale": True}}, "auto_source_stale"),
        ({"diagnostic": True}, "auto_source_stale"),
        ({"valid_for_ms": 0}, "auto_source_stale"),
        ({"valid_for_ms": True}, "auto_source_stale"),
        ({"valid_for_ms": float("nan")}, "auto_frame_missing"),
    ],
)
async def test_stale_history_diagnostic_never_save(tmp_path, changes, reason):
    reg, sid, clock = await make(tmp_path)
    try:
        offer(reg, clock, **changes)
        await drain(reg)
        assert not reg.store.samples(sid)
        # A rejected RAM annotation does not become a new auto event.
        if changes.get("valid_for_ms") != changes.get("valid_for_ms"):
            assert reg.automatic.status()["attempts"] == 0
        else:
            assert reg.automatic.status()["reason"] == reason
    finally:
        await reg.close()


async def test_queue_delay_cannot_freshen_expired_result(tmp_path):
    reg, sid, clock = await make(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def hold():
        entered.set()
        release.wait(3)

    try:
        future = reg._submit(hold)
        while not entered.is_set():
            await asyncio.sleep(0)
        offer(reg, clock)
        clock[0] += 0.3
        release.set()
        await asyncio.wrap_future(future)
        await drain(reg)
        assert reg.automatic.status()["reason"] == "auto_source_stale"
        assert not reg.store.samples(sid)
    finally:
        release.set()
        await reg.close()


async def test_bounded_auto_admission_leaves_manual_capacity_and_stop_barrier(
    tmp_path, monkeypatch
):
    reg, sid, clock = await make(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = reg.store.capture

    def slow(*args, **kwargs):
        entered.set()
        if not release.wait(3):
            raise RuntimeError("writer not released")
        return original(*args, **kwargs)

    monkeypatch.setattr(reg.store, "capture", slow)
    try:
        offer(reg, clock)
        while not entered.is_set():
            await asyncio.sleep(0)
        for seq in range(2, 102):
            offer(reg, clock, seq)
        assert reg.automatic.status()["dropped"] == 100
        # A hundred auto results consume one writer slot, not a hundred jobs.
        reg.capture(sid, "manual", next(reversed(reg.frames.frames)))
        stopping = asyncio.create_task(reg.automatic.stop())
        await asyncio.sleep(0.01)
        assert reg.automatic.current is None and not stopping.done()
        # No disk lock is held by the event loop.
        tick = asyncio.Event()
        asyncio.get_running_loop().call_soon(tick.set)
        await asyncio.wait_for(tick.wait(), 0.1)
        release.set()
        await stopping
        count = len(reg.store.samples(sid))
        offer(reg, clock, 200)
        await drain(reg)
        assert len(reg.store.samples(sid)) == count == 2
        assert reg.automatic.status()["saved"] == 1
    finally:
        release.set()
        await reg.close()


async def test_limits_burst_and_future_sources(tmp_path):
    reg, sid, clock = await make(tmp_path, burst_frames=3, max_samples=3)
    try:
        for seq in range(1, 5):
            offer(reg, clock, seq)
            await drain(reg)
            clock[0] += 0.25
        assert reg.automatic.status()["saved"] == 3
        assert reg.automatic.status()["reason"] == "auto_sample_limit"
        page = reg.automatic.store.page(reg.automatic.status()["id"])
        assert {row["body"]["source"]["frame_id"] for row in page["events"]} == {
            "frame-1",
            "frame-2",
            "frame-3",
        }
        assert len({row["event_id"] for row in page["events"]}) == 1
        assert len(reg.store.samples(sid)) == 3
    finally:
        await reg.close()


async def test_static_and_flashing_results_stay_bounded(tmp_path):
    reg, sid, clock = await make(tmp_path)
    try:
        for seq in range(1, 101):
            offer(reg, clock, seq, "clear" if seq % 2 else "obstacle")
            await drain(reg)
            clock[0] += 0.1
        assert len(reg.store.samples(sid)) <= 1
    finally:
        await reg.close()


async def test_freshness_model_binding_foreign_frames_and_source_change(tmp_path):
    reg, sid, clock = await make(tmp_path)
    try:
        offer(reg, clock, context={"host_device_session": "other"})
        await drain(reg)
        assert reg.automatic.status()["rejected"] == 1
        offer(reg, clock, 2)
        await drain(reg)
        offer(reg, clock, 3, model_sha256="b" * 64)
        await drain(reg)
        assert reg.automatic.status()["reason"] == "auto_source_changed"
        assert len(reg.store.samples(sid)) == 1
        assert reg.annotate_prediction("missing", {}) is False
    finally:
        await reg.close()


async def test_full_disk_stops_without_claiming_save(tmp_path, monkeypatch):
    reg, sid, clock = await make(tmp_path)
    try:

        def full(*_):
            raise RiderError("samples_disk_full", 507)

        monkeypatch.setattr(reg.store, "_blob", full)
        offer(reg, clock)
        await drain(reg)
        assert reg.automatic.status()["saved"] == 0
        assert reg.automatic.status()["failed"] == 1
        page = reg.automatic.store.page(reg.automatic.status()["id"])
        assert page["events"][0]["state"] == "failed"
        assert not reg.store.samples(sid)
    finally:
        await reg.close()


async def test_feed_loss_duration_end_restart_do_not_resume(tmp_path):
    reg, sid, clock = await make(tmp_path)
    try:
        row = reg.automatic.current
        clock[0] += 2
        reg.automatic._timer(row)
        await reg.automatic.stop()
        assert reg.automatic.status()["reason"] == "auto_feed_lost"
        assert not reg.writer_owners
        await reg.end(sid)
    finally:
        await reg.close()
    other = SamplesRegistry(tmp_path / "samples")
    try:
        assert other.automatic.status()["state"] == "off"
        assert (await other.status())["active"] == []
    finally:
        await other.close()


async def test_profile_idempotency_and_collection_boundaries(tmp_path):
    reg, sid, clock = await make(tmp_path)
    auto = reg.automatic
    try:
        profile = EventProfile(stable_frames=1, stable_ms=0).model_dump()
        same = await auto.start(sid, "enable", "robot", "host", profile)
        with pytest.raises(RiderError, match="samples_request_conflict"):
            await auto.start(
                sid, "enable", "robot", "host", {**profile, "max_samples": 2}
            )
        with pytest.raises(RiderError, match="auto_already_on"):
            await auto.start(sid, "other", "robot", "host", profile)
        await auto.stop()
        old = await auto.start(sid, "enable", "robot", "host", profile)
        assert old["id"] == same["id"] and old["state"] == "off"
        await auto.start(sid, "second", "robot", "host", profile)
        await reg.end(sid)
        assert auto.status()["reason"] == "auto_collection_ended"
        with pytest.raises(RiderError, match="auto_collection_required"):
            await auto.start(sid, "third", "robot", "host", profile)
    finally:
        await reg.close()


async def test_api_explicit_start_selection_guard_stop_and_no_actuator_posts(tmp_path):
    service, pi, reg, app, client = await rig(tmp_path)
    try:
        created = await client.post(
            BASE + "/samples/sessions",
            json={"request_id": "collect", "source_device_id": "demo"},
        )
        sid = created.json()["session_id"]
        intent = {"session_id": sid, "request_id": "enable"}
        assert (
            await client.post(BASE + "/samples/automatic", json=intent)
        ).status_code == 409
        headers = {"X-Rider-Session": service.session}
        started = await client.post(
            BASE + "/samples/automatic", json=intent, headers=headers
        )
        assert started.status_code == 200, started.text
        assert started.json()["state"] == "on"
        assert started.json()["request_id"] == "enable"
        trace = list(pi.trace)
        assert (await client.get(BASE + "/samples/automatic")).json()["state"] == "on"
        assert (
            await client.get(BASE + "/samples/automatic/runs/" + started.json()["id"])
        ).status_code == 200
        assert (await client.post(BASE + "/samples/automatic/stop", json={})).json()[
            "state"
        ] == "off"
        assert pi.trace == trace and not pi.camera_on and pi.state == "DISARMED"
    finally:
        await client.aclose()
        await app.state.rider_vision.close()
        await reg.close()
        await service.close()


def test_scene_hash_bounded_and_low_texture_not_faked():
    assert scene_hash(png()) is None
    image = Image.new("L", (32, 24))
    for x in range(32):
        for y in range(24):
            image.putpixel((x, y), x * 7)
    stream = io.BytesIO()
    image.save(stream, "PNG")
    assert type(scene_hash(stream.getvalue())) is int
    stream = io.BytesIO()
    Image.new("L", (2100, 2000)).save(stream, "PNG")
    with pytest.raises(RiderError, match="auto_image_invalid"):
        scene_hash(stream.getvalue())


async def test_queue_full_missing_frame_and_mismatched_identity(tmp_path):
    reg, sid, clock = await make(tmp_path)
    entered, release = threading.Event(), threading.Event()
    try:

        def hold():
            entered.set()
            release.wait(3)

        jobs = [reg._submit(hold) for _ in range(reg.queue_slots)]
        while not entered.is_set():
            await asyncio.sleep(0)
        offer(reg, clock)
        assert reg.automatic.status()["dropped"] == 1
        release.set()
        await asyncio.gather(*(asyncio.wrap_future(job) for job in jobs))
        offer(reg, clock, 2, input_frame={"run_id": "other"})
        await drain(reg)
        assert reg.automatic.status()["reason"] == "auto_source_changed"
        assert not reg.store.samples(sid)
        await reg.automatic.stop()
        await reg.automatic.start(
            sid, "missing", "robot", "host", EventProfile().model_dump()
        )
        reg.automatic.offer("missing")
        await reg.automatic.stop()
        assert reg.automatic.status()["reason"] == "auto_frame_missing"
    finally:
        release.set()
        await reg.close()


async def test_duration_shutdown_and_cancel_during_enable(tmp_path, monkeypatch):
    reg, sid, clock = await make(tmp_path, duration_s=1.0)
    try:
        row = reg.automatic.current
        clock[0] += 1.1
        reg.automatic._timer(row)
        await reg.automatic.stop()
        assert reg.automatic.status()["reason"] == "auto_duration_limit"
        original = reg.automatic.store.create
        entered, release = threading.Event(), threading.Event()

        def held(*args):
            entered.set()
            release.wait(3)
            return original(*args)

        monkeypatch.setattr(reg.automatic.store, "create", held)
        starting = asyncio.create_task(
            reg.automatic.start(
                sid, "cancel-enable", "robot", "host", EventProfile().model_dump()
            )
        )
        while not entered.is_set():
            await asyncio.sleep(0)
        stopping = asyncio.create_task(reg.automatic.stop())
        await asyncio.sleep(0)
        release.set()
        assert (await starting)["state"] == "off"
        await stopping
        assert reg.automatic.current is None
        await reg.automatic.close()
        with pytest.raises(RiderError, match="samples_closed"):
            await reg.automatic.start(sid, "closed", "robot", "host", {})
    finally:
        await reg.close()


async def test_annotation_failure_keeps_sample_but_never_claims_complete(
    tmp_path, monkeypatch
):
    reg, sid, clock = await make(tmp_path)
    try:

        def fail(*args, **kwargs):
            raise OSError("label volume unavailable")

        monkeypatch.setattr(reg.store, "label", fail)
        offer(reg, clock)
        await drain(reg)
        row = reg.automatic.store.page(reg.automatic.status()["id"])["events"][0]
        assert row["state"] == "failed" and row["sample_id"]
        assert reg.store.sample(row["sample_id"])["labels"] == []
        assert reg.automatic.status()["saved"] == 0
    finally:
        await reg.close()


async def test_failed_stop_metadata_is_visible_without_blocking_control(
    tmp_path, monkeypatch
):
    reg, sid, clock = await make(tmp_path)

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(reg.automatic.store, "finish", fail)
    try:
        await reg.automatic.stop()
        assert reg.automatic.status()["reason"] == "auto_storage_unavailable"
        assert reg.failures == 1
        assert not reg.writer_owners
    finally:
        await reg.close()


async def test_expiry_during_decode_and_invalid_image_do_not_save(
    tmp_path, monkeypatch
):
    import venom_module_rider.automatic_capture as module

    reg, sid, clock = await make(tmp_path)
    try:

        def delayed(_):
            clock[0] += 0.3
            return None

        monkeypatch.setattr(module, "scene_hash", delayed)
        offer(reg, clock)
        await drain(reg)
        assert reg.automatic.status()["reason"] == "auto_source_stale"
        await reg.automatic.stop()
        await reg.automatic.start(
            sid, "invalid", "robot", "host", EventProfile().model_dump()
        )

        def invalid(_):
            raise OSError("invalid source")

        monkeypatch.setattr(module, "scene_hash", invalid)
        offer(reg, clock, 2)
        await drain(reg)
        assert reg.automatic.status()["reason"] == "auto_write_failed"
        assert not reg.store.samples(sid)
    finally:
        await reg.close()


async def test_control_stop_and_device_switch_invalidate_without_data_io_wait(tmp_path):
    service, pi, reg, app, client = await rig(tmp_path)
    try:
        session = await reg.start("collect", "demo")
        await reg.automatic.start(
            session["session_id"], "enable", "demo", service.session, {}
        )
        await service.control({"action": "stop"}, service.session)
        assert reg.automatic.status()["reason"] == "auto_global_stop"
        await reg.automatic.stop()
        await reg.automatic.start(
            session["session_id"], "again", "demo", service.session, {}
        )
        await service.select(None, service.session)
        assert reg.automatic.status()["reason"] == "auto_device_changed"
    finally:
        await client.aclose()
        await app.state.rider_vision.close()
        await reg.close()
        await service.close()


async def test_controlled_labelled_sequence_manual_and_automatic_same_frames(tmp_path):
    # Ground truth below is explicit fixture annotation, not model output.
    reg, sid, clock = await make(tmp_path, events=["new_obstacle"], cooldown_ms=500)
    manual = await reg.start("reference", "robot")
    try:
        expected = [5, 15]
        for seq in range(1, 21):
            state = "obstacle" if 5 <= seq <= 7 or 15 <= seq <= 17 else "clear"
            key = offer(reg, clock, seq, state)
            reg.capture(manual["session_id"], f"manual-{seq}", key)
            await drain(reg)
            clock[0] += 0.1
        events = reg.automatic.store.page(reg.automatic.status()["id"])["events"]
        # Same obstacle scene is intentionally deduplicated within one enable.
        selected = sorted(row["body"]["source"]["sequence"] for row in events)
        assert selected == expected[:1]
        assert len(reg.store.samples(manual["session_id"])) == 20
        assert len(reg.store.samples(sid)) == 1
        assert all(approved_label(sample) is None for sample in reg.store.samples(sid))
        assert reg.automatic.status()["deduplicated"] > 0
    finally:
        await reg.close()
