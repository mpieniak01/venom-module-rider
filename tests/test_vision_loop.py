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


async def test_expired_invalid_corridor_and_closed():
    loop = VisionLoop(Detector())
    for change in ({"age_ms": 300.0}, {"corridor": (0.9, 0.1, 0.2, 0.5)}):
        with pytest.raises(RiderError):
            await loop.decide("pi", frame().model_copy(update=change), b"x")
    await loop.close()
    with pytest.raises(RiderError):
        await loop.decide("pi", frame(), b"x")


async def test_timeout_holds_slot_until_worker_finishes():
    import threading

    entered = threading.Event()
    release = threading.Event()

    class Blocked(Detector):
        def detect(self, *args):
            entered.set()
            release.wait(1)
            return super().detect(*args)

    loop = VisionLoop(Blocked())
    result = await loop.decide(
        "pi", frame().model_copy(update={"deadline_ms": 50}), b"x"
    )
    assert entered.is_set()
    assert result["action"] == "stop"
    assert loop.status()["busy"]
    with pytest.raises(RiderError):
        await loop.decide("pi", frame(2), b"x")
    release.set()
    await loop.close()
    assert loop.busy is None


async def test_detector_error_is_unknown_stop():
    class Failed(Detector):
        def detect(self, *_):
            raise ValueError("model error")

    loop = VisionLoop(Failed())
    assert (await loop.decide("pi", frame(), b"x"))["state"] == "unknown"


async def test_clear_sequence_expires_and_run_cache_bounded():
    now = [0.0]
    loop = VisionLoop(Detector(), clock=lambda: now[0])
    await loop.decide("pi", frame(), b"x")
    now[0] = 1.0
    assert (await loop.decide("pi", frame(2), b"x"))["clear_count"] == 1
    for i in range(35):
        await loop.decide(
            "pi", frame().model_copy(update={"run_id": "run" + str(i)}), b"x"
        )
    assert len(loop.runs) == 32


def test_model_loading_hash_and_output_validation(tmp_path, monkeypatch):
    import hashlib
    import sys
    from types import SimpleNamespace

    model = tmp_path / "model.onnx"
    model.write_bytes(b"fixture")
    detector = FloorDetector()
    detector.path = model
    detector.expected = "a" * 64
    with pytest.raises(ValueError, match="hash_mismatch"):
        detector._load()
    detector.expected = hashlib.sha256(b"fixture").hexdigest()

    class Session:
        def __init__(self, *args, **kwargs):
            self.inputs = [SimpleNamespace(name="pixels")]

        def get_inputs(self):
            return self.inputs

        def run(self, _, data):
            assert next(iter(data.values())).shape == (1, 3, 512, 512)
            logits = np.zeros((1, 150, 8, 8), dtype=np.float32)
            logits[:, 3] = 20
            return [logits]

    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(
            SessionOptions=lambda: SimpleNamespace(), InferenceSession=Session
        ),
    )
    detector._load()
    assert detector.status()["loaded"]
    detector._load()
    buffer = io.BytesIO()
    Image.fromarray(
        np.random.default_rng(42).integers(30, 220, (64, 64, 3), dtype=np.uint8)
    ).save(buffer, format="PNG")
    assert detector.detect(buffer.getvalue(), (0, 0, 1, 1))["state"] == "clear"
    bad = FloorDetector()
    bad.path = model
    bad.expected = detector.expected

    class Wrong(Session):
        def get_inputs(self):
            return []

    sys.modules["onnxruntime"].InferenceSession = Wrong
    with pytest.raises(ValueError, match="model_format"):
        bad._load()
    for payload in (b"", b"x" * (512 * 1024 + 1)):
        with pytest.raises(ValueError):
            detector.detect(payload, (0, 0, 1, 1))
    tiny = io.BytesIO()
    Image.new("RGB", (8, 8)).save(tiny, format="PNG")
    with pytest.raises(ValueError):
        detector.detect(tiny.getvalue(), (0, 0, 1, 1))


async def test_close_during_request_cannot_return_go():
    import threading

    release = threading.Event()

    class Blocked(Detector):
        def detect(self, *args):
            release.wait(1)
            return super().detect(*args)

    loop = VisionLoop(Blocked())
    pending = asyncio.create_task(loop.decide("pi", frame(), b"x"))
    await asyncio.sleep(0.01)
    closer = asyncio.create_task(loop.close())
    release.set()
    with pytest.raises(RiderError):
        await pending
    await closer


