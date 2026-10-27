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

    monkeypatch.setattr(
        "venom_module_rider.cuda_execution.require_cuda", lambda: "cuda"
    )
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

        def get_providers(self):
            return ["CUDAExecutionProvider"]

        def disable_fallback(self):
            pass

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
            get_available_providers=lambda: ["CUDAExecutionProvider"],
            preload_dlls=lambda: None,
            SessionOptions=lambda: SimpleNamespace(
                add_session_config_entry=lambda *args: None
            ),
            InferenceSession=Session,
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
    device = Device("pi", "Fixture", "http://127.0.0.1", "demo")
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


async def test_observation_separates_image_go_from_motion_stop():
    loop = VisionLoop(Detector())
    for seq in range(1, 4):
        f = frame(seq).model_copy(update={"mode": "observe"})
        result = await loop.decide("pi", f, b"x")
    assert result["vision_action"] == "go"
    assert result["action"] == "stop"
    assert result["motion_reason"] == "observe_only"


async def test_motion_diagnostic_reason_is_not_just_the_model_reason():
    loop = VisionLoop(Detector())
    first = await loop.decide("pi", frame(), b"x")
    assert first["vision_action"] == "stop"
    assert first["motion_reason"] == "clear_frames_pending"
    for seq in range(2, 4):
        result = await loop.decide("pi", frame(seq), b"x")
    assert result["motion_reason"] == "clear_frames_ready"
    loop.detector.state = "unknown"
    result = await loop.decide("pi", frame(4), b"x")
    assert result["vision_action"] == result["action"] == "stop"
    assert result["motion_reason"] == "unknown"


@pytest.mark.parametrize("label", [6, 11, 28])
def test_ground_like_classes_are_reported_but_never_silently_allow_go(label):
    logits = np.zeros((1, 150, 8, 8), dtype=np.float32)
    logits[:, label] = 20
    result = classify_logits(logits, (0, 0, 1, 1))
    assert result["state"] == "obstacle"
    assert result["floor_fraction"] == result["floor_candidate_fraction"] == 0
    assert result["dominant_class_id"] == label
    assert (
        result["dominant_class_fraction"] == result["unsupported_ground_fraction"] == 1
    )
    assert result["dominant_class_confidence"] > 0.99


def test_low_confidence_floor_label_is_not_current_clearance():
    logits = np.zeros((1, 150, 8, 8), dtype=np.float32)
    logits[:, 3] = 1
    result = classify_logits(logits, (0, 0, 1, 1))
    assert result["state"] == "unknown"
    assert result["floor_candidate_fraction"] == 1
    assert result["floor_fraction"] == 0
    assert result["dominant_class_id"] == 3
    assert result["confidence_fraction"] == 0


def test_tiny_uncertain_region_is_not_silently_promoted_to_clearance():
    # A small uncertain region can be the only evidence of an obstacle.
    # Do not promote it to floor without validating the perception change.
    logits = np.zeros((1, 150, 20, 20), dtype=np.float32)
    logits[:, 3] = 20
    logits[:, :, 0, 0] = 0
    result = classify_logits(logits, (0, 0, 1, 1))
    assert result["confidence_fraction"] > 0.99
    assert result["floor_fraction"] > 0.99
    assert result["state"] != "clear"


def test_depth_geometry_free_plane_box_and_invalid_output():
    from venom_module_rider.depth_detector import PerceptionSettings, assess_depth

    settings = PerceptionSettings()
    y, x = np.mgrid[0:120, 0:160]
    depth = 1.0 / np.maximum(0.05, 0.2 + 3 * (y / 120 - 0.55))
    assert assess_depth(depth, (0.35, 0.65, 0.65, 0.95), settings)["state"] == "clear"
    depth[78:114, 56:104] = 0.3
    result = assess_depth(depth, (0.35, 0.65, 0.65, 0.95), settings)
    assert result["state"] == "unknown"
    assert result["drive_supported"] is True
    depth[85:95, 65:80] = 0.1
    assert assess_depth(depth, (0.35, 0.65, 0.65, 0.95), settings)["state"] != "clear"
    for bad in (np.zeros((40, 40)), np.ones((8, 8)), np.full((40, 40), np.nan)):
        with pytest.raises(ValueError):
            assess_depth(bad, (0, 0, 1, 1), settings)
    with pytest.raises(ValueError):
        assess_depth(depth, (1, 0, 0, 1), settings)


