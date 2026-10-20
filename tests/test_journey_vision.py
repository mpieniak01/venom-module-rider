"""Real vision admission and planner across successive obstacle episodes."""

from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_steering import SETTINGS, obstacle

from venom_module_rider.errors import RiderError
from venom_module_rider.vision_contract import VisionFrame
from venom_module_rider.vision_loop import VisionLoop


def frame(seq, *, epoch=0, **changes):
    return VisionFrame(
        protocol_version=3,
        run_id="journey-run",
        frame_id=f"frame-{seq}",
        sequence=seq,
        config_revision=4,
        age_ms=0.0,
        deadline_ms=300,
        mode="drive",
        verified=True,
        calibration_id="fixture",
        model_sha256="a" * 64,
        perception={"engine": "depth_plane"},
        avoidance=SETTINGS,
        journey={"version": 1, "id": "mission", "maneuver_epoch": epoch},
        **changes,
    )


class Detector:
    def __init__(self):
        self.result = obstacle()

    def status(self):
        return {"sha256": "a" * 64}

    def detect(self, *_):
        return deepcopy(self.result)


@pytest.fixture
async def vision():
    now, detector = [0.0], Detector()
    loop = VisionLoop(detector, clock=lambda: now[0])
    loop.depth_detector = detector
    yield loop, detector, now
    await loop.close()


async def decide(vision, seq, **changes):
    loop, _, now = vision
    result = await loop.decide("fixture", frame(seq, **changes), b"test-image")
    now[0] += 0.07
    return result


async def test_two_obstacles_get_separate_pi_approved_correction_budgets(vision):
    loop, detector, _ = vision
    for seq in range(1, 4):
        first = await decide(vision, seq)
    assert first["action"] == "steer_right" and first["turn_count"] == 1
    waiting = await decide(vision, 4, motion_phase="steer_wait")
    assert waiting["action"] == "stop"
    detector.result = {**obstacle(), "state": "clear"}
    for seq in range(5, 10):
        clear = await decide(vision, seq)
    assert clear["action"] == "go"
    # Pi owns this transition after accepting fresh straightening decisions.
    detector.result = obstacle("right")
    for seq in range(10, 13):
        second = await decide(vision, seq, epoch=1)
    assert second["action"] == "steer_left" and second["turn_count"] == 1
    assert second["journey"] == {"version": 1, "id": "mission", "maneuver_epoch": 1}
    assert second["host_session"] == first["host_session"] == loop.host_session


async def test_clear_without_pi_transition_does_not_replenish_budget(vision):
    _, detector, _ = vision
    for seq in range(1, 4):
        await decide(vision, seq)
    detector.result = {**obstacle(), "state": "clear"}
    for seq in range(4, 7):
        await decide(vision, seq)
    detector.result = obstacle()
    result = await decide(vision, 7)
    assert result["action"] == "steer_right" and result["turn_count"] == 2
    for seq in range(8, 10):
        result = await decide(vision, seq)
        assert result["turn_count"] == 2


@pytest.mark.parametrize("epoch", [1, 2])
async def test_first_epoch_must_be_zero(vision, epoch):
    with pytest.raises(RiderError, match="vision_journey_epoch_changed"):
        await decide(vision, 1, epoch=epoch)


async def test_epoch_change_without_completed_correction_is_rejected(vision):
    _, detector, _ = vision
    detector.result = {**obstacle(), "state": "clear"}
    for seq in range(1, 5):
        await decide(vision, seq)
    with pytest.raises(RiderError, match="vision_journey_epoch_changed"):
        await decide(vision, 5, epoch=1)


@pytest.mark.parametrize("epoch", [0, 2])
async def test_single_clear_frame_cannot_refresh_budget(vision, epoch):
    _, detector, _ = vision
    for seq in range(1, 4):
        await decide(vision, seq)
    detector.result = {**obstacle(), "state": "clear"}
    await decide(vision, 4)
    if epoch == 0:
        # A valid unchanged epoch does not reset the correction counter.
        result = await decide(vision, 5)
        assert result["turn_count"] == 1
    else:
        with pytest.raises(RiderError, match="vision_journey_epoch_changed"):
            await decide(vision, 5, epoch=epoch)


async def test_rollback_and_epoch_skip_are_rejected(vision):
    _, detector, _ = vision
    for seq in range(1, 4):
        await decide(vision, seq)
    detector.result = {**obstacle(), "state": "clear"}
    for seq in range(4, 7):
        await decide(vision, seq)
    await decide(vision, 7, epoch=1)
    for epoch in (0, 3):
        with pytest.raises(RiderError, match="vision_journey_epoch_changed"):
            await decide(vision, 8, epoch=epoch)