def test_vision_http_lane_auth_origin_and_metadata(monkeypatch):
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from venom_module_rider.api.routes import router
    from venom_module_rider.config import Device
    from venom_module_rider.service import RiderService

    monkeypatch.setenv("RIDER_COMPUTE_TOKEN", "fixture-token")
    app = FastAPI()
    device = Device("pi", "Fixture", "http://127.0.0.1:18880", "demo")
    app.state.rider = RiderService((device,))
    app.state.rider_vision = VisionLoop(Detector())
    app.include_router(router)
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer fixture-token",
        "X-Rider-Device": "pi",
        "Content-Type": "image/jpeg",
        "X-Rider-Vision": frame().model_dump_json(),
    }
    result = client.post(
        "/api/v1/rider/compute/vision/decision", content=b"fixture", headers=headers
    )
    assert result.status_code == 200
    assert result.json()["action"] == "stop"
    assert (
        client.get("/api/v1/rider/compute/vision/status", headers=headers).json()[
            "protocol_version"
        ]
        == 1
    )
    assert (
        client.post("/api/v1/rider/compute/vision/decision", content=b"x").status_code
        == 403
    )
    for changes, expected in [
        ({"Origin": "http://evil.invalid"}, 403),
        ({"Content-Type": "text/plain"}, 415),
        ({"X-Rider-Vision": "not-json"}, 400),
        ({"X-Rider-Vision": "x" * 4097}, 400),
        (
            {"X-Rider-Vision": json.dumps({**frame().model_dump(), "sequence": True})},
            400,
        ),
    ]:
        response = client.post(
            "/api/v1/rider/compute/vision/decision",
            content=b"x",
            headers={**headers, **changes},
        )
        assert response.status_code == expected
    response = client.post(
        "/api/v1/rider/compute/vision/decision",
        content=b"x" * (512 * 1024 + 1),
        headers={**headers, "X-Rider-Vision": frame(2).model_dump_json()},
    )
    assert response.status_code == 413


async def test_navigation_config_proxies_selected_pi():
    from types import SimpleNamespace

    from venom_module_rider.api.vision_routes import configuration, configure

    calls = []

    class Domain:
        async def read(self, path, ttl, force=False):
            calls.append(("read", path, force))
            return {"data": {"revision": 1, "mode": "observe"}}

        async def mutate(self, path, body, session):
            calls.append(("write", path, body, session))
            return {"data": {"revision": 2}}

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(rider=Domain(), rider_vision=VisionLoop(Detector()))
        ),
        headers={"X-Rider-Session": "session"},
    )

    async def body():
        return {"revision": 1, "values": {"speed": 0.1}}

    request.json = body
    assert (await configuration(request))["config"]["mode"] == "observe"
    assert (await configure(request))["data"]["revision"] == 2
    assert calls[0] == ("read", "/api/navigation/config", True)
    assert calls[1] == ("read", "/api/navigation/status", True)
    assert calls[2] == ("read", "/api/logic/state", True)
    assert calls[3] == ("read", "/api/logic/catalog", True)
    assert calls[4][1] == "/api/navigation/config"


async def test_navigation_configuration_reports_runtime_readiness():
    from types import SimpleNamespace

    from venom_module_rider.api.vision_routes import configuration, navigation_state

    reason = "navigation_executor_update_required"

    class Domain:
        async def read(self, path, *args, **kwargs):
            if path.endswith("catalog"):
                return {
                    "data": {
                        "features": [
                            {
                                "name": "s15_venom_perception",
                                "installed": True,
                                "start_available": True,
                            },
                            {
                                "name": "s14_venom_navigation",
                                "installed": True,
                                "start_available": False,
                                "start_unavailable_reason": reason,
                            },
                        ]
                    }
                }
            if path.endswith("config"):
                return {"data": {"mode": "observe", "revision": 0}}
            return {"data": {"running": True, "stale": False}}

    req = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(rider=Domain(), rider_vision=VisionLoop(Detector()))
        )
    )
    row = await configuration(req)
    assert row["runtime"] == {
        "observe": True,
        "drive": False,
        "known": True,
        "drive_reason": "navigation_executor_update_required",
    }
    assert (await navigation_state(req))["status"]["running"] is True


async def test_old_pi_configuration_returns_specific_error():
    from types import SimpleNamespace

    from venom_module_rider.api.vision_routes import configuration

    class Domain:
        async def read(self, *args, **kwargs):
            raise RiderError("not_found", 404)

    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(rider=Domain())))
    with pytest.raises(RiderError) as error:
        await configuration(req)
    assert (
        error.value.code == "navigation_api_unavailable" and error.value.status == 503
    )


@pytest.mark.parametrize(
    "features",
    [
        {
            "s15_venom_perception": {"enabled": True},
            "s14_venom_navigation": {"enabled": False},
        },
        [
            {"name": "s15_venom_perception", "requested": True},
            {"name": "s14_venom_navigation", "requested": False},
        ],
    ],
)
async def test_loop_state_handles_real_pi_rows_and_fixture_map(features):
    from venom_module_rider.api.vision_routes import loop_goals

    class Domain:
        async def read(self, *args, **kwargs):
            return {"data": {"state": {"features": features}}}

    assert await loop_goals(Domain()) == {"observe": True, "drive": False}


async def test_stale_or_missing_goal_observation_is_never_a_confirmed_stop():
    from venom_module_rider.api.vision_routes import loop_goals

    class Domain:
        async def read(self, *args, **kwargs):
            return {
                "data": {
                    "state": {
                        "features": [
                            {
                                "name": "s15_venom_perception",
                                "requested": False,
                                "stale": True,
                            }
                        ]
                    }
                }
            }

    assert await loop_goals(Domain()) == {}
