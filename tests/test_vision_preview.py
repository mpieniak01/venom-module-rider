import io
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from venom_module_rider.api.vision_preview import processed_camera
from venom_module_rider.errors import RiderError
from venom_module_rider.floor_detector import render_preview
from venom_module_rider.vision_loop import VisionLoop


def test_overlay_floor_obstacle_uncertainty_and_corridor():
    rgb = np.full((512, 512, 3), 0.3, dtype=np.float32)
    logits = np.full((1, 150, 8, 8), -20.0, dtype=np.float32)
    logits[0, 3, :, :4] = 20
    logits[0, 2, :, 4:] = 20
    logits[0, :, 6:, :] = 0
    output = render_preview(rgb, logits, (0.35, 0.65, 0.65, 0.95))
    image = Image.open(io.BytesIO(output))
    assert image.format == "JPEG"
    assert image.size == (512, 512)
    green, red, amber = [image.getpixel(p) for p in [(80, 80), (430, 80), (80, 470)]]
    assert green[1] > green[0]
    assert green[1] > green[2]
    assert red[0] > red[1]
    assert amber[0] > amber[2]
    assert amber[1] > amber[2]
    assert min(image.getpixel((178, 350))) > 180


class Detector:
    def status(self):
        return {"sha256": "a" * 64}

    def detect(self, payload, corridor, **kw):
        return {
            "state": "clear",
            "reason": "floor_candidate",
            "floor_fraction": 1.0,
            "_preview": b"jpeg-fixture",
        }


class Domain:
    def __init__(self, *, legacy=False, stale=False):
        self.client = SimpleNamespace(device=SimpleNamespace(id="pi"))
        self.calls = []
        self.legacy, self.stale = legacy, stale
        self.features = {
            "s15_venom_perception": {"requested": False},
            "s14_venom_navigation": {"requested": False},
        }

    def transport(self):
        return self.client

    async def camera(self, stream):
        self.calls.append(("camera", stream))
        return (
            b"raw-fixture",
            "image/jpeg",
            {
                "x-rider-source": "pi-camera",
                "x-rider-stale": "true" if self.stale else "false",
                "cache-control": "no-store",
            },
        )

    async def read(self, path, ttl, force=False):
        self.calls.append(("read", path))
        if path == "/api/logic/state":
            return {"data": {"state": {"features": self.features}}}
        if path == "/api/navigation/status":
            active = any(row.get("requested") is True for row in self.features.values())
            return {"data": {"running": active, "stale": False, "run_id": "run"}}
        if self.legacy:
            raise RiderError("frame_unavailable", 404)
        return {"data": {"corridor": [0.3, 0.6, 0.7, 0.9], "revision": 4}}


def request(domain, loop):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(rider=domain, rider_vision=loop))
    )


@pytest.mark.parametrize(
    "legacy,policy", [(False, "device_policy"), (True, "diagnostic_default")]
)
async def test_processed_preview_without_actuation_or_pi_processor(legacy, policy):
    domain, loop = Domain(legacy=legacy), VisionLoop(Detector())
    response = await processed_camera(request(domain, loop))
    assert response.body == b"jpeg-fixture"
    assert response.headers["x-rider-source"] == "venom"
    assert response.headers["x-rider-vision-state"] == "clear"
    assert response.headers["x-rider-vision-action"] == "stop"
    assert response.headers["x-rider-vision-policy"] == policy
    assert [c for c in domain.calls if c[0] == "camera"] == [("camera", "raw")]
    assert all(state["signature"][2] == "observe" for state in loop.runs.values())
    assert response.headers["cache-control"] == "no-store"


async def test_stale_input_rejected_before_inference():
    loop = VisionLoop(Detector())
    with pytest.raises(RiderError) as error:
        await processed_camera(request(Domain(stale=True), loop))
    assert error.value.code == "frame_stale"
    assert not loop.runs


