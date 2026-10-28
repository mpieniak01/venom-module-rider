"""Preview correlation and bounded, camera-independent active-loop reads."""

import asyncio
from types import SimpleNamespace

import pytest

from venom_module_rider.api.vision_preview import processed_camera
from venom_module_rider.errors import RiderError
from venom_module_rider.vision_contract import VisionFrame
from venom_module_rider.vision_loop import VisionLoop


class Detector:
    def __init__(self):
        self.calls = 0
        self.failure = None

    def status(self):
        return {"sha256": "a" * 64}

    def detect(self, *args):
        self.calls += 1
        if self.failure:
            raise self.failure
        return {
            "state": "clear",
            "reason": "floor_candidate",
            "_preview": b"jpeg-fixture",
        }


def frame(sequence=1, run="run", age=40.0):
    return VisionFrame(
        protocol_version=1,
        run_id=run,
        frame_id=f"{run}-{sequence}",
        sequence=sequence,
        config_revision=4,
        corridor=(0.3, 0.6, 0.7, 0.9),
        age_ms=age,
        deadline_ms=300,
        mode="observe",
    )


class Domain:
    def __init__(self, *, run="run", active=True):
        self.run, self.active = run, active
        self.camera_calls = 0
        self.reads = []
        self.status = {"running": active, "stale": False, "run_id": run}

    def transport(self):
        return SimpleNamespace(device=SimpleNamespace(id="pi"))

    async def read(self, path, ttl, force=False):
        self.reads.append((path, ttl, force))
        if path.endswith("config"):
            return {"data": {"corridor": [0.3, 0.6, 0.7, 0.9], "revision": 4}}
        if path.endswith("status"):
            return {"data": self.status}
        return {
            "data": {
                "features": {
                    "s15_venom_perception": {"requested": self.active},
                    "s14_venom_navigation": {"requested": False},
                }
            }
        }

    async def camera(self, stream):
        self.camera_calls += 1
        raise AssertionError("An active loop must never fetch RAW for the preview")


def request(domain, loop):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(rider=domain, rider_vision=loop))
    )


async def test_two_browsers_share_one_result_without_raw_fetch_or_inference():
    now = [1.0]
    detector = Detector()
    loop = VisionLoop(detector, clock=lambda: now[0])
    await loop.decide("pi", frame(sequence=7), b"raw")
    now[0] = 1.06
    domain = Domain()
    responses = await asyncio.gather(
        processed_camera(request(domain, loop)),
        processed_camera(request(domain, loop)),
    )
    for response in responses:
        assert response.body == b"jpeg-fixture"
        assert response.headers["x-rider-run-id"] == "run"
        assert response.headers["x-rider-sequence"] == "7"
        assert response.headers["x-rider-frame-id"] == "run-7"
        assert response.headers["x-rider-config-revision"] == "4"
        assert float(response.headers["x-rider-frame-age-ms"]) == pytest.approx(100)
        assert (
            response.headers["x-rider-frame-age-source"]
            == "pi-reported-plus-host-elapsed"
        )
        assert response.headers["cache-control"] == "no-store"
    assert detector.calls == 1
    assert domain.camera_calls == 0
    assert all(ttl == 0.2 and not force for _, ttl, force in domain.reads)


@pytest.mark.parametrize(
    "status",
    [
        {"run_id": "new-run", "running": True, "stale": False},
        {"run_id": "run", "running": False, "stale": False},
        {"run_id": "run", "running": True, "stale": True},
        {"running": True, "stale": False},
    ],
)
async def test_active_loop_never_displays_previous_or_unconfirmed_run(status):
    detector, domain = Detector(), Domain()
    loop = VisionLoop(detector)
    await loop.decide("pi", frame(age=0), b"raw")
    domain.status = status

    async def no_wait(*args, **kwargs):
        assert kwargs["run_id"] == "new-run"
        return None

    loop.wait_for_preview = no_wait
    with pytest.raises(RiderError, match="vision_busy"):
        await processed_camera(request(domain, loop))
    assert detector.calls == 1
    assert domain.camera_calls == 0


async def test_waiter_can_only_receive_its_matching_run():
    loop = VisionLoop(Detector())
    await loop.decide("pi", frame(run="old", age=0), b"raw")
    assert loop.preview("pi", run_id="new") is None
    waiter = asyncio.create_task(
        loop.wait_for_preview("pi", corridor=frame().corridor, revision=4, run_id="new")
    )
    await asyncio.sleep(0)
    assert not waiter.done()
    await loop.decide("pi", frame(run="new", age=0), b"raw")
    row = await waiter
    assert row[2]["run_id"] == "new"
    assert loop.preview("pi", run_id="old") is None


async def test_admitting_new_run_hides_old_preview_even_before_result():
    import threading

    detector, loop = Detector(), VisionLoop(Detector())
    await loop.decide("pi", frame(run="old", age=0), b"raw")
    release = threading.Event()

    def blocked(*args):
        release.wait(1)
        return detector.detect(*args)

    loop.detector.detect = blocked
    pending = asyncio.create_task(loop.decide("pi", frame(run="new", age=0), b"raw"))
    await asyncio.sleep(0.01)
    assert loop.preview("pi", run_id="old") is None
    release.set()
    await pending
    assert loop.preview("pi", run_id="new") is not None


