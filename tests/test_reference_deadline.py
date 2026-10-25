"""Reference TTL uses Pi-relative time, not the status anchor's upper bound."""

import asyncio
import time

from fastapi.testclient import TestClient
from test_pipeline_api import AUTH, Detector, descriptor, fixture, warmup


def test_status_roundtrip_uncertainty_does_not_expire_a_fresh_pi_drive(monkeypatch):
    app, _service, pi = fixture(monkeypatch)
    read = pi.json

    async def uncertain_status(path, **kwargs):
        result = await read(path, **kwargs)
        if path == "/api/resource/camera/pipeline":
            # A real case returned at Pi capture age247.29ms with RTT200.41ms,
            # yet the host expired it. A slow status sample has up to its whole
            # RTT as uncertainty: it must not become the drive's exact age.
            result["_rider_request_started_mono"] -= 0.31
        return result

    pi.json = uncertain_status
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        current = warmup(client)
        for sequence in (1, 2, 3):
            if sequence > 1:
                current = client.portal.call(pi.emit, sequence)
            response = client.post(
                "/api/v1/rider/compute/vision/decision-reference",
                headers=AUTH,
                json=descriptor(current, sequence, mode="drive", age_ms=46.88),
            )
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["source"] == current
            assert result["state"] == "clear"
            assert result["valid_for_ms"] >= 50
            timing = result["source_timing_ms"]
            assert timing["upper_age"] >= 300
            assert timing["deadline_age"] < 300
        assert result["action"] == "go"
        assert detector.calls == 3
        assert pi.snapshot_requests == 0
        assert all(method == "GET" for method, _, _ in pi.calls)


def test_reference_waiting_for_canonical_still_spends_its_pi_deadline(monkeypatch):
    app, _service, pi = fixture(monkeypatch)
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        current = warmup(client)
        hub = app.state.rider_stream
        canonical = hub.canonical_frame

        async def slow_decode(*args):
            await asyncio.sleep(0.025)
            return await canonical(*args)

        monkeypatch.setattr(hub, "canonical_frame", slow_decode)
        response = client.post(
            "/api/v1/rider/compute/vision/decision-reference",
            headers=AUTH,
            json=descriptor(current, age_ms=290.0),
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "vision_frame_expired"
        assert detector.calls == 0


def test_host_buffered_old_source_cannot_be_made_young_by_the_request(monkeypatch):
    app, _service, pi = fixture(monkeypatch)
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        current = warmup(client)
        time.sleep(0.32)  # Cache is500ms; the decision budget remains300ms.
        response = client.post(
            "/api/v1/rider/compute/vision/decision-reference",
            headers=AUTH,
            json=descriptor(current, age_ms=5.0),
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "vision_frame_expired"
        assert detector.calls == 0


def test_reference_metadata_reads_run_together_within_one_capture_budget(monkeypatch):
    from venom_module_rider.api import pipeline_routes, vision_routes

    app, _service, pi = fixture(monkeypatch)
    with TestClient(app) as client:
        app.state.rider_vision.detector = Detector()
        current = warmup(client)
        pipeline, policy = (
            pipeline_routes.native_pipeline,
            vision_routes.validate_model_policy,
        )
        started = set()

        async def pipeline_read(*args, **kwargs):
            started.add("pipeline")
            await asyncio.sleep(0)
            assert "policy" in started, "policy read waits behind pipeline read"
            return await pipeline(*args, **kwargs)

        async def policy_read(*args, **kwargs):
            started.add("policy")
            await asyncio.sleep(0)
            assert "pipeline" in started
            return await policy(*args, **kwargs)

        monkeypatch.setattr(pipeline_routes, "native_pipeline", pipeline_read)
        monkeypatch.setattr(vision_routes, "validate_model_policy", policy_read)
        result = client.post(
            "/api/v1/rider/compute/vision/decision-reference",
            headers=AUTH,
            json=descriptor(current),
        )
        assert result.status_code == 200, result.text
        assert result.json()["source"] == current
        timing = result.json()["source_timing_ms"]
        assert all(
            timing[key] >= 0
            for key in ("pipeline_ms", "policy_ms", "source_wait_ms", "canonical_ms")
        )
        assert all(method == "GET" for method, _, _ in pi.calls)


def test_rejected_policy_cancels_other_metadata_read_without_inference(monkeypatch):
    from venom_module_rider.api import pipeline_routes, vision_routes
    from venom_module_rider.errors import RiderError

    app, _service, pi = fixture(monkeypatch)
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        current = warmup(client)
        cancelled = []

        async def blocked_pipeline(*_args, **_kwargs):
            try:
                await asyncio.Future()
            finally:
                cancelled.append(True)

        async def rejected_policy(*_args, **_kwargs):
            await asyncio.sleep(0)
            raise RiderError("model_selection_conflict", 409)

        monkeypatch.setattr(pipeline_routes, "native_pipeline", blocked_pipeline)
        monkeypatch.setattr(vision_routes, "validate_model_policy", rejected_policy)
        response = client.post(
            "/api/v1/rider/compute/vision/decision-reference",
            headers=AUTH,
            json=descriptor(current),
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "model_selection_conflict"
        assert cancelled == [True] and detector.calls == 0
        assert all(method == "GET" for method, _, _ in pi.calls)


def test_ticket_preparation_counts_before_model_admission(monkeypatch):
    from venom_module_rider import sample_sources

    app, _service, pi = fixture(monkeypatch)
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        current = warmup(client)
        hub = app.state.rider_stream
        clock = hub.clock
        elapsed = [0.0]
        monkeypatch.setattr(hub, "clock", lambda: clock() + elapsed[0])

        def slow_ticket(*_args):
            elapsed[0] += 0.31
            return None

        monkeypatch.setattr(sample_sources, "native_ticket", slow_ticket)
        response = client.post(
            "/api/v1/rider/compute/vision/decision-reference",
            headers=AUTH,
            json=descriptor(current, age_ms=5.0),
        )
        assert response.status_code == 409
        details = response.json()["detail"]
        assert details["code"] == "vision_frame_expired"
        timing = details["details"]["source_timing_ms"]
        assert timing["ticket_ms"] >= 310
        assert timing["deadline_age"] >= 300
        assert timing["failed_stage"] == "source_ticket"
        assert timing["host_total_ms"] >= 310
        assert detector.calls == 0


def test_reference_timeout_keeps_inflight_phase_evidence(monkeypatch):
    app, _service, _pi = fixture(monkeypatch)
    with TestClient(app) as client:
        detector = Detector()
        app.state.rider_vision.detector = detector
        current = warmup(client)
        hub = app.state.rider_stream

        async def blocked_decode(*_args):
            await asyncio.Future()

        monkeypatch.setattr(hub, "canonical_frame", blocked_decode)
        response = client.post(
            "/api/v1/rider/compute/vision/decision-reference",
            headers=AUTH,
            json=descriptor(current, age_ms=280.0),
        )
        assert response.status_code == 409
        timing = response.json()["detail"]["details"]["source_timing_ms"]
        assert timing["failed_stage"] == "canonical"
        assert timing["canonical_ms"] > 0
        assert timing["remaining_ms"] == 0
        assert "runtime_state" in timing and "model_busy" in timing
        assert detector.calls == 0
