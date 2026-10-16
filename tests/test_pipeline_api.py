"""Production-shaped two-panel/native-reference flows use an isolated Pi fixture."""

import asyncio
import copy
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_stream_contract import jpeg, native

from venom_module_rider.api.routes import router
from venom_module_rider.config import Device
from venom_module_rider.errors import RiderError
from venom_module_rider.service import RiderService
from venom_module_rider.vision_contract import VisionFrame

AUTH = {"X-Rider-Device": "pi", "Authorization": "Bearer fixture-compute"}
PROFILE = {
    "analysis_location": "venom",
    "local_engine": "canny",
    "remote_transport": "mjpeg",
}


class PiFixture:
    def __init__(self, device):
        self.device = device
        self.pipeline = {
            "contract_id": "rider.camera_pipeline",
            "contract_version": 1,
            "ok": True,
            "revision": 3,
            "profile": PROFILE.copy(),
            "active_profile": PROFILE.copy(),
            "state": "active",
            "operation_id": None,
            "transport_epoch": 2,
            "camera_session": "session",
            "capabilities": {
                "analysis_locations": ["local", "venom"],
                "local_engines": ["canny"],
                "remote_transports": ["snapshots", "mjpeg"],
                "stream_drive_supported": False,
                "local_drive_supported": False,
            },
            "consumers": [],
            "effective_stages": {},
            "latest_frame": None,
            "error": None,
        }
        self.calls = []
        self.opened = self.ended = 0
        self.frames = asyncio.Queue()
        self.supported = True
        self.running = False
        self.latest = None
        self.snapshot_requests = 0

    async def json(self, path, *, method="GET", body=None, **kwargs):
        self.calls.append((method, path, copy.deepcopy(body)))
        requested_at = time.monotonic()
        if path == "/api/resource/camera/pipeline":
            if not self.supported:
                raise RiderError("not_found", 404)
            if method == "POST":
                if body["expected_revision"] != self.pipeline["revision"]:
                    raise RiderError(
                        "camera_pipeline_revision_conflict",
                        409,
                        {"current_revision": self.pipeline["revision"]},
                    )
                if body["action"] == "configure":
                    self.pipeline["profile"] = body["profile"]
                    self.pipeline["revision"] += 1
                    self.pipeline.update(
                        active_profile=None, state="stopped", camera_session=None
                    )
                elif body["action"] == "stop":
                    self.pipeline.update(state="stopped", active_profile=None)
                self.pipeline["operation_id"] = body["request_id"]
            if self.pipeline["state"] in {"active", "waiting_frame"}:
                self.latest = self.latest or native(1)
                metadata = self.latest.source.model_dump(mode="json")
                metadata["frame_age_ms"] = max(
                    0, (time.monotonic_ns() - metadata["captured_monotonic_ns"]) / 1e6
                )
                self.pipeline["latest_frame"] = metadata
            else:
                self.latest = None
                self.pipeline["latest_frame"] = None
            return {
                "data": copy.deepcopy(self.pipeline),
                "_rider_request_started_mono": requested_at,
                "_rider_response_received_mono": time.monotonic(),
            }
        if path == "/api/navigation/config":
            return {
                "data": {
                    "corridor": [0.35, 0.8, 0.65, 0.95],
                    "revision": 4,
                    "perception": {"engine": "segformer"},
                }
            }
        if path == "/api/navigation/status":
            return {
                "data": {"running": self.running, "stale": False, "run_id": "pi-run"}
            }
        if path == "/api/logic/state":
            return {
                "data": {
                    "features": {
                        "s15_venom_perception": {"requested": self.running},
                        "s14_venom_navigation": {"requested": False},
                    }
                }
            }
        if path == "/api/control/state":
            return {"data": {"safety": {"state": "DISARMED", "moving": False}}}
        raise RiderError("not_found", 404)

    async def image(self, *args, **kwargs):
        self.snapshot_requests += 1
        raise AssertionError("Native frames must not re-enter the snapshot/file path")

    async def mjpeg(self):
        self.opened += 1
        try:
            self.latest = self.latest or native(1)
            yield self.latest
            while True:
                frame = await self.frames.get()
                yield frame
        finally:
            self.ended += 1

    async def emit(self, sequence):
        frame = native(sequence)
        self.latest = frame
        await self.frames.put(frame)
        await asyncio.sleep(0.01)
        return frame.source.reference().model_dump(mode="json")

    async def close(self):
        pass