@pytest.mark.parametrize("error", [KeyError("bad-output"), TypeError("bad-type")])
async def test_unexpected_detector_exception_returns_unknown_and_clears_preview(error):
    detector = Detector()
    loop = VisionLoop(detector)
    await loop.decide("pi", frame(age=0), b"raw")
    detector.failure = error
    result = await loop.decide("pi", frame(sequence=2, age=0), b"raw")
    assert result["state"] == "unknown"
    assert result["action"] == result["vision_action"] == "stop"
    assert result["reason"] == "vision_inference_unavailable"
    assert result["clear_count"] == 0
    assert loop.preview("pi") is None
    assert loop.busy is None


async def test_preview_age_uses_reported_age_and_host_monotonic_only(monkeypatch):
    now = [10.0]
    loop = VisionLoop(Detector(), clock=lambda: now[0])
    monkeypatch.setattr("venom_module_rider.vision_loop.time.time", lambda: 999999.0)
    await loop.decide("pi", frame(age=45), b"raw")
    now[0] = 10.05
    monkeypatch.setattr("venom_module_rider.vision_loop.time.time", lambda: 1.0)
    first = await processed_camera(request(Domain(), loop))
    assert float(first.headers["x-rider-frame-age-ms"]) == pytest.approx(95)
    now[0] = 10.1
    second = await processed_camera(request(Domain(), loop))
    assert float(second.headers["x-rider-frame-age-ms"]) == pytest.approx(145)
    now[0] = 10.46
    assert loop.preview("pi") is None


async def test_diagnostic_snapshot_reports_unverified_relative_age_source():
    class Snapshot(Domain):
        async def camera(self, stream):
            self.camera_calls += 1
            # Pi wall time deliberately differs from the Venom clock.
            return b"raw", "image/jpeg", {"x-rider-file-observed-at": "1"}

    domain, loop = Snapshot(active=False), VisionLoop(Detector())
    response = await processed_camera(request(domain, loop))
    assert response.headers["x-rider-frame-age-source"] == "host-received"
    assert response.headers["x-rider-vision-action"] == "stop"
    assert domain.camera_calls == 1


async def test_active_cached_preview_does_not_read_full_service_graph():
    class Lightweight(Domain):
        async def read(self, path, *args, **kwargs):
            if path == "/api/logic/state":
                raise AssertionError(
                    "A matching live preview must not read the service graph"
                )
            return await super().read(path, *args, **kwargs)

    loop = VisionLoop(Detector())
    await loop.decide("pi", frame(age=0), b"raw")
    domain = Lightweight()
    response = await processed_camera(request(domain, loop))
    assert response.body == b"jpeg-fixture"
    assert [path for path, *_ in domain.reads] == [
        "/api/navigation/config",
        "/api/navigation/status",
    ]
    assert domain.camera_calls == 0


async def test_stopped_pi_cannot_serve_its_still_fresh_previous_mask():
    class Snapshot(Domain):
        async def camera(self, stream):
            self.camera_calls += 1
            return b"new-raw", "image/jpeg", {"x-rider-stale": "false"}

    detector, domain = Detector(), Snapshot(active=False)
    loop = VisionLoop(detector)
    await loop.decide("pi", frame(age=0), b"old-raw")
    response = await processed_camera(request(domain, loop))
    assert response.headers["x-rider-frame-id"] != "run-1"
    assert response.headers["x-rider-run-id"] != "run"
    assert response.headers["x-rider-frame-age-source"] == "host-received"
    assert detector.calls == 2
    assert domain.camera_calls == 1


async def test_live_producer_without_cache_cannot_fall_back_to_independent_inference():
    class Lightweight(Domain):
        async def read(self, path, *args, **kwargs):
            if path == "/api/logic/state":
                raise AssertionError("A running producer already owns inference")
            return await super().read(path, *args, **kwargs)

    detector, domain = Detector(), Lightweight()
    loop = VisionLoop(detector)

    async def no_result(*args, **kwargs):
        return None

    loop.wait_for_preview = no_result
    with pytest.raises(RiderError, match="vision_busy"):
        await processed_camera(request(domain, loop))
    assert detector.calls == 0
    assert domain.camera_calls == 0


async def test_snapshot_cache_is_rechecked_after_slow_pi_metadata_reads():
    now = [10.0]
    detector = Detector()
    loop = VisionLoop(detector, clock=lambda: now[0])
    await loop.decide("pi", frame(run="old-snapshot", age=0), b"old", with_preview=True)

    class SlowMetadata(Domain):
        async def read(self, path, *args, **kwargs):
            result = await super().read(path, *args, **kwargs)
            if path.endswith("status"):
                now[0] += 1.2
            return result

        async def camera(self, stream):
            self.camera_calls += 1
            return b"new-raw", "image/jpeg", {"x-rider-stale": "false"}

    domain = SlowMetadata(active=False)
    response = await processed_camera(request(domain, loop))
    assert response.headers["x-rider-frame-id"] != "old-snapshot-1"
    assert float(response.headers["x-rider-frame-age-ms"]) < 500
    assert domain.camera_calls == 1
    assert detector.calls == 2


