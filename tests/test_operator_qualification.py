"""Warm qualification resets permission while retaining the loaded inference lane."""

from test_stream_decisions import Detector, frame

from venom_module_rider.vision_loop import VisionLoop


async def test_new_run_requires_three_new_decisions_without_reloading_detector():
    detector = Detector()
    loop = VisionLoop(detector)
    settings = {
        "mode": "drive",
        "verified": True,
        "calibration_id": "fixture",
        "model_sha256": "a" * 64,
    }
    try:
        for sequence in range(1, 4):
            before = await loop.decide(
                "pi",
                frame("before-intervention", sequence=sequence, **settings),
                b"frame",
                source_key=("camera-session", sequence),
            )
        assert before["action"] == "go"
        assert detector.calls == 3
        # A fresh run receives new captures from the same uninterrupted camera.
        for sequence in range(4, 7):
            after = await loop.decide(
                "pi",
                frame("explicit-resume", sequence=sequence, **settings),
                b"frame",
                source_key=("camera-session", sequence),
            )
            assert after["clear_count"] == sequence - 3
            assert after["action"] == ("go" if sequence == 6 else "stop")
        assert loop.detector is detector
        assert detector.calls == 6
    finally:
        await loop.close()


async def test_shared_detection_cache_does_not_transfer_earlier_run_clearance():
    detector = Detector()
    loop = VisionLoop(detector)
    settings = {
        "mode": "drive",
        "verified": True,
        "calibration_id": "fixture",
        "model_sha256": "a" * 64,
    }
    try:
        for sequence in range(1, 4):
            await loop.decide(
                "pi",
                frame("before", sequence=sequence, **settings),
                b"frame",
                source_key=("camera-session", sequence),
            )
        after = await loop.decide(
            "pi",
            frame("after", sequence=3, **settings),
            b"frame",
            source_key=("camera-session", 3),
        )
        assert detector.calls == 3
        assert after["clear_count"] == 1
        assert after["action"] == "stop"
        # Capture cutoff admission is authoritative on Pi; even a cache hit
        # cannot carry the previous run's consecutive-clear authorization.
    finally:
        await loop.close()