class Detector:
    def __init__(self):
        self.calls = 0
        self.inputs = []

    def status(self):
        return {"sha256": "a" * 64}

    def detect(self, payload, corridor):
        self.calls += 1
        self.inputs.append(payload)
        return {"state": "clear", "reason": "floor_candidate", "_preview": jpeg()}


def fixture(monkeypatch):
    monkeypatch.setenv("RIDER_COMPUTE_TOKEN", "fixture-compute")
    device = Device("pi", "Pi fixture", "http://127.0.0.1:18880", "device")
    instances = []

    def factory(device):
        pi = PiFixture(device)
        instances.append(pi)
        return pi

    service = RiderService((device,), client_factory=factory)
    asyncio.run(service.select("pi", service.session))
    app = FastAPI()
    app.state.rider = service
    app.include_router(router)
    return app, service, instances[0]


def descriptor(source, sequence=1, *, mode="observe", **updates):
    frame = VisionFrame(
        protocol_version=1,
        run_id="pi-run",
        frame_id=source["frame_id"],
        sequence=sequence,
        config_revision=4,
        age_ms=5.0,
        deadline_ms=300,
        mode=mode,
        verified=mode == "drive",
        calibration_id="fixture",
        model_sha256="a" * 64,
    ).model_dump(mode="json")
    return {"frame": {**frame, **updates}, "source": source}


def warmup(client):
    response = client.get(
        "/api/v1/rider/compute/vision/status?stream=true", headers=AUTH
    )
    assert response.status_code == 200, response.text
    state = response.json()["stream"]
    assert state["ready"] is True
    return {
        key: state["latest_source"][key]
        for key in (
            "camera_session",
            "sequence",
            "transport_epoch",
            "pipeline_revision",
            "frame_id",
            "sha256",
        )
    }


def test_raw_preview_and_two_panels_share_reader_without_model_or_legacy_file(
    monkeypatch,
):
    app, service, pi = fixture(monkeypatch)
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        current = warmup(client)
        first = client.get("/api/v1/rider/camera/raw")
        second = client.get("/api/v1/rider/camera/raw")
        assert first.status_code == second.status_code == 200
        assert first.content == second.content == jpeg()
        assert first.headers["x-rider-frame-id"] == current["frame_id"]
        assert first.headers["x-rider-source"] == "pi-camera"
        assert pi.opened == 1 and pi.snapshot_requests == 0 and detector.calls == 0
        assert all(method == "GET" for method, _, _ in pi.calls)
    assert pi.ended == 1


