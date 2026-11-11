"""Axis-relative avoidance, stop/rotate/reobserve and explicit curved motion."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_collision_geometry import FRONT, ROI, assess, scene, settings

from venom_module_rider.steering import plan

GUIDANCE = {
    "version": 1,
    "motion_mode": "rotate_then_drive",
    "center_deadband": 0.1,
    "rotation_bounds": [0.2, 0.88, 0.8, 0.95],
}


def guided(**changes):
    return settings(guidance={**GUIDANCE, **changes})


def advance(result, config, state=None, count=3, phase="ready"):
    actions = []
    for _ in range(count):
        action, reason, state, *_, terminal = plan(
            deepcopy(result), state, config, phase, True, corridor=ROI
        )
        actions.append(action)
    return actions, reason, state, terminal


@pytest.mark.parametrize("x0,expected", [(130, "steer_right"), (180, "steer_left")])
@pytest.mark.parametrize("mirrored", [False, True])
def test_free_side_tie_is_resolved_away_from_obstacle_axis(x0, expected, mirrored):
    config = settings()
    config.mirrored = mirrored
    result = assess(scene((x0, 196, x0 + 10, 202, 0.5)), config)
    if mirrored:
        expected = "steer_left" if expected == "steer_right" else "steer_right"
    assert advance(result, config)[0][-1] == expected
    assert result["axis_evidence"]["obstacle_side"] == ("left" if x0 < 160 else "right")


def test_rotation_requires_zero_then_reobserves_before_forward():
    config = guided()
    result = assess(scene((130, 196, 140, 202, 0.5)), config)
    actions, reason, state, _ = advance(result, config)
    assert actions == ["stop"] * 3
    assert reason == "avoidance_stop_before_rotation"
    actions, _, state, _ = advance(result, config, state, count=1)
    assert actions == ["turn_right"]
    assert state["side"] == "right"
    assert advance(result, config, state, phase="steer_wait")[0] == ["stop"] * 3
    clear = assess(scene(), config)
    actions, reason, state, _ = advance(clear, config, state)
    assert actions == ["stop", "stop", "go"]
    assert "side" not in state


def test_rotation_never_borrows_curved_path_clearance():
    config = guided(rotation_bounds=None)
    result = assess(scene(FRONT), config)
    assert result["paths"]["left"]["state"] == "clear"
    assert result["paths"]["rotation"]["coverage_complete"] is False
    assert advance(result, config)[0] == ["stop"] * 3


def test_obstacle_in_body_rotation_mask_blocks_turn_even_with_free_curve():
    config = guided()
    result = assess(scene(FRONT, (65, 215, 75, 222, 0.5)), config)
    assert result["paths"]["guard"]["state"] == "clear"
    assert result["paths"]["rotation"]["state"] == "obstacle"
    assert advance(result, config)[0] == ["stop"] * 3


def test_auto_uses_curve_if_clear_and_rotation_only_with_own_evidence():
    config = guided(motion_mode="auto")
    result = assess(scene((130, 196, 140, 202, 0.5)), config)
    assert advance(result, config)[0][-1] == "steer_right"
    result["paths"]["right"]["state"] = "obstacle"
    actions, reason, state, _ = advance(result, config)
    assert actions[-1] == "stop"
    assert reason == "avoidance_stop_before_rotation"
    assert advance(result, config, state, count=1)[0] == ["turn_right"]


def test_selected_side_does_not_oscillate_when_obstacle_crosses_axis():
    config = guided(motion_mode="steer")
    left = assess(scene((130, 196, 140, 202, 0.5)), config)
    _, _, state, _ = advance(left, config)
    right = assess(scene((180, 196, 190, 202, 0.5)), config)
    assert advance(right, config, state)[0][-1] == "steer_right"


def test_image_no_progress_ends_corrections_before_independent_pulse_limit():
    config = guided(motion_mode="steer")
    config.max_turn_pulses = 5
    result = assess(scene(FRONT), config)
    state = None
    for _ in range(3):
        actions, reason, state, terminal = advance(result, config, state)
    assert terminal
    assert reason == "avoidance_no_progress"
    assert state["turns"] == 2


def test_obstacle_moving_toward_exit_is_progress_before_it_leaves_the_corridor():
    config = guided()
    config.max_turn_pulses = 5
    first = assess(scene((145, 196, 155, 202, 0.5)), config)
    _, _, state, _ = advance(first, config)
    actions, _, state, _ = advance(first, config, state, count=1)
    assert actions == ["turn_right"]
    # Still fully inside the corridor, but moving away from the driving axis.
    shifted = assess(scene((135, 196, 145, 202, 0.5)), config)
    _, _, state, terminal = advance(shifted, config, state)
    assert not terminal
    assert state["stalled"] == 0
    assert state["progress_delta"] > 0.03


@pytest.mark.parametrize("mirrored", [False, True])
async def test_continuous_journey_rotates_reobserves_and_handles_a_second_obstacle(
    monkeypatch, mirrored
):
    import json
    import os
    import subprocess
    from pathlib import Path

    import numpy as np
    from test_collision_geometry import PERCEPTION

    from venom_module_rider.overlay_geometry import analysis_geometry
    from venom_module_rider.vision_contract import VisionFrame
    from venom_module_rider.vision_loop import VisionLoop

    pi_root, pi_python = (
        os.getenv("RIDER_COLLISION_PI_ROOT"),
        os.getenv("RIDER_COLLISION_PI_PYTHON"),
    )
    if not pi_root or not pi_python:
        pytest.skip("Explicit paired Pi checkout/interpreter; mock outputs only")
    config = guided()
    config.mirrored = mirrored
    now, replies = [0.0], []
    sha = "a" * 64
    behavior = {"version": 1, "profile_id": "fluid", "profile_revision": 1}
    policy = {
        "revision": 7,
        "mode": "drive",
        "verified": True,
        "calibration_id": "offline",
        "model_sha256": sha,
        "speed": 0.1,
        "max_motion_ms": 150,
        "decision_budget_ms": 300,
        "corridor": list(ROI),
        "avoidance": config.model_dump(),
        "perception": PERCEPTION.model_dump(),
        "behavior": behavior,
    }
    journey = {"version": 1, "id": "axis-offline", "maneuver_epoch": 0}
    current = {"box": (130, 196, 140, 202, 0.5)}

    class Detector:
        def status(self):
            return {"sha256": sha}

        def detect(self, _payload, corridor, perception, *, evidence, geometry):
            result = assess_depth_for_session(
                current, corridor, perception, evidence, geometry
            )
            result["_navigation_maps"]["gray"] = np.random.default_rng(42).integers(
                0, 255, (240, 320), dtype=np.uint8
            )
            result["overlay_geometry"] = analysis_geometry((320, 240), result, corridor)
            result.pop("_errors")
            return result

    loop = VisionLoop(clock=lambda: now[0])
    loop.depth_detector = Detector()
    received = {
        "journey": journey,
        "correction_after": None,
        "outputs": [],
        "latched": False,
    }
    turns = []
    try:
        for sequence in range(1, 91):
            epoch = received["journey"]["maneuver_epoch"]
            if epoch >= 2:
                break
            # The second obstacle is on the other side, in the same mission.
            count = len([row for row in received["outputs"] if row[0] == "turn"])
            current["box"] = (
                None
                if count > epoch
                else (
                    (130, 196, 140, 202, 0.5)
                    if epoch == 0
                    else (180, 196, 190, 202, 0.5)
                )
            )
            waiting = (
                received["correction_after"] is not None
                and now[0] <= received["correction_after"]
            )
            frame = VisionFrame(
                protocol_version=3,
                run_id="offline",
                frame_id=f"f{sequence}",
                sequence=sequence,
                config_revision=7,
                age_ms=0.0,
                deadline_ms=300,
                mode="drive",
                verified=True,
                calibration_id="offline",
                model_sha256=sha,
                perception=PERCEPTION,
                behavior=behavior,
                avoidance=config,
                motion_phase="steer_wait" if waiting else "ready",
                journey={**journey, "maneuver_epoch": epoch},
            )
            reply = await loop.decide("fixture", frame, b"offline")
            assert len(json.dumps(reply).encode()) < 16384
            replies.append({"decision": reply, "captured": now[0]})
            completed = subprocess.run(
                [
                    pi_python,
                    str(Path(pi_root) / "tests/fixtures/replay_collision_lease.py"),
                ],
                input=json.dumps(
                    {"config": policy, "journey": journey, "replies": replies}
                ),
                text=True,
                capture_output=True,
                check=True,
                timeout=15,
            )
            received = json.loads(completed.stdout)
            assert not received["latched"], (reply, received)
            assert not any(row[0] == "steer" for row in received["outputs"])
            if waiting:
                assert reply["action"] == "stop"
            now[0] += 0.07
        turns = [row[1] for row in received["outputs"] if row[0] == "turn"]
        assert turns == (["left", "right"] if mirrored else ["right", "left"])
        assert received["journey"]["maneuver_epoch"] == 2
        assert received["journey"]["state"] == "forward"
    finally:
        await loop.close()


def assess_depth_for_session(current, corridor, perception, evidence, geometry):
    from venom_module_rider.depth_detector import assess_depth

    boxes = [current["box"]] if current["box"] else []
    return assess_depth(
        scene(*boxes),
        corridor,
        perception,
        evidence=evidence,
        collision=geometry.collision,
    )


def test_temporal_slow_cannot_replace_guidance_stop_or_rotation():
    from venom_module_rider.vision_loop import _apply_temporal_decision

    config = guided()
    result = assess(scene(FRONT), config)
    result["guidance"] = {"version": 1, "motion_mode": "rotate_then_drive"}
    for action, reason in [
        ("stop", "avoidance_stop_before_rotation"),
        ("turn_left", "avoidance_rotate"),
    ]:
        signals = SimpleNamespace(
            action=action, reason=reason, vision_action="stop", permitted=True
        )
        run = SimpleNamespace(count=3, frame=SimpleNamespace(motion_phase="ready"))
        _apply_temporal_decision(
            run, result, signals, {"action": "slow", "reason": "noise"}
        )
        assert signals.action == "stop"


def test_vetoed_rotation_does_not_consume_an_attempt_or_create_progress_evidence():
    from venom_module_rider.vision_loop import _reconcile_guidance

    run = SimpleNamespace(state={"avoidance": {"turns": 2, "pending_exit": 0.5}})
    result = {"turn_count": 2, "maneuver_clear_count": 3}
    _reconcile_guidance(
        run,
        result,
        SimpleNamespace(action="stop", reason="temporal_uncertainty"),
        "turn_left",
        {"turns": 1},
    )
    assert run.state["avoidance"]["turns"] == result["turn_count"] == 1
    assert "pending_exit" not in run.state["avoidance"]
    assert result["maneuver_clear_count"] == 0


def test_vetoed_legacy_collision_steer_is_not_counted_as_executed():
    from venom_module_rider.vision_loop import _reconcile_guidance

    run = SimpleNamespace(state={"avoidance": {"turns": 2}})
    result = {"turn_count": 2, "maneuver_clear_count": 3}
    _reconcile_guidance(
        run,
        result,
        SimpleNamespace(action="stop", reason="temporal_uncertainty"),
        "steer_left",
        {"turns": 1},
    )
    assert run.state["avoidance"]["turns"] == result["turn_count"] == 1
    assert "guidance_state" not in result


@pytest.mark.parametrize(
    "patch",
    [
        {"version": True},
        {"motion_mode": "guess"},
        {"center_deadband": -1},
        {"rotation_bounds": [0.4, 0.9, 0.6, 0.95]},
    ],
)
def test_invalid_guidance_is_rejected(patch):
    if "rotation_bounds" in patch:
        config = guided(**patch)
        with pytest.raises(ValueError):
            config.geometry.collision.validate_corridor(ROI)
    else:
        with pytest.raises(ValueError):
            guided(**patch)


def test_rotation_overlay_roundtrips_through_saved_analysis_validator():
    from venom_module_rider.overlay_geometry import analysis_geometry, validate_geometry

    result = assess(scene(FRONT), guided())
    drawing = analysis_geometry((320, 240), result, ROI)
    assert drawing["paths"]["rotation"]
    assert drawing["axis"]
    assert validate_geometry(drawing, (320, 240)) == drawing
    drawing["axis"][0][1] = 240
    with pytest.raises(ValueError, match="vision_overlay_geometry_invalid"):
        validate_geometry(drawing, (320, 240))


def test_axis_overlay_at_source_edge_stays_inside_source_pixels():
    from venom_module_rider.overlay_geometry import analysis_geometry, validate_geometry

    result = assess(scene(FRONT), guided())
    drawing = analysis_geometry((320, 240), result, (0.35, 0.999, 0.65, 1.0))
    assert all(0 <= point[1] < 240 for point in drawing["axis"])
    validate_geometry(drawing, (320, 240))


@pytest.mark.parametrize(
    "pi,host", [(False, False), (True, False), (False, True), (True, True)]
)
def test_guidance_panel_requires_support_on_both_peers(pi, host):
    from venom_module_rider.api.vision_routes import profile_presentation

    shown = profile_presentation({"axis_guidance_v1": pi}, {"axis_guidance_v1": host})
    assert shown["axis_guidance_supported"] is (pi and host)


@pytest.mark.parametrize("mode,accepted", [("observe", True), ("drive", False)])
def test_rotation_only_input_must_cover_body_envelope_before_drive(mode, accepted):
    from test_collision_geometry import PERCEPTION

    from venom_module_rider.vision_contract import VisionFrame

    perception = PERCEPTION.model_dump()
    perception["input_region"] = {"version": 1, "bounds": [0.35, 0.65, 0.65, 0.95]}
    payload = dict(
        protocol_version=3,
        run_id="offline",
        frame_id="frame",
        sequence=1,
        config_revision=1,
        age_ms=0.0,
        deadline_ms=300,
        mode=mode,
        corridor=ROI,
        avoidance=guided(),
        perception=perception,
    )
    if accepted:
        VisionFrame(**payload)
    else:
        with pytest.raises(ValueError, match="navigation_rotation_input_incomplete"):
            VisionFrame(**payload)
