"""Pulse-series proxy and evidence use only inert Pi responses and archive data."""

import copy
import json

import httpx
import pytest
from pydantic import ValidationError
from test_motion_study import setup_study, snapshot
from test_navigation_trial_api import setup_proxy

from venom_module_rider.api.pulse_trial_routes import TrialStart
from venom_module_rider.demo import envelope
from venom_module_rider.motion_timeline import build_timeline
from venom_module_rider.pulse_trials import series_timeline, trial_evidence
from venom_module_rider.sample_evidence import observations


def trial(**changes):
    return {
        "version": 1,
        "run_id": "run-abc",
        "profile_id": "B",
        "profile_version": 1,
        "count": 2,
        "pulse_ms": 250,
        "gap_ms": 100,
        "direction": "forward",
        "settings_revision": 4,
        "generation": "generation",
        "group_id": "a" * 64,
        "common": {
            "parameters": {"speed": 0.6},
            "ramps": {"linear_rate": 0.5, "linear_deceleration": 1.0},
            "context": "wood",
        },
        "phase": "completed",
        "reason": "completed",
        "pulse_index": 2,
        "completed_pulses": 2,
        "elapsed_ms": 620.0,
        "events": [
            {"kind": "pulse_started", "pulse_index": 1, "elapsed_ms": 0.0},
            {"kind": "pulse_zero", "pulse_index": 1, "elapsed_ms": 260.0},
            {"kind": "gap_started", "pulse_index": 1, "elapsed_ms": 260.0},
            {"kind": "pulse_started", "pulse_index": 2, "elapsed_ms": 360.0},
            {"kind": "pulse_zero", "pulse_index": 2, "elapsed_ms": 620.0},
            {"kind": "completed", "pulse_index": 2, "elapsed_ms": 620.0},
        ],
        "physical_completion": "unverified",
        **changes,
    }


def body(**changes):
    return {
        "action": "arm",
        "request_id": "run-abc",
        "generation": "generation",
        "seq": 2,
        "ts": 1.0,
        "ttl_ms": 500,
        "settings_revision": 4,
        "source": "manual",
        "execution_context": "panel",
        "owner": "manual",
        "arming_mode": "operator",
        "panel_id": "panel",
        "operator_consent": True,
        "operator_plan": "Mock-only B 2x250ms; no physical robot",
        "trial_profile_id": "B",
        "trial_version": 1,
        "trial_context": "wood",
        **changes,
    }


def packet(data, family="rider.pulse_trials"):
    response = envelope(data, family)
    return httpx.Response(200, content=response.body, headers=response.headers)


async def test_catalog_and_terminal_read_are_read_only_and_capture_final_control(
    monkeypatch,
):
    seen = []

    def receive(request):
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/profiles"):
            return packet({"version": 1, "supported": True, "profiles": []})
        if request.url.path == "/api/control/state":
            return packet(
                {
                    "safety": {
                        "contract_version": 1,
                        "pulse_trials": {"current": trial()},
                    }
                },
                "rider.safety",
            )
        return packet({"version": 1, "trial": trial()})

    service, client = await setup_proxy(monkeypatch, receive)
    try:
        assert (
            await client.get("/api/v1/rider/motion/trials/profiles")
        ).status_code == 200
        result = await client.get("/api/v1/rider/motion/trials/run-abc")
        assert result.status_code == 200, result.text
        assert result.json()["data"]["trial"]["completed_pulses"] == 2
        assert all(method == "GET" for method, _ in seen)
        assert seen[-1][1] == "/api/control/state"
        assert (await client.get("/api/v1/rider/motion/trials/bad!")).status_code == 400
    finally:
        await client.aclose()
        await service.close()


async def test_start_preserves_fresh_operator_packet_and_shared_gate(monkeypatch):
    seen = []

    def receive(request):
        seen.append(request)
        return packet(
            {"confirmed": True, "safety": {"contract_version": 1}}, "rider.safety"
        )

    service, client = await setup_proxy(monkeypatch, receive)
    try:
        original = body()
        response = await client.post(
            "/api/v1/rider/motion/trials",
            json=original,
            headers={"X-Rider-Session": service.session},
        )
        assert response.status_code == 200, response.text
        assert seen[-1].url.path == "/api/safety"
        assert json.loads(seen[-1].content) == original
        n = len(seen)
        response = await client.post(
            "/api/v1/rider/motion/trials",
            json=original,
            headers={"X-Rider-Session": "old"},
        )
        assert response.status_code == 409
        assert len(seen) == n
    finally:
        await client.aclose()
        await service.close()