def test_preview_then_pi_reference_uses_one_inference_but_keeps_run_gates(monkeypatch):
    app, service, pi = fixture(monkeypatch)
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        current = warmup(client)
        preview = client.get("/api/v1/rider/camera/venom")
        assert preview.status_code == 200, preview.text
        assert preview.headers["x-rider-frame-id"] == current["frame_id"]
        assert preview.headers["x-rider-vision-action"] == "stop"
        assert detector.calls == 1
        response = client.post(
            "/api/v1/rider/compute/vision/decision-reference",
            headers=AUTH,
            json=descriptor(current, mode="drive"),
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["source"] == current
        assert result["action"] == "stop" and result["clear_count"] == 1
        assert detector.calls == 1 and pi.opened == 1
        duplicate = client.post(
            "/api/v1/rider/compute/vision/decision-reference",
            headers=AUTH,
            json=descriptor(current, mode="drive"),
        )
        assert duplicate.status_code == 409
        for sequence in (2, 3):
            current = client.portal.call(pi.emit, sequence)
            response = client.post(
                "/api/v1/rider/compute/vision/decision-reference",
                headers=AUTH,
                json=descriptor(current, sequence, mode="drive"),
            )
            assert response.status_code == 200, response.text
        assert response.json()["action"] == "go"  # Fixture decision, no actuator path.
        assert detector.calls == 3
        assert not any(method == "POST" for method, _, _ in pi.calls)
        assert pi.snapshot_requests == 0


def test_reference_is_exact_and_expiry_epoch_hash_errors_never_infer(monkeypatch):
    app, service, pi = fixture(monkeypatch)
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        current = warmup(client)
        endpoint = "/api/v1/rider/compute/vision/decision-reference"
        malformed = client.post(endpoint, headers=AUTH, json={"frame": {}})
        assert malformed.status_code == 400
        bad_type = client.post(
            endpoint, headers={**AUTH, "Content-Type": "image/jpeg"}, content=b"x"
        )
        assert bad_type.status_code == 415
        old_epoch = {**current, "transport_epoch": 1}
        old = client.post(endpoint, headers=AUTH, json=descriptor(old_epoch))
        assert (
            old.status_code == 409
            and old.json()["detail"]["code"] == "stream_epoch_changed"
        )
        expired = client.post(
            endpoint, headers=AUTH, json=descriptor(current, age_ms=300.0)
        )
        assert expired.status_code == 409
        altered = {**current, "sha256": "b" * 64}
        missing = client.post(
            endpoint, headers=AUTH, json=descriptor(altered, deadline_ms=50)
        )
        assert missing.status_code == 409
        assert missing.json()["detail"]["code"] == "stream_frame_unavailable"
        assert detector.calls == 0 and pi.snapshot_requests == 0
        denied = client.post(endpoint, json=descriptor(current))
        assert denied.status_code == 403


def test_profile_proxy_preserves_pi_authority_and_old_device_capability(monkeypatch):
    app, service, pi = fixture(monkeypatch)
    with TestClient(app) as client:
        status = client.get("/api/v1/rider/camera/pipeline").json()
        assert status["supported"] is True and status["revision"] == 3
        source = warmup(client)
        connection = app.state.rider_stream.connection
        result = client.post(
            "/api/v1/rider/camera/pipeline",
            headers={"X-Rider-Session": service.session},
            json={
                "contract_version": 1,
                "action": "configure",
                "request_id": "configure-1",
                "expected_revision": 3,
                "profile": {**PROFILE, "remote_transport": "snapshots"},
            },
        )
        assert result.status_code == 200, result.text
        assert result.json()["revision"] == 4
        assert connection.closed and app.state.rider_stream.connection is None
        conflict = client.post(
            "/api/v1/rider/camera/pipeline",
            headers={"X-Rider-Session": service.session},
            json={
                "contract_version": 1,
                "action": "stop",
                "request_id": "stop-old",
                "expected_revision": 3,
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["details"]["current_revision"] == 4
        pi.supported = False
        service.cache.clear()
        unsupported = client.get("/api/v1/rider/camera/pipeline").json()
        assert unsupported["supported"] is False
        assert unsupported["capabilities"]["remote_transports"] == ["snapshots"]
        assert pi.snapshot_requests == 0
        assert source["pipeline_revision"] == 3


def test_local_analysis_never_schedules_venom_model(monkeypatch):
    app, service, pi = fixture(monkeypatch)
    pi.pipeline["profile"]["analysis_location"] = "local"
    pi.pipeline["active_profile"]["analysis_location"] = "local"
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        current = warmup(client)  # Explicit RAW stream is allowed.
        response = client.get("/api/v1/rider/camera/venom")
        assert response.status_code == 409
        reference = client.post(
            "/api/v1/rider/compute/vision/decision-reference",
            headers=AUTH,
            json=descriptor(current),
        )
        assert reference.status_code == 409
        assert reference.json()["detail"]["code"] == "vision_remote_disabled"
        assert detector.calls == 0


@pytest.mark.parametrize(
    "body",
    [
        {
            "contract_version": 1,
            "request_id": "x",
            "expected_revision": 3,
            "action": "configure",
        },
        {
            "contract_version": 1,
            "request_id": "x",
            "expected_revision": 3,
            "action": "start",
            "profile": PROFILE,
        },
        {
            "contract_version": True,
            "request_id": "x",
            "expected_revision": 3,
            "action": "start",
        },
        {
            "contract_version": 1,
            "request_id": "x",
            "expected_revision": True,
            "action": "stop",
        },
    ],
)
def test_invalid_profile_operations_never_reach_pi(monkeypatch, body):
    app, service, pi = fixture(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/rider/camera/pipeline",
            headers={"X-Rider-Session": service.session},
            json=body,
        )
        assert response.status_code == 400, response.text
        assert not any(method == "POST" for method, _path, _body in pi.calls)


def test_shared_wire_examples_match_runtime_models():
    from pathlib import Path

    from venom_module_rider.api.pipeline_routes import PipelineAction, PipelineProfile
    from venom_module_rider.stream_contract import SourceDescription, SourceReference

    examples = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "config/contracts/camera_pipeline_v1.examples.json"
        ).read_text()
    )
    for operation in ("configure", "start", "stop"):
        assert PipelineAction.model_validate(examples[operation])
    assert PipelineProfile.model_validate(examples["profiles"][0])
    descriptor = SourceDescription.model_validate(examples["source"])
    assert descriptor.reference() == SourceReference.model_validate(
        examples["reference_source"]
    )


@pytest.mark.parametrize(
    "body",
    [
        {
            "contract_version": 1,
            "request_id": "null-profile",
            "expected_revision": 3,
            "action": "start",
            "profile": None,
        },
        {
            "contract_version": 1,
            "request_id": "null-profile",
            "expected_revision": 3,
            "action": "stop",
            "profile": None,
        },
        {
            "contract_version": 1,
            "request_id": "zero-revision",
            "expected_revision": 0,
            "action": "start",
        },
    ],
)
def test_null_profile_and_zero_revision_match_shared_schema(monkeypatch, body):
    app, service, pi = fixture(monkeypatch)
    with TestClient(app) as client:
        result = client.post(
            "/api/v1/rider/camera/pipeline",
            headers={"X-Rider-Session": service.session},
            json=body,
        )
        assert result.status_code == 400
        assert not any(method == "POST" for method, _path, _body in pi.calls)


def test_active_pi_result_is_the_only_processed_preview_without_second_inference(
    monkeypatch,
):
    app, service, pi = fixture(monkeypatch)
    pi.running = True
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        current = warmup(client)
        computed = client.post(
            "/api/v1/rider/compute/vision/decision-reference",
            headers=AUTH,
            json=descriptor(current),
        )
        assert computed.status_code == 200, computed.text
        preview = client.get("/api/v1/rider/camera/venom")
        assert preview.status_code == 200, preview.text
        assert preview.headers["x-rider-run-id"] == "pi-run"
        assert preview.headers["x-rider-frame-id"] == current["frame_id"]
        assert json.loads(preview.headers["x-rider-analysis-json"])["source"] == current
        assert detector.calls == 1 and pi.snapshot_requests == 0


def test_active_pi_without_a_current_result_never_creates_diagnostic_inference(
    monkeypatch,
):
    app, service, pi = fixture(monkeypatch)
    pi.running = True
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        warmup(client)
        preview = client.get("/api/v1/rider/camera/venom")
        assert preview.status_code == 429
        assert preview.json()["detail"]["code"] == "vision_busy"
        assert detector.calls == 0 and pi.snapshot_requests == 0


def test_empty_native_warmup_reports_not_ready_without_inference_or_legacy_fallback(
    monkeypatch,
):
    app, service, pi = fixture(monkeypatch)

    async def silent():
        await asyncio.Event().wait()
        yield

    pi.mjpeg = silent
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        response = client.get(
            "/api/v1/rider/compute/vision/status?stream=true", headers=AUTH
        )
        assert response.status_code == 200
        assert response.json()["stream"]["ready"] is False
        assert response.json()["stream"]["latest_source"] is None
        assert detector.calls == 0 and pi.snapshot_requests == 0


async def test_old_pipeline_capability_is_not_polled_per_camera_frame():
    from test_rider import domain

    service, pi = domain()
    await service.select("demo", service.session)
    pi.camera_on = True
    for _ in range(4):
        payload, _mime, _headers = await service.camera("raw")
        assert payload
    assert sum(row["path"] == "/api/resource/camera/pipeline" for row in pi.trace) == 1
    assert not (await service.pipeline(force=True))["supported"]
    assert sum(row["path"] == "/api/resource/camera/pipeline" for row in pi.trace) == 2
    await service.close()


def test_request_identity_pattern_matches_pi_and_contract(monkeypatch):
    app, service, pi = fixture(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/rider/camera/pipeline",
            headers={"X-Rider-Session": service.session},
            json={
                "contract_version": 1,
                "action": "stop",
                "request_id": "bad request!",
                "expected_revision": 3,
            },
        )
        assert response.status_code == 400
        assert not any(method == "POST" for method, _path, _body in pi.calls)


def test_delayed_binary_and_legacy_preview_cannot_restart_old_inference_after_switch(
    monkeypatch,
):
    app, service, pi = fixture(monkeypatch)
    snapshots = {**PROFILE, "remote_transport": "snapshots"}
    pi.pipeline.update(profile=snapshots, active_profile=snapshots)
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        current = native(1).source.reference().model_dump(mode="json")
        binary_headers = {
            **AUTH,
            "Content-Type": "image/jpeg",
            "X-Rider-Vision": json.dumps(descriptor(current)["frame"]),
        }
        before = client.post(
            "/api/v1/rider/compute/vision/decision",
            headers=binary_headers,
            content=jpeg(),
        )
        assert before.status_code == 200, before.text
        assert detector.calls == 1
        changed = client.post(
            "/api/v1/rider/camera/pipeline",
            headers={"X-Rider-Session": service.session},
            json={
                "contract_version": 1,
                "request_id": "native-switch",
                "expected_revision": 3,
                "action": "configure",
                "profile": PROFILE,
            },
        )
        assert changed.status_code == 200
        delayed = client.post(
            "/api/v1/rider/compute/vision/decision",
            headers=binary_headers,
            content=jpeg(),
        )
        assert delayed.status_code == 409
        assert delayed.json()["detail"]["code"] == "vision_transport_mismatch"
        legacy_preview = client.get("/api/v1/rider/camera/venom")
        assert legacy_preview.status_code == 503
        assert detector.calls == 1 and pi.snapshot_requests == 0


def test_local_profile_rejects_binary_vision_but_keeps_explicit_raw_consumer(
    monkeypatch,
):
    app, service, pi = fixture(monkeypatch)
    pi.pipeline["profile"]["analysis_location"] = "local"
    pi.pipeline["active_profile"]["analysis_location"] = "local"
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        current = warmup(client)
        delayed = client.post(
            "/api/v1/rider/compute/vision/decision",
            headers={
                **AUTH,
                "Content-Type": "image/jpeg",
                "X-Rider-Vision": json.dumps(descriptor(current)["frame"]),
            },
            content=jpeg(),
        )
        assert delayed.status_code == 409
        assert delayed.json()["detail"]["code"] == "vision_remote_disabled"
        preview = client.get("/api/v1/rider/camera/venom")
        assert preview.status_code == 409
        raw = client.get("/api/v1/rider/camera/raw")  # S12/manual explicit source.
        assert raw.status_code == 200 and raw.content == jpeg()
        assert detector.calls == 0 and pi.snapshot_requests == 0


def test_network_delayed_part_is_stale_despite_small_send_age_and_never_infers(
    monkeypatch,
):
    from venom_module_rider.stream_contract import NativeFrame

    app, service, pi = fixture(monkeypatch)
    pi.latest = native(2)
    delayed = native(1)
    descriptor_source = delayed.source.model_copy(
        update={
            "captured_monotonic_ns": time.monotonic_ns() - 350_000_000,
            "frame_age_ms": 10.0,
        }
    )
    delayed = NativeFrame(descriptor_source, delayed.payload, time.monotonic())

    async def backlogged():
        yield delayed
        await asyncio.Event().wait()

    pi.mjpeg = backlogged
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        current = warmup(client)
        raw = client.get("/api/v1/rider/camera/raw")
        assert raw.status_code == 200
        assert float(raw.headers["x-rider-frame-age-ms"]) >= 350
        assert raw.headers["x-rider-stale"] == "true"
        assert (
            raw.headers["x-rider-frame-age-source"]
            == "pi-status-anchor-plus-host-elapsed"
        )
        preview = client.get("/api/v1/rider/camera/venom")
        assert preview.status_code == 409
        assert preview.json()["detail"]["code"] == "frame_stale"
        # Even an erroneously underreported Pi request age cannot bypass the
        # host anchor's conservative source age.
        reference = client.post(
            "/api/v1/rider/compute/vision/decision-reference",
            headers=AUTH,
            json=descriptor(current, age_ms=10.0),
        )
        assert reference.status_code == 409
        assert reference.json()["detail"]["code"] == "vision_frame_expired"
        assert detector.calls == 0 and pi.snapshot_requests == 0


async def test_cached_status_timing_is_never_redated_as_a_new_age_anchor():
    device = Device("pi", "Fixture", "http://127.0.0.1:18880", "device")
    pi = PiFixture(device)
    domain = RiderService((device,), client_factory=lambda _device: pi)
    await domain.select("pi", domain.session)
    first = await domain.pipeline()
    anchor = first["_age_anchor"]
    assert anchor is not None
    source = pi.latest.source
    age_before = anchor.upper_age_ms(source, time.monotonic())
    await asyncio.sleep(0.03)
    second = await domain.pipeline()
    assert second["_age_anchor"].request_started_mono == anchor.request_started_mono
    assert (
        second["_age_anchor"].upper_age_ms(source, time.monotonic()) >= age_before + 25
    )
    assert sum(path == "/api/resource/camera/pipeline" for _, path, _ in pi.calls) == 1
    await domain.close()


def test_native_prepare_rechecks_cold_pipeline_and_retires_old_camera_authority(
    monkeypatch,
):
    app, service, pi = fixture(monkeypatch)
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        old_source = warmup(client)
        old_connection = app.state.rider_stream.connection
        assert old_connection is not None and not old_connection.closed

        # Direct Pi start/stop changes can happen while the host's previous
        # active pipeline response is still inside its normal 200 ms cache.
        pi.pipeline.update(
            active_profile=None,
            state="starting",
            camera_session=None,
            transport_epoch=3,
            revision=4,
        )
        waiting = client.get(
            "/api/v1/rider/compute/vision/status?stream=true", headers=AUTH
        )
        assert waiting.status_code == 200, waiting.text
        assert waiting.json()["stream"] == {
            "ready": False,
            "latest_source": None,
            "frame_age_ms": None,
            "error": "stream_not_ready",
        }
        assert old_connection.closed and app.state.rider_stream.connection is None

        pi.pipeline.update(
            active_profile=PROFILE.copy(), state="active", camera_session="next-camera"
        )
        pi.latest = native(session="next-camera", epoch=3, revision=4)
        ready = warmup(client)
        assert ready["camera_session"] == "next-camera"
        assert ready["transport_epoch"] == 3 and ready["pipeline_revision"] == 4
        assert ready != old_source and pi.opened == 2
        assert detector.calls == 0 and pi.snapshot_requests == 0
        assert all(method == "GET" for method, _path, _body in pi.calls)


@pytest.mark.parametrize("state", ["stopped", "stopping", "starting", "waiting_frame"])
def test_native_prepare_reports_pending_without_a_camera_session_or_automatic_start(
    monkeypatch, state
):
    app, service, pi = fixture(monkeypatch)
    pi.pipeline.update(state=state, active_profile=None, camera_session=None)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/rider/compute/vision/status?stream=true", headers=AUTH
        )
        assert response.status_code == 200, response.text
        assert response.json()["stream"]["ready"] is False
        assert response.json()["stream"]["latest_source"] is None
        assert pi.opened == 0 and pi.snapshot_requests == 0
        assert all(method == "GET" for method, _path, _body in pi.calls)