async def test_depth_drive_permit_requires_three_frames_and_matching_receipt():
    from venom_module_rider.depth_detector import PerceptionSettings

    class Geometry(Detector):
        def detect(self, *args):
            return {
                "state": "clear",
                "reason": "depth_plane_candidate",
                "drive_supported": True,
            }

    loop = VisionLoop(Detector())
    loop.depth_detector = Geometry()
    for seq in range(1, 4):
        f = frame(seq).model_copy(
            update={"perception": PerceptionSettings(engine="depth_plane")}
        )
        result = await loop.decide("pi", f, b"x")
    assert result["vision_action"] == "go"
    assert result["action"] == "go"
    assert result["motion_reason"] == "clear_frames_ready"
    assert result["drive_supported"] is True


async def test_perception_changes_reset_run_admission():
    from venom_module_rider.depth_detector import PerceptionSettings

    loop = VisionLoop(Detector())
    await loop.decide("pi", frame(), b"x")
    changed = frame(2).model_copy(
        update={"perception": PerceptionSettings(residual_limit=0.08)}
    )
    with pytest.raises(RiderError):
        await loop.decide("pi", changed, b"x")


def test_depth_settings_reject_unknown_engine_nan_and_unsafe_thresholds():
    from pydantic import ValidationError

    from venom_module_rider.depth_detector import PerceptionSettings

    for patch in (
        {"engine": "llm"},
        {"residual_limit": float("nan")},
        {"outlier_limit": 0.5},
        {"slope_min": True},
        {"unexpected": 1},
    ):
        with pytest.raises(ValidationError):
            PerceptionSettings(**patch)


def test_depth_detector_local_manifest_lazy_load_preview_and_bounds(
    tmp_path, monkeypatch
):
    import hashlib
    import json
    import sys
    from contextlib import nullcontext
    from types import SimpleNamespace

    from venom_module_rider.depth_detector import DepthDetector, PerceptionSettings

    files = {}
    for name in ("config.json", "preprocessor_config.json", "model.safetensors"):
        (tmp_path / name).write_bytes(b"fixture")
        files[name] = hashlib.sha256(b"fixture").hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps({"files": files}))
    detector = DepthDetector()
    detector.path = tmp_path
    assert detector.status()["configured"] and not detector.status()["loaded"]
    y, x = np.mgrid[0:64, 0:64]
    depth = 1 / np.maximum(0.05, 0.2 + 3 * (y / 64 - 0.55))

    class Tensor:
        def float(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return depth

    class Inputs(dict):
        def to(self, device):
            return self

    class Processor:
        @classmethod
        def from_pretrained(cls, path, local_files_only):
            assert local_files_only
            return cls()

        def __call__(self, **kwargs):
            return Inputs()

        def post_process_depth_estimation(self, *args, **kwargs):
            return [{"predicted_depth": Tensor()}]

    class Model:
        @classmethod
        def from_pretrained(cls, path, local_files_only):
            assert local_files_only
            return cls()

        def eval(self):
            return self

        def to(self, device):
            return self

        def __call__(self, **kwargs):
            return object()

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: True), inference_mode=nullcontext
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoImageProcessor=Processor, AutoModelForDepthEstimation=Model
        ),
    )
    detector._load()
    detector._load()
    assert detector.status()["loaded"] and len(detector.digest) == 64
    image = io.BytesIO()
    Image.fromarray(
        np.random.default_rng(42).integers(30, 220, (64, 64, 3), dtype=np.uint8)
    ).save(image, format="PNG")
    result = detector.detect(
        image.getvalue(), (0.35, 0.65, 0.65, 0.95), PerceptionSettings()
    )
    assert result["state"] == "clear" and result["_preview"].startswith(b"\xff\xd8")
    dark = io.BytesIO()
    Image.new("RGB", (64, 64)).save(dark, format="PNG")
    assert (
        detector.detect(dark.getvalue(), (0, 0, 1, 1), PerceptionSettings())["state"]
        == "unknown"
    )
    for payload in (b"", b"x" * (512 * 1024 + 1)):
        with pytest.raises(ValueError):
            detector.detect(payload, (0, 0, 1, 1), PerceptionSettings())
    tiny = io.BytesIO()
    Image.new("RGB", (8, 8)).save(tiny, format="PNG")
    with pytest.raises(ValueError):
        detector.detect(tiny.getvalue(), (0, 0, 1, 1), PerceptionSettings())
    broken = DepthDetector()
    broken.path = tmp_path
    (tmp_path / "model.safetensors").write_bytes(b"wrong")
    with pytest.raises(ValueError, match="hash_mismatch"):
        broken._load()
    (tmp_path / "manifest.json").write_text(json.dumps({"files": {}}))
    with pytest.raises(ValueError, match="manifest"):
        broken._load()