@pytest.mark.parametrize(
    "change",
    [
        {"operator_consent": False},
        {"trial_version": True},
        {"trial_profile_id": "D"},
        {"lease_s": 30.0},
        {"panel_id": ""},
        {"trial_context": "x" * 81},
    ],
)
def test_invalid_start_shape_never_reaches_domain(change):
    with pytest.raises(ValidationError):
        TrialStart.model_validate(body(**change))


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"profile_version": True},
        {"count": 5},
        {"completed_pulses": 3},
        {"completed_pulses": 1},
        {"elapsed_ms": float("inf")},
        {"events": [{"kind": "pulse_zero", "pulse_index": 1, "elapsed_ms": 999.0}]},
    ],
)
def test_untrusted_series_evidence_is_validated(change):
    with pytest.raises((ValueError, ValidationError)):
        trial_evidence(trial(**change))


def source(run, mono=101.0):
    return observations(
        "/api/control/state",
        {
            "data": {
                "safety": {
                    "instance_id": "boot",
                    "observed_at": 1000.0,
                    "ready": True,
                    "stale": False,
                    "pulse_trials": {"current": run},
                    "output": {"lx": 0.0, "az": 0.0},
                }
            }
        },
        "demo",
        "session",
        mono,
        1001.0,
    )[0]


def test_timeline_retains_whole_series_and_does_not_roll_back_terminal_state():
    initial = trial(phase="pulse", completed_pulses=0, elapsed_ms=0.0, events=[])
    final = trial()
    rows = [source(initial, 100.0), source(final, 101.0), source(initial, 102.0)]
    series = series_timeline(rows, 100.0)
    assert len(series) == 1
    assert series[0]["phase"] == "completed"
    assert series[0]["t"] == 0.0
    assert len(series[0]["events"]) == 6
    assert series[0]["clock_sync_uncertainty_ms"] is None
    assert source(final)["executor"]["pulse_trial"]["common"]["context"] == "wood"


async def test_saved_series_round_trips_through_real_archive_without_device(tmp_path):
    service, pi, registry, sid = await setup_study(tmp_path)
    try:
        row = source(trial())
        await registry.read(registry.store.evidence.append, sid, [row])
        await registry.end(sid)
        manifest = await snapshot(registry, sid)
        pi.offline = True
        timeline = build_timeline(manifest)
        series = timeline["streams"][0]["trials"][0]
        assert series["run_id"] == "run-abc"
        assert series["completed_pulses"] == 2
        assert timeline["physical_distance"] is None
        assert timeline["physical_velocity"] is None
    finally:
        await registry.close()
        await service.close()


def test_unknown_extra_series_fields_cannot_enter_archive():
    bad = copy.deepcopy(trial())
    bad["fake_distance_m"] = 100
    with pytest.raises(ValidationError):
        source(bad)


@pytest.mark.parametrize("busy_slot", [False, True])
async def test_terminal_status_is_saved_before_the_recording_ends(
    monkeypatch, tmp_path, busy_slot
):
    from venom_module_rider.samples_archive import SampleArchive
    from venom_module_rider.samples_registry import SamplesRegistry

    def receive(request):
        if request.url.path == "/api/control/state":
            return packet(
                {
                    "safety": {
                        "contract_version": 1,
                        "instance_id": "boot",
                        "observed_at": 1000.0,
                        "ready": True,
                        "stale": False,
                        "pulse_trials": {"version": 1, "current": trial()},
                    }
                },
                "rider.safety",
            )
        return packet({"version": 1, "trial": trial()})

    service, client = await setup_proxy(monkeypatch, receive)
    registry = SamplesRegistry(tmp_path / "archive")
    service.samples = registry
    try:
        session = await registry.start(
            "series",
            "pi",
            {
                "kind": "manual",
                "scene_group": "motion-study-v1:wood",
                "record_images": False,
                "record_video": False,
            },
        )
        if busy_slot:
            assert registry.evidence.slot.acquire(blocking=False)
        try:
            response = await client.get("/api/v1/rider/motion/trials/run-abc")
            assert response.status_code == 200
        finally:
            if busy_slot:
                registry.evidence.slot.release()
        await registry.end(session["session_id"])
        manifest, revision = await registry.read(
            SampleArchive(registry.store).snapshot, session["session_id"]
        )
        manifest["dataset_revision"] = revision
        assert manifest["evidence"]["records"], (
            manifest["evidence"],
            registry.failures,
        )
        series = build_timeline(manifest)["streams"][0]["trials"][0]
        assert series["phase"] == "completed"
        assert series["completed_pulses"] == 2
    finally:
        await client.aclose()
        await registry.close()
        await service.close()