async def test_terminal_obstacle_cannot_be_resurrected_by_clear_frame(vision):
    _, detector, _ = vision
    for seq in range(1, 11):
        result = await decide(vision, seq)
    assert result["terminal_stop"] is True
    detector.result = {**obstacle(), "state": "clear"}
    with pytest.raises(RiderError, match="vision_journey_ended"):
        await decide(vision, 11)


async def test_changed_mission_or_capture_context_never_resets_budget(vision):
    loop, _, _ = vision
    await decide(vision, 1)
    changed = frame(2).model_copy(
        update={
            "journey": frame(2).journey.model_copy(update={"id": "different"}),
        }
    )
    with pytest.raises(RiderError, match="vision_replay_or_configuration_changed"):
        await loop.decide("fixture", changed, b"test-image")


async def test_long_clear_sequence_preserves_session_and_bounded_storage(vision):
    loop, detector, now = vision
    detector.result = {**obstacle(), "state": "clear"}
    for seq in range(1, 202):
        result = await decide(vision, seq)
    assert now[0] > 10 and result["action"] == "go"
    assert result["valid_for_ms"] <= 200
    assert len(loop.runs) == 1 and len(loop.previews) == 0
    assert loop.status()["navigation_journey_version"] == 1
    another = VisionLoop(Detector())
    assert another.host_session != loop.host_session
    await another.close()


@pytest.mark.parametrize(
    "context",
    [
        {"version": True, "id": "mission", "maneuver_epoch": 0},
        {"version": 1, "id": " ", "maneuver_epoch": 0},
        {"version": 1, "id": "mission", "maneuver_epoch": True},
        {"version": 1, "id": "mission", "maneuver_epoch": -1},
    ],
)
def test_journey_context_is_strict(context):
    with pytest.raises(ValidationError):
        VisionFrame.model_validate({**frame(1).model_dump(), "journey": context})


@pytest.mark.parametrize(
    "change",
    [
        {"mode": "observe"},
        {"protocol_version": 2},
        {"avoidance": {"enabled": False}},
    ],
)
def test_journey_requires_explicit_accepted_steering_policy(change):
    with pytest.raises(ValidationError):
        VisionFrame.model_validate({**frame(1).model_dump(), **change})