def test_depth_ground_deviation_threshold_and_corridor_bounds():
    from venom_module_rider.depth_detector import PerceptionSettings, assess_depth

    y, x = np.mgrid[0:120, 0:160]
    depth = 1 / np.maximum(0.05, 0.2 + 3 * (y / 120 - 0.55))
    depth[90:94, 70:74] = 0.1
    result = assess_depth(depth, (0.35, 0.65, 0.65, 0.95), PerceptionSettings())
    assert result["state"] == "obstacle"
    assert result["outlier_fraction"] > result["outlier_limit"]
    with pytest.raises(ValueError):
        assess_depth(depth, (0, 0, 0.001, 0.001), PerceptionSettings())


async def test_geometry_observation_cannot_take_segmentation_slot():
    from venom_module_rider.depth_detector import PerceptionSettings

    loop = VisionLoop(Detector())
    loop.depth_busy = asyncio.create_task(asyncio.sleep(0.05))
    result = await loop.decide("prod", frame(), b"x")
    assert result["state"] == "clear"
    assert loop.status()["busy"] is False
    assert loop.status({"engine": "depth_plane"})["busy"] is True
    with pytest.raises(RiderError):
        await loop.decide(
            "demo",
            frame().model_copy(
                update={"perception": PerceptionSettings(engine="depth_plane")}
            ),
            b"x",
        )
    await loop.close()


def test_uncertain_floor_is_not_reported_as_a_different_object_class():
    logits = np.zeros((1, 150, 20, 20), dtype=np.float32)
    logits[:, 3] = 20
    logits[:, :, 0, 0] = 0
    result = classify_logits(logits, (0, 0, 1, 1))
    assert result["state"] == "unknown"
    assert result["reason"] == "vision_floor_uncertainty"
    assert result["uncertain_pixels"] == 1
    assert result["non_floor_pixels"] == 0
    assert result["corridor_pixels"] == 400


async def test_real_pi_has_priority_over_same_engine_demo_admission():
    now = [0.0]
    loop = VisionLoop(Detector(), clock=lambda: now[0])
    await loop.decide("prod", frame(), b"x")
    with pytest.raises(RiderError, match="vision_production_priority"):
        await loop.decide("demo", frame(), b"x")
    now[0] = 0.6
    assert (await loop.decide("demo", frame(), b"x"))["state"] == "clear"
    await loop.close()


def test_rejected_dark_capture_replaces_previous_preview_without_model_load():
    """Publish the actual rejected frame with separate geometry and no model load."""
    from venom_module_rider.depth_detector import DepthDetector, PerceptionSettings

    detector = DepthDetector()
    payload = io.BytesIO()
    Image.new("RGB", (64, 64), (0, 0, 0)).save(payload, format="JPEG")
    result = detector.detect(
        payload.getvalue(), (0, 0, 1, 1), PerceptionSettings(engine="depth_plane")
    )
    assert result["state"] == "unknown"
    assert detector.model is None
    pixels = np.asarray(Image.open(io.BytesIO(result["_preview"])))
    # Source stays dark. Geometry now travels separately for vector drawing.
    assert pixels[24:48, 24:48].max() == 0
    assert pixels.max() == 0
    assert result["overlay_geometry"]["input_bounds"] == [0, 0, 64, 64]
    assert all(row["state"] == "unknown" for row in result["sectors"].values())


