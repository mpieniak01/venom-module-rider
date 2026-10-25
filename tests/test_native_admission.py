"""A version proof avoids nested Pi RPC but never replaces capture admission."""

import copy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_pipeline_api import AUTH, Detector, descriptor, fixture, warmup

from venom_module_rider.native_admission import NativeAdmissionCache


def world():
    policy = {
        "revision": 4,
        "perception": {"model_id": "model", "artifact_sha256": "a" * 64},
    }
    pipeline = {
        "contract_id": "rider.camera_pipeline",
        "contract_version": 1,
        "revision": 3,
        "transport_epoch": 2,
        "camera_session": "camera",
        "state": "active",
        "active_profile": {"analysis_location": "venom", "remote_transport": "mjpeg"},
    }
    client = SimpleNamespace(device=SimpleNamespace(id="pi"))
    domain = SimpleNamespace(
        session="session",
        epoch=1,
        native_admission_epoch=0,
        transport=lambda: client,
        cache={
            "/api/navigation/config": (0, {"data": copy.deepcopy(policy)}),
            "/api/resource/camera/pipeline": (0, {"data": copy.deepcopy(pipeline)}),
        },
    )

    def binding(*_args):
        return ("pi", "session", 2, "camera", 3)

    connection = SimpleNamespace(
        closed=False,
        reader=SimpleNamespace(done=lambda: False),
        error=None,
        frames={"frame": True},
        client=client,
        domain=domain,
        binding=binding(),
    )
    hub = SimpleNamespace(connection=connection, closed=False, binding=binding)
    models = SimpleNamespace(runtime=SimpleNamespace(generation=0))
    request = SimpleNamespace(
        frame=SimpleNamespace(config_revision=4),
        source=SimpleNamespace(
            transport_epoch=2, pipeline_revision=3, camera_session="camera"
        ),
    )
    cache = NativeAdmissionCache()
    cache.put("pi", domain, models, hub, pipeline, policy)
    return cache, domain, models, hub, request


def test_snapshot_is_version_bound_and_isolated_from_callers():
    cache, domain, models, hub, request = world()
    result = cache.get("pi", domain, models, hub, request)
    assert result is not None
    result[1]["revision"] = 88
    assert cache.get("pi", domain, models, hub, request)[1]["revision"] == 4


@pytest.mark.parametrize(
    "changed",
    [
        "revision",
        "epoch",
        "mutation",
        "inflight_mutation",
        "session",
        "runtime",
        "cache_clear",
        "closed",
        "reader_ended",
        "reader_error",
        "no_frames",
        "binding",
        "source",
        "profile",
    ],
)
def test_every_authority_boundary_misses_instead_of_reusing_snapshot(changed):
    cache, domain, models, hub, request = world()
    if changed == "revision":
        request.frame.config_revision += 1
    elif changed == "epoch":
        domain.epoch += 1
    elif changed == "mutation":
        domain.native_admission_epoch += 1
    elif changed == "inflight_mutation":
        domain.native_admission_mutating = 1
    elif changed == "session":
        domain.session = "new"
    elif changed == "runtime":
        models.runtime.generation += 1
    elif changed == "cache_clear":
        domain.cache.clear()
    elif changed == "closed":
        hub.connection.closed = True
    elif changed == "reader_ended":
        hub.connection.reader.done = lambda: True
    elif changed == "reader_error":
        hub.connection.error = "stream_disconnected"
    elif changed == "no_frames":
        hub.connection.frames.clear()
    elif changed == "binding":
        hub.connection.binding = ("pi", "session", 3, "new", 4)
    elif changed == "source":
        request.source.camera_session = "new"
    else:
        domain.cache["/api/resource/camera/pipeline"][1]["data"]["active_profile"][
            "analysis_location"
        ] = "local"
    assert cache.get("pi", domain, models, hub, request) is None


def selected_fixture(monkeypatch):
    app, service, pi = fixture(monkeypatch)
    original = pi.json
    policy = {
        "corridor": [0.35, 0.8, 0.65, 0.95],
        "revision": 4,
        "perception": {
            "engine": "depth_plane",
            "model_id": "depth-anything-v2-indoor-small",
            "artifact_sha256": "a" * 64,
        },
    }

    async def selected(path, **kwargs):
        if path == "/api/navigation/config":
            pi.calls.append(("GET", path, None))
            return {"data": copy.deepcopy(policy)}
        return await original(path, **kwargs)

    pi.json = selected
    return app, service, pi, policy


def test_prepared_selected_source_needs_zero_pi_gets_for_repeated_decisions(
    monkeypatch,
):
    app, service, pi, policy = selected_fixture(monkeypatch)
    with TestClient(app) as client:
        app.state.rider_vision.detector = Detector()
        current = warmup(client)
        calls = []

        async def decide(_device, frame, _payload, **_kwargs):
            calls.append(frame)
            return {"state": "unknown", "action": "stop"}

        monkeypatch.setattr(app.state.rider_vision, "decide", decide)
        pi.calls.clear()
        for sequence in (1, 2, 3):
            # An ordinary UI poll projects transport server_time into the same
            # cached config without changing any authoritative policy field.
            service.cache["/api/navigation/config"][1]["data"]["server_time"] = sequence
            if sequence > 1:
                current = client.portal.call(pi.emit, sequence)
            response = client.post(
                "/api/v1/rider/compute/vision/decision-reference",
                headers=AUTH,
                json=descriptor(current, sequence, perception=policy["perception"]),
            )
            assert response.status_code == 200, response.text
            assert (
                response.json()["source_timing_ms"]["authority_source"]
                == "version_snapshot"
            )
        assert len(calls) == 3
        assert pi.calls == []


def test_snapshot_cannot_admit_old_buffered_source_or_forged_selected_hash(monkeypatch):
    app, _service, _pi, policy = selected_fixture(monkeypatch)
    with TestClient(app) as client:
        app.state.rider_vision.detector = Detector()
        current = warmup(client)
        calls = []

        async def decide(*_args, **_kwargs):
            calls.append(True)
            return {}

        monkeypatch.setattr(app.state.rider_vision, "decide", decide)
        response = client.post(
            "/api/v1/rider/compute/vision/decision-reference",
            headers=AUTH,
            json=descriptor(
                current,
                perception={**policy["perception"], "artifact_sha256": "b" * 64},
            ),
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "model_selection_conflict"
        hub = app.state.rider_stream
        clock = hub.clock
        monkeypatch.setattr(hub, "clock", lambda: clock() + 0.31)
        response = client.post(
            "/api/v1/rider/compute/vision/decision-reference",
            headers=AUTH,
            json=descriptor(current, perception=policy["perception"]),
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "vision_frame_expired"
        assert calls == []