async def test_fresh_cached_mask_does_not_compete_with_decisions():
    from venom_module_rider.vision_contract import VisionFrame

    clock = [0.0]
    loop = VisionLoop(Detector(), clock=lambda: clock[0])
    frame = VisionFrame(
        protocol_version=1,
        run_id="run",
        frame_id="captured",
        corridor=(0.3, 0.6, 0.7, 0.9),
        sequence=1,
        config_revision=4,
        age_ms=0.0,
        deadline_ms=300,
        mode="observe",
    )
    result = await loop.decide("pi", frame, b"raw")
    assert "_preview" not in result
    domain = Domain()
    domain.features["s15_venom_perception"]["requested"] = True
    response = await processed_camera(request(domain, loop))
    assert response.headers["x-rider-frame-id"] == "captured"
    assert len(loop.runs) == 1
    clock[0] = 0.6
    assert loop.preview("pi") is None
    await loop.close()
    assert not loop.previews


async def test_failed_inference_clears_cached_mask():
    from venom_module_rider.vision_contract import VisionFrame

    loop = VisionLoop(Detector())
    frame = VisionFrame(
        protocol_version=1,
        run_id="run",
        frame_id="captured",
        corridor=(0.3, 0.6, 0.7, 0.9),
        sequence=1,
        config_revision=4,
        age_ms=0.0,
        deadline_ms=300,
        mode="observe",
    )
    await loop.decide("pi", frame, b"raw")

    def fail(*args, **kw):
        raise ValueError("fixture")

    loop.detector.detect = fail
    await loop.decide(
        "pi", frame.model_copy(update={"sequence": 2, "frame_id": "new"}), b"raw"
    )
    assert loop.preview("pi") is None
    with pytest.raises(RiderError):
        await processed_camera(request(Domain(), loop))


@pytest.mark.parametrize("goal", ["s15_venom_perception", "s14_venom_navigation"])
async def test_active_pi_loop_never_admits_independent_preview_inference(goal):
    domain = Domain()
    domain.features[goal]["requested"] = True

    class DecisionOnly(Detector):
        def detect(self, *args, **kwargs):
            raise AssertionError("Preview must not occupy the Pi decision slot")

    loop = VisionLoop(DecisionOnly())
    with pytest.raises(RiderError) as error:
        await processed_camera(request(domain, loop))
    assert error.value.code == "vision_busy"
    assert not loop.runs
    assert loop.busy is None


async def test_unknown_pi_loop_state_does_not_compete_for_detector():
    domain, loop = Domain(), VisionLoop(Detector())
    domain.features = {}
    with pytest.raises(RiderError) as error:
        await processed_camera(request(domain, loop))
    assert error.value.code == "vision_busy"
    assert not loop.runs


async def test_expired_preview_cannot_interrupt_next_pi_decision():
    from venom_module_rider.vision_contract import VisionFrame

    clock = [0.0]
    loop = VisionLoop(Detector(), clock=lambda: clock[0])
    domain = Domain()
    domain.features["s15_venom_perception"]["requested"] = True
    frame = VisionFrame(
        protocol_version=1,
        run_id="run",
        frame_id="captured",
        corridor=(0.3, 0.6, 0.7, 0.9),
        sequence=1,
        config_revision=4,
        age_ms=0.0,
        deadline_ms=300,
        mode="observe",
    )
    await loop.decide("pi", frame, b"raw")
    cached = await processed_camera(request(domain, loop))
    assert cached.headers["x-rider-frame-id"] == "captured"
    clock[0] = 0.6
    with pytest.raises(RiderError) as error:
        await processed_camera(request(domain, loop))
    assert error.value.code == "vision_busy"
    assert loop.busy is None
    assert len(loop.runs) == 1
    result = await loop.decide(
        "pi", frame.model_copy(update={"sequence": 2, "frame_id": "next"}), b"raw"
    )
    assert result["frame_id"] == "next"
    assert result["action"] == "stop"


