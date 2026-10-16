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
