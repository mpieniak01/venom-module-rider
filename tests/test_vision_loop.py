import asyncio
import io

import numpy as np
import pytest
from PIL import Image

from venom_module_rider.errors import RiderError
from venom_module_rider.floor_detector import FloorDetector, classify_logits
from venom_module_rider.vision_contract import VisionFrame
from venom_module_rider.vision_loop import VisionLoop


class Detector:
    def __init__(self, state="clear"):
        self.state = state

    def status(self):
        return {"sha256": "a" * 64}

    def detect(self, *_):
        return {
            "state": self.state,
            "reason": "floor_candidate" if self.state == "clear" else self.state,
            "floor_fraction": 1.0,
        }


def frame(seq=1, **kw):
    return VisionFrame(
        protocol_version=1,
        run_id="run",
        frame_id="f" + str(seq),
        sequence=seq,
        config_revision=1,
        age_ms=0.0,
        deadline_ms=300,
        mode="drive",
        verified=True,
        calibration_id="trial",
        model_sha256="a" * 64,
        **kw,
    )


async def test_three_distinct_frames_then_go():
    loop = VisionLoop(Detector())
    assert (await loop.decide("pi", frame(1), b"x"))["action"] == "stop"
    assert (await loop.decide("pi", frame(2), b"x"))["action"] == "stop"
    assert (await loop.decide("pi", frame(3), b"x"))["action"] == "go"
    loop.detector.state = "obstacle"
    assert (await loop.decide("pi", frame(4), b"x"))["action"] == "stop"


@pytest.mark.parametrize(
    "field,value",
    [
        ("verified", False),
        ("mode", "observe"),
        ("model_sha256", "b" * 64),
        ("calibration_id", ""),
    ],
)
async def test_no_clearance_without_validation(field, value):
    loop = VisionLoop(Detector())
    for seq in range(1, 4):
        f = frame(seq).model_copy(update={field: value})
        result = await loop.decide("pi", f, b"x")
        assert result["action"] == "stop"


async def test_replay_and_config_change():
    loop = VisionLoop(Detector())
    await loop.decide("pi", frame(), b"x")
    for f in (frame(), frame(2).model_copy(update={"config_revision": 2})):
        with pytest.raises(RiderError):
            await loop.decide("pi", f, b"x")


async def test_busy_does_not_queue():
    loop = VisionLoop(Detector())
    loop.busy = asyncio.create_task(asyncio.sleep(0.05))
    with pytest.raises(RiderError):
        await loop.decide("pi", frame(), b"x")
    await loop.close()


@pytest.mark.parametrize("state", ["unknown", "obstacle"])
async def test_unknown_and_obstacle_stop(state):
    loop = VisionLoop(Detector(state))
    assert (await loop.decide("pi", frame(), b"x"))["action"] == "stop"


def test_segmentation_floor_obstacle_unknown():
    logits = np.zeros((1, 150, 8, 8), dtype=np.float32)
    logits[:, 3] = 20
    assert classify_logits(logits, (0, 0, 1, 1))["state"] == "clear"
    logits[:, 3, 4, 4] = 0
    logits[:, 0, 4, 4] = 20
    assert classify_logits(logits, (0, 0, 1, 1))["state"] == "obstacle"
    logits[:] = 0
    assert classify_logits(logits, (0, 0, 1, 1))["state"] == "unknown"
    logits[0, 0, 0, 0] = np.nan
    with pytest.raises(ValueError):
        classify_logits(logits, (0, 0, 1, 1))


def test_dark_image_unknown_no_model_load():
    detector = FloorDetector()
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64)).save(buffer, format="JPEG")
    assert detector.detect(buffer.getvalue(), (0, 0, 1, 1))["state"] == "unknown"
    assert detector.session is None


def test_missing_model():
    detector = FloorDetector()
    detector.path = __import__("pathlib").Path("/missing/model.onnx")
    with pytest.raises(ValueError):
        detector._load()
