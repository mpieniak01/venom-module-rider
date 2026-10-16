"""Exact-source inference reuse cannot renew TTL or transfer motion permission."""

import asyncio
import threading

import pytest

from venom_module_rider.errors import RiderError
from venom_module_rider.vision_contract import VisionFrame
from venom_module_rider.vision_loop import VisionLoop


class Detector:
    def __init__(self, release=None):
        self.calls = 0
        self.started = threading.Event()
        self.release = release

    def status(self):
        return {"sha256": "a" * 64}

    def detect(self, payload, corridor):
        self.calls += 1
        self.started.set()
        if self.release:
            self.release.wait(1)
        return {"state": "clear", "reason": "floor_candidate", "_preview": b"overlay"}


def frame(run="pi", *, sequence=1, revision=3, **updates):
    return VisionFrame(
        protocol_version=1,
        run_id=run,
        frame_id=f"captured-{sequence}",
        sequence=sequence,
        config_revision=revision,
        age_ms=5.0,
        deadline_ms=300,
        **updates,
    )


async def test_cancelled_preview_does_not_cancel_shared_pi_inference_or_arm_it():
    release = threading.Event()
    detector = Detector(release)
    loop = VisionLoop(detector)
    diagnostic = asyncio.create_task(
        loop.decide(
            "pi",
            frame("diagnostic"),
            b"source",
            source_key=("source", 1),
            with_preview=True,
        )
    )
    assert await asyncio.to_thread(detector.started.wait, 0.5)
    control = asyncio.create_task(
        loop.decide(
            "pi",
            frame(
                "control",
                mode="drive",
                verified=True,
                calibration_id="fixture",
                model_sha256="a" * 64,
            ),
            b"source",
            source_key=("source", 1),
        )
    )
    await asyncio.sleep(0)
    diagnostic.cancel()
    with pytest.raises(asyncio.CancelledError):
        await diagnostic
    release.set()
    result = await control
    assert detector.calls == 1 and result["action"] == "stop"
    assert result["clear_count"] == 1
    await loop.close()


async def test_cached_detection_does_not_rejuvenate_after_repeated_reads():
    now = [10.0]
    detector = Detector()
    loop = VisionLoop(detector, clock=lambda: now[0])
    first = frame("first").model_copy(update={"deadline_ms": 100})
    await loop.decide("pi", first, b"source", source_key="same")
    now[0] = 10.05
    await loop.decide(
        "pi",
        first.model_copy(update={"run_id": "second"}),
        b"source",
        source_key="same",
    )
    assert detector.calls == 1
    # Expiry is fixed at 10.095, not second read + 95 ms.
    now[0] = 10.10
    await loop.decide(
        "pi", first.model_copy(update={"run_id": "third"}), b"source", source_key="same"
    )
    assert detector.calls == 2
    await loop.close()


async def test_another_policy_or_source_never_reuses_the_previous_assessment():
    detector = Detector()
    loop = VisionLoop(detector)
    await loop.decide("pi", frame("first"), b"one", source_key="source-1")
    await loop.decide("pi", frame("second", revision=4), b"one", source_key="source-1")
    await loop.decide("pi", frame("third"), b"two", source_key="source-2")
    assert detector.calls == 3
    await loop.close()


async def test_different_input_while_model_busy_is_rejected_without_a_backlog():
    release = threading.Event()
    detector = Detector(release)
    loop = VisionLoop(detector)
    active = asyncio.create_task(
        loop.decide("pi", frame("first"), b"one", source_key="source-1")
    )
    assert await asyncio.to_thread(detector.started.wait, 0.5)
    with pytest.raises(RiderError, match="vision_busy"):
        await loop.decide("pi", frame("second"), b"two", source_key="source-2")
    assert ("pi", "second") not in loop.runs
    release.set()
    await active
    assert detector.calls == 1 and len(loop.inference_cache) == 1
    await loop.close()


async def test_shared_cache_is_bounded_and_shutdown_removes_source_results():
    detector = Detector()
    loop = VisionLoop(detector)
    for index in range(12):
        await loop.decide(
            "pi", frame("run", sequence=index), str(index).encode(), source_key=index
        )
    assert len(loop.inference_cache) == 8 and detector.calls == 12
    await loop.close()
    assert not loop.inference_cache and not loop.previews