async def test_preview_waits_for_inflight_pi_result_without_another_inference():
    import asyncio
    import threading

    from venom_module_rider.vision_contract import VisionFrame

    release = threading.Event()

    class Inflight(Detector):
        calls = 0

        def detect(self, *args, **kwargs):
            self.calls += 1
            release.wait(1)
            return super().detect(*args, **kwargs)

    detector = Inflight()
    loop, domain = VisionLoop(detector), Domain()
    domain.features["s15_venom_perception"]["requested"] = True
    frame = VisionFrame(
        protocol_version=1,
        run_id="run",
        frame_id="incoming",
        corridor=(0.3, 0.6, 0.7, 0.9),
        sequence=1,
        config_revision=4,
        age_ms=0.0,
        deadline_ms=300,
        mode="observe",
    )
    decision = asyncio.create_task(loop.decide("pi", frame, b"raw"))
    preview = asyncio.create_task(processed_camera(request(domain, loop)))
    await asyncio.sleep(0.01)
    assert not preview.done()
    release.set()
    await decision
    response = await preview
    assert response.headers["x-rider-frame-id"] == "incoming"
    assert detector.calls == 1
    assert len(loop.runs) == 1


async def test_preview_wait_requires_fresh_matching_policy_and_is_bounded():
    from venom_module_rider.vision_contract import VisionFrame

    loop = VisionLoop(Detector())
    frame = VisionFrame(
        protocol_version=1,
        run_id="run",
        frame_id="cached",
        corridor=(0.3, 0.6, 0.7, 0.9),
        sequence=1,
        config_revision=4,
        age_ms=0.0,
        deadline_ms=300,
        mode="observe",
    )
    await loop.decide("pi", frame, b"raw")
    assert loop.preview("pi", corridor=(0.1, 0.2, 0.8, 0.9)) is None
    assert loop.preview("pi", revision=5) is None
    assert (
        await loop.wait_for_preview(
            "pi", corridor=frame.corridor, revision=5, timeout=0.01
        )
        is None
    )
    cached = await loop.wait_for_preview("pi", corridor=frame.corridor, revision=4)
    assert cached is not None
    assert cached[2]["frame_id"] == "cached"


async def test_module_close_releases_preview_waiters():
    import asyncio

    loop = VisionLoop(Detector())
    waiter = asyncio.create_task(
        loop.wait_for_preview("pi", corridor=(0.3, 0.6, 0.7, 0.9), revision=4)
    )
    await asyncio.sleep(0)
    await loop.close()
    assert await waiter is None


@pytest.mark.parametrize("label", [6, 11, 28])
def test_unsupported_ground_is_blue_not_an_obstacle_red_or_go_green(label):
    rgb = np.full((512, 512, 3), 0.3, dtype=np.float32)
    logits = np.zeros((1, 150, 8, 8), dtype=np.float32)
    logits[:, label] = 20
    image = Image.open(
        io.BytesIO(render_preview(rgb, logits, (0.35, 0.65, 0.65, 0.95)))
    )
    red, green, blue = image.getpixel((256, 256))
    assert blue > green > red


@pytest.mark.parametrize("moving", [False, True])
async def test_manual_takeover_keeps_legacy_preview_after_journey(moving):
    domain = Domain()
    domain.features["s14_venom_navigation"]["requested"] = True
    original = domain.read

    async def read(path, ttl, force=False):
        if path == "/api/navigation/status":
            return {
                "data": {
                    "running": False,
                    "stale": True,
                    "journey": {"version": 1, "active": False, "state": "failed"},
                }
            }
        if path == "/api/control/state":
            return {
                "data": {
                    "safety": {
                        "state": "ARMED",
                        "owner": "manual",
                        "arming_mode": "operator",
                        "can_move": True,
                        "moving": moving,
                        "stale": False,
                        "journey": {"active": False},
                    }
                }
            }
        return await original(path, ttl, force)

    domain.read = read
    loop = VisionLoop(Detector())
    try:
        response = await processed_camera(request(domain, loop))
        assert response.body == b"jpeg-fixture"
        assert response.headers["x-rider-vision-action"] == "stop"
        assert all(row["signature"][2] == "observe" for row in loop.runs.values())
        assert all(call[0] in {"read", "camera"} for call in domain.calls)
    finally:
        await loop.close()