def test_native_prepare_does_not_turn_real_camera_failure_into_pending(monkeypatch):
    app, service, pi = fixture(monkeypatch)
    pi.pipeline.update(state="failed", active_profile=None)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/rider/compute/vision/status?stream=true", headers=AUTH
        )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "stream_unavailable"
        assert pi.opened == 0


def test_active_native_preview_keeps_camera_source_distinct_from_pi_run_identity(
    monkeypatch,
):
    import hashlib

    app, service, pi = fixture(monkeypatch)
    pi.latest = native(101)
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        current = warmup(client)
        pi.running = True
        for request_sequence, camera_sequence in ((1, 101), (2, 107)):
            if request_sequence > 1:
                current = client.portal.call(pi.emit, camera_sequence)
            # Pi hashes its request input and counts admitted loop cycles. Those
            # identifiers need not equal sensor frame IDs or camera sequence.
            request_id = hashlib.sha256(
                json.dumps(current, sort_keys=True).encode()
            ).hexdigest()
            response = client.post(
                "/api/v1/rider/compute/vision/decision-reference",
                headers=AUTH,
                json=descriptor(current, request_sequence, frame_id=request_id),
            )
            assert response.status_code == 200, response.text
            decision = response.json()
            assert decision["frame_id"] == request_id
            assert decision["sequence"] == request_sequence
            assert decision["source"] == current

            preview = client.get("/api/v1/rider/camera/venom")
            assert preview.status_code == 200, preview.text
            source = json.loads(preview.headers["x-rider-source-json"])
            analysis = json.loads(preview.headers["x-rider-analysis-json"])
            assert source["frame_id"] == f"session-{camera_sequence}"
            assert source["sequence"] == camera_sequence
            assert preview.headers["x-rider-frame-id"] == source["frame_id"]
            assert analysis["source"] == current
            assert analysis["source"]["frame_id"] == source["frame_id"]
            assert analysis["source"]["sequence"] == source["sequence"]
            assert analysis["frame_id"] == request_id
            assert analysis["sequence"] == request_sequence
            assert preview.content == jpeg() and detector.calls == request_sequence
            assert (
                app.state.rider_vision.runs[("pi", "pi-run")]["sequence"]
                == request_sequence
            )
        assert pi.opened == 1 and pi.snapshot_requests == 0
        assert all(method == "GET" for method, _path, _body in pi.calls)