async def test_snapshot_returns_newest_result_published_during_goal_read():
    now = [10.0]
    detector = Detector()
    loop = VisionLoop(detector, clock=lambda: now[0])
    await loop.decide("pi", frame(run="old-snapshot", age=0), b"old", with_preview=True)

    class UpdatedMetadata(Domain):
        async def read(self, path, *args, **kwargs):
            result = await super().read(path, *args, **kwargs)
            if path == "/api/logic/state":
                await loop.decide(
                    "pi", frame(run="new-snapshot", age=0), b"new", with_preview=True
                )
            return result

    domain = UpdatedMetadata(active=False)
    response = await processed_camera(request(domain, loop))
    assert response.headers["x-rider-frame-id"] == "new-snapshot-1"
    assert domain.camera_calls == 0
    assert detector.calls == 2


async def test_standalone_browsers_join_one_raw_capture_and_inference():
    entered, release = asyncio.Event(), asyncio.Event()

    class Snapshot(Domain):
        async def camera(self, stream):
            self.camera_calls += 1
            entered.set()
            await release.wait()
            return b"raw", "image/jpeg", {"x-rider-stale": "false"}

    detector, domain = Detector(), Snapshot(active=False)
    loop = VisionLoop(detector)
    first = asyncio.create_task(processed_camera(request(domain, loop)))
    await entered.wait()
    second = asyncio.create_task(processed_camera(request(domain, loop)))
    await asyncio.sleep(0)
    release.set()
    responses = await asyncio.gather(first, second)
    assert domain.camera_calls == detector.calls == 1
    assert (
        responses[0].headers["x-rider-frame-id"]
        == responses[1].headers["x-rider-frame-id"]
    )
    assert all(r.headers["x-rider-vision-action"] == "stop" for r in responses)


async def test_cancelling_one_browser_does_not_cancel_shared_snapshot():
    entered, release = asyncio.Event(), asyncio.Event()

    class Snapshot(Domain):
        async def camera(self, stream):
            self.camera_calls += 1
            entered.set()
            await release.wait()
            return b"raw", "image/jpeg", {"x-rider-stale": "false"}

    detector, domain = Detector(), Snapshot(active=False)
    loop = VisionLoop(detector)
    first = asyncio.create_task(processed_camera(request(domain, loop)))
    await entered.wait()
    second = asyncio.create_task(processed_camera(request(domain, loop)))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    response = await second
    assert response.body == b"jpeg-fixture"
    assert domain.camera_calls == detector.calls == 1
    assert not loop.snapshot_tasks


async def test_module_close_cancels_pending_snapshot_and_releases_readers():
    entered = asyncio.Event()
    loop = VisionLoop(Detector())

    async def produce():
        entered.set()
        await asyncio.Event().wait()

    reader = asyncio.create_task(loop.share_snapshot(("pi", 1), produce))
    await entered.wait()
    await loop.close()
    with pytest.raises(asyncio.CancelledError):
        await reader
    assert not loop.snapshot_tasks
    with pytest.raises(RiderError, match="module_closed"):
        await loop.share_snapshot(("pi", 1), produce)


async def test_snapshot_failures_are_shared_and_retry_does_not_reuse_failure():
    loop = VisionLoop(Detector())
    release = asyncio.Event()
    calls = 0

    async def produce():
        nonlocal calls
        calls += 1
        await release.wait()
        raise RiderError("camera_off", 404)

    first = asyncio.create_task(loop.share_snapshot(("pi", 1), produce))
    second = asyncio.create_task(loop.share_snapshot(("pi", 1), produce))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert calls == 1
    assert all(isinstance(r, RiderError) and r.code == "camera_off" for r in results)
    assert not loop.snapshot_tasks
    with pytest.raises(RiderError, match="camera_off"):
        await loop.share_snapshot(("pi", 1), produce)
    assert calls == 2


async def test_different_policy_cannot_join_another_snapshot_and_queue_is_bounded():
    entered, release = asyncio.Event(), asyncio.Event()
    loop = VisionLoop(Detector())

    async def produce():
        entered.set()
        await release.wait()
        return "result"

    readers = [
        asyncio.create_task(loop.share_snapshot(("pi", revision), produce))
        for revision in range(16)
    ]
    await entered.wait()
    await asyncio.sleep(0)
    assert len(loop.snapshot_tasks) == 16
    with pytest.raises(RiderError, match="vision_busy"):
        await loop.share_snapshot(("pi", 17), produce)
    release.set()
    assert await asyncio.gather(*readers) == ["result"] * 16
    assert not loop.snapshot_tasks