async def test_actual_pi_lease_and_host_planner_pass_two_obstacles_then_stop(vision):
    """Paired repo fixture imports exact Pi source, never its hardware executor."""
    import importlib.util
    import os

    source = os.getenv("RIDER_TEST_PI_LEASE")
    if not source:
        pytest.skip("Explicit actual Pi lease fixture required; no hardware")
    spec = importlib.util.spec_from_file_location("journey_pi", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    loop, detector, now = vision
    outputs = []
    lease = module.DecisionLease(
        {
            "revision": 4,
            "mode": "drive",
            "verified": True,
            "model_sha256": "a" * 64,
            "speed": 0.1,
            "max_motion_ms": 150,
            "decision_budget_ms": 300,
            "avoidance": SETTINGS.model_dump(),
        },
        "journey-run",
        lambda *x: outputs.append(("go", *x)),
        lambda: outputs.append(("halt",)),
        clock=lambda: now[0],
        pause=lambda *x: outputs.append(("zero", *x)),
        steer=lambda *x: outputs.append(("steer", *x)),
        feedback_wait=True,
        journey={"version": 1, "id": "mission", "maneuver_epoch": 0},
    )
    sequence = 0

    async def cycle(result):
        nonlocal sequence
        sequence += 1
        detector.result = result
        captured = now[0]
        f = frame(sequence, epoch=lease.journey.context["maneuver_epoch"])
        assert lease.tick()
        row = await loop.decide("fixture", f, b"test-image")
        lease.accept(row, f.frame_id, sequence, captured)
        now[0] += 0.07
        return row

    clear = {**obstacle(), "state": "clear"}
    for _ in range(160):
        await cycle(clear)
    assert now[0] > 10 and not lease.latched
    for side, expected_epoch in (("left", 1), ("right", 2)):
        count = len([x for x in outputs if x[0] == "steer"])
        for _ in range(3):
            await cycle(obstacle(side))
        assert len([x for x in outputs if x[0] == "steer"]) == count + 1
        for _ in range(8):
            await cycle(clear)
            if lease.journey.context["maneuver_epoch"] == expected_epoch:
                break
        assert lease.journey.context["maneuver_epoch"] == expected_epoch
        assert lease.turn_count == 0 and not lease.latched
    # A current-looking frame with the preceding host epoch is still obsolete.
    sequence += 1
    f = frame(sequence, epoch=1)
    rejected = {
        "protocol_version": 3,
        "run_id": f.run_id,
        "frame_id": f.frame_id,
        "sequence": sequence,
        "config_revision": 4,
        "valid_for_ms": 200,
        "journey": f.journey.model_dump(),
        "host_session": loop.host_session,
        "action": "go",
        "state": "clear",
        "clear_count": 3,
        "model_sha256": "a" * 64,
    }
    before = len([x for x in outputs if x[0] == "go"])
    assert not lease.accept(rejected, f.frame_id, sequence, now[0])
    assert lease.latched and lease.reason == "navigation_journey_identity_mismatch"
    assert outputs[-1] == ("halt",)
    assert len([x for x in outputs if x[0] == "go"]) == before
    assert [x[1] for x in outputs if x[0] == "steer"] == ["right", "left"]
    assert all(x[-1] <= 0.15 for x in outputs if x[0] in {"go", "steer"})


async def test_cancelled_inference_ends_journey_without_partial_state_500(vision):
    import asyncio
    import threading

    loop, detector, _ = vision
    entered, release = threading.Event(), threading.Event()

    def blocked(*_):
        entered.set()
        assert release.wait(2)
        return deepcopy(detector.result)

    detector.detect = blocked
    pending = asyncio.create_task(decide(vision, 1))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        inference = loop.depth_busy
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    finally:
        release.set()
    await inference
    with pytest.raises(RiderError, match="vision_journey_ended"):
        await decide(vision, 2)
    assert loop.runs[("fixture", "journey-run")]["journey_epoch"] == 0


@pytest.mark.parametrize("transport", ["snapshots", "mjpeg"])
def test_journey_http_contract_survives_both_image_transports(monkeypatch, transport):
    from fastapi.testclient import TestClient
    from test_pipeline_api import AUTH, PROFILE, fixture, warmup
    from test_stream_contract import jpeg

    app, _service, pi = fixture(monkeypatch)
    if transport == "snapshots":
        profile = {**PROFILE, "remote_transport": "snapshots"}
        pi.pipeline.update(profile=profile, active_profile=profile)
    with TestClient(app) as client:
        detector = Detector()
        detector.result = {**obstacle(), "state": "clear"}
        app.state.rider_vision.depth_detector = detector
        source = warmup(client) if transport == "mjpeg" else None
        for seq in range(1, 4):
            if source is not None and seq > 1:
                source = client.portal.call(pi.emit, seq)
            f = frame(seq)
            if source is not None:
                response = client.post(
                    "/api/v1/rider/compute/vision/decision-reference",
                    headers=AUTH,
                    json={"frame": f.model_dump(mode="json"), "source": source},
                )
            else:
                response = client.post(
                    "/api/v1/rider/compute/vision/decision",
                    content=jpeg(),
                    headers={
                        **AUTH,
                        "Content-Type": "image/jpeg",
                        "X-Rider-Vision": f.model_dump_json(),
                    },
                )
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["journey"] == f.journey.model_dump()
            assert result["host_session"] == app.state.rider_vision.host_session
            assert result["valid_for_ms"] <= 200
        assert result["action"] == "go"
        assert pi.opened == (1 if transport == "mjpeg" else 0)
        assert pi.snapshot_requests == 0
        assert all(method == "GET" for method, _, _ in pi.calls)


@pytest.mark.parametrize("expire_action", ["steer", "go"])
async def test_expired_decision_never_qualifies_next_maneuver_epoch(
    vision, monkeypatch, expire_action
):
    from venom_module_rider import vision_loop

    loop, detector, now = vision
    original = vision_loop.record_result

    def record(state, capture, action, result):
        original(state, capture, action, result)
        if (expire_action == "steer" and action.startswith("steer_")) or (
            expire_action == "go" and state.get("journey_completed")
        ):
            now[0] += 0.31

    monkeypatch.setattr(vision_loop, "record_result", record)
    for seq in range(1, 16):
        if seq > 3:
            detector.result = {**obstacle(), "state": "clear"}
        result = await decide(vision, seq)
        if result["reason"] == "vision_decision_expired":
            break
    assert result["reason"] == "vision_decision_expired"
    assert result["action"] == "stop"
    assert result["maneuver_clear_count"] == 0
    assert result["maneuver_phase"] == "vision_decision_expired"
    assert loop.runs[("fixture", "journey-run")]["journey_completed"] is False
    with pytest.raises(RiderError, match="vision_journey_epoch_changed"):
        await decide(vision, seq + 1, epoch=1)