@pytest.mark.parametrize(
    "field,value",
    [
        ("verified", False),
        ("mode", "observe"),
        ("model_sha256", "b" * 64),
        ("calibration_id", ""),
    ],
)
async def test_depth_denies_execution_without_matching_operator_acceptance(
    field, value
):
    from venom_module_rider.depth_detector import PerceptionSettings

    loop = VisionLoop(Detector())
    loop.depth_detector = Detector()
    for seq in range(1, 4):
        f = frame(seq).model_copy(
            update={
                "perception": PerceptionSettings(engine="depth_plane"),
                field: value,
            }
        )
        result = await loop.decide("pi", f, b"x")
    assert result["vision_action"] == "go"
    assert result["action"] == "stop"
    await loop.close()


async def test_exact_depth_trace_is_bound_to_admitted_frame_and_never_serialized(
    monkeypatch,
):
    from venom_module_rider.depth_detector import PerceptionSettings

    depth = np.ones((32, 32))

    class TracedDetector(Detector):
        def detect(self, *_):
            return {
                **super().detect(),
                "_trace_depth": depth,
                "model_sha256": "a" * 64,
                "_preview": b"jpeg",
            }

    recorded = []

    def capture(*args):
        recorded.append(args)
        return "queued-trace"

    monkeypatch.setattr("venom_module_rider.depth_trace.submit", capture)
    loop = VisionLoop(Detector())
    loop.depth_detector = TracedDetector()
    admitted = frame().model_copy(
        update={"perception": PerceptionSettings(engine="depth_plane")}
    )
    result = await loop.decide("pi", admitted, b"exact-input")
    payload, captured, assessment, corridor, settings = recorded[0]
    assert payload == b"exact-input"
    assert captured is depth
    assert assessment["run_id"] == admitted.run_id
    assert assessment["frame_id"] == admitted.frame_id
    assert assessment["sequence"] == admitted.sequence
    assert assessment["config_revision"] == admitted.config_revision
    assert assessment["model_sha256"] == "a" * 64
    assert not any(key.startswith("_") for key in assessment)
    assert corridor == admitted.corridor
    assert settings is admitted.perception
    assert result["depth_trace_id"] == "queued-trace"
    assert "_trace_depth" not in result
    assert "_trace_depth" not in loop.preview("pi")[2]
    assert result["action"] == "stop"  # still only one CLEAR
    await loop.close()


async def test_trace_storage_failure_cannot_change_go_stop(monkeypatch):
    from venom_module_rider.depth_detector import PerceptionSettings

    class TracedDetector(Detector):
        def detect(self, *_):
            return {**super().detect(), "_trace_depth": np.ones((32, 32))}

    def fail(*_):
        raise OSError("unwritable diagnostic directory")

    monkeypatch.setattr("venom_module_rider.depth_trace.submit", fail)
    loop = VisionLoop(Detector())
    loop.depth_detector = TracedDetector()
    for seq in range(1, 4):
        admitted = frame(seq).model_copy(
            update={"perception": PerceptionSettings(engine="depth_plane")}
        )
        result = await loop.decide("pi", admitted, b"exact-input")
    assert result["action"] == "go"
    assert "depth_trace_id" not in result
    assert "_trace_depth" not in result
    await loop.close()


async def test_reply_can_exceed_old_200ms_ceiling_but_keeps_frame_budget():
    loop = VisionLoop(Detector())
    try:
        for seq in range(1, 4):
            result = await loop.decide(
                "pi", frame(seq).model_copy(update={"deadline_ms": 500}), b"x"
            )
        assert result["action"] == "go"
        assert 200 < result["valid_for_ms"] <= 500
        result = await loop.decide(
            "pi",
            frame(4).model_copy(update={"deadline_ms": 500, "age_ms": 350.0}),
            b"x",
        )
        assert 0 < result["valid_for_ms"] <= 150
    finally:
        await loop.close()
