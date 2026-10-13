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
    assert image.format == "JPEG" and image.size == (512, 512)
    green, red, amber = [image.getpixel(p) for p in [(80, 80), (430, 80), (80, 470)]]
    assert green[1] > green[0] and green[1] > green[2]
    assert red[0] > red[1]
    assert amber[0] > amber[2] and amber[1] > amber[2]
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

    async def read(self, path, ttl):
        self.calls.append(("read", path))
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
    assert "no-store" == response.headers["cache-control"]


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
    response = await processed_camera(request(Domain(), loop))
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
