"""Collision envelopes must qualify the actual maneuver, not adjacent rectangles."""

from copy import deepcopy

import numpy as np
import pytest

from venom_module_rider.avoidance import AvoidanceSettings
from venom_module_rider.depth_detector import PerceptionSettings, assess_depth
from venom_module_rider.navigation_maps import assess_evidence
from venom_module_rider.navigation_profiles import parameters
from venom_module_rider.overlay_geometry import analysis_geometry
from venom_module_rider.steering import plan

ROI = (0.35, 0.8, 0.65, 0.95)
COLLISION = {
    "version": 1,
    "far_width_ratio": 1.0,
    "left_shift": 0.30,
    "right_shift": 0.30,
    "end_fraction": 0.2,
    "reference_corridor": [0.35, 0.65, 0.65, 0.95],
}
PERCEPTION = PerceptionSettings(engine="depth_plane")


def settings(**changes):
    return AvoidanceSettings(
        enabled=True,
        sweep_verified=True,
        geometry={"mode": "image", "collision": {**COLLISION, **changes}},
    )


def scene(*boxes):
    y, x = np.mgrid[:240, :320]
    inverse = np.maximum(0.1 * x / 320 + 3.5 * y / 240 - 2, 0.1)
    for x0, y0, x1, y1, delta in boxes:
        inverse[y0:y1, x0:x1] += delta
    return 1 / inverse


FRONT = (155, 196, 165, 202, 0.5)
LATERAL = [(20, 213, 30, 220, 0.5), (290, 213, 300, 220, 0.5)]


def assess(depth, config=None, **kwargs):
    return assess_depth(
        depth,
        ROI,
        PERCEPTION,
        collision=(config or settings()).geometry.collision,
        **kwargs,
    )


def decision(result, config=None):
    state = None
    for _ in range(3):
        action, reason, state, *_ = plan(
            deepcopy(result), state, config or settings(), "ready", True, corridor=ROI
        )
    return action, reason


def test_adjacent_rectangles_do_not_veto_observed_free_maneuvers():
    depth = scene(FRONT, *LATERAL)
    old = assess_depth(depth, ROI, PERCEPTION)
    legacy = AvoidanceSettings(enabled=True, sweep_verified=True)
    assert decision(old, legacy) == ("stop", "avoidance_no_clear_side")
    result = assess(depth)
    assert result["sectors"]["left"]["state"] == "obstacle"
    assert result["sectors"]["right"]["state"] == "obstacle"
    assert result["paths"]["left"]["state"] == "clear"
    assert result["paths"]["right"]["state"] == "clear"
    assert decision(result) == ("steer_left", "avoidance_steer")


def test_lateral_only_does_not_trigger_a_turn():
    assert decision(assess(scene(*LATERAL)))[0] == "go"


@pytest.mark.parametrize(
    "box,expected",
    [
        ((20, 194, 30, 200, 0.5), "steer_right"),  # terminal footprint
        ((290, 194, 300, 200, 0.5), "steer_left"),
        ((94, 211, 104, 217, 0.5), "steer_right"),  # swept body
    ],
)
def test_obstacle_inside_maneuver_still_blocks_that_side(box, expected):
    assert decision(assess(scene(FRONT, box)))[0] == expected


@pytest.mark.parametrize("delta", [0.5, -0.3])
def test_near_obstacle_or_drop_never_authorizes_a_maneuver(delta):
    result = assess(scene(FRONT, (155, 219, 165, 226, delta)))
    assert decision(result)[0] == "stop"


def test_out_of_frame_path_is_unknown_not_clipped_clear():
    result = assess(scene(FRONT), settings(left_shift=0.5, right_shift=0.5))
    assert result["paths"]["left"]["coverage_complete"] is False
    assert result["paths"]["right"]["coverage_complete"] is False
    assert decision(result, settings(left_shift=0.5, right_shift=0.5))[0] == "stop"


def test_missing_pixels_in_a_path_are_unknown():
    depth = scene(FRONT)
    depth[:, :60] = np.nan
    result = assess(depth, input_bounds=(60, 0, 320, 240))
    assert result["paths"]["left"]["coverage_complete"] is False
    assert decision(result)[0] == "steer_right"


def test_forward_taper_does_not_shrink_ground_reference():
    config = settings(far_width_ratio=0.5)
    depth = scene((114, 193, 120, 201, 0.5))
    result = assess(depth, config, evidence=True)
    assert result["state"] == "clear"
    assert result["plane_reference_corridor"] == (0.35, 0.65, 0.65, 0.95)
    params = parameters({"version": 1, "profile_id": "fluid", "profile_revision": 1})
    spatial = assess_evidence(result["_navigation_maps"], ROI, params)
    assert spatial["candidate"] == "clear"
    assert spatial["counts"]["corridor_pixels"] == result["zone_pixels"]


def test_overlay_contains_the_scored_paths_not_rectangular_sectors():
    result = assess(scene(FRONT))
    overlay = analysis_geometry((320, 240), result, ROI)
    assert overlay["paths"]["left"] != [
        [16, 192],
        [111, 192],
        [111, 227],
        [16, 227],
        [16, 192],
    ]
    assert overlay["paths"]["center"]
    assert overlay["near"]
    assert overlay["reference_bounds"] == [112, 156, 208, 228]


def test_foreign_geometry_cannot_reuse_clear_side_history():
    result = assess(scene(FRONT))
    result["collision_geometry"]["sha256"] = "0" * 64
    assert decision(result) == ("stop", "collision_geometry_mismatch")


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"version": 2},
        {"far_width_ratio": 0},
        {"left_shift": float("nan")},
        {"end_fraction": 0},
    ],
)
def test_invalid_geometry_is_rejected(change):
    with pytest.raises(ValueError):
        settings(**change)


@pytest.mark.parametrize("mirrored", [False, True])
async def test_real_loop_and_pi_qualify_same_envelope(monkeypatch, mirrored):
    import json
    import os
    import subprocess
    from pathlib import Path

    from venom_module_rider.vision_contract import VisionFrame
    from venom_module_rider.vision_loop import VisionLoop

    pi_root = os.getenv("RIDER_COLLISION_PI_ROOT")
    pi_python = os.getenv("RIDER_COLLISION_PI_PYTHON")
    if not pi_root or not pi_python:
        pytest.skip(
            "Explicit Pi checkout and interpreter required; never physical motors"
        )
    config = settings()
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

    class Detector:
        def status(self):
            return {"sha256": sha}

        def detect(self, _payload, corridor, perception, *, evidence, geometry):
            result = assess_depth(
                scene(FRONT, *LATERAL, (290, 194, 300, 200, 0.5)),
                corridor,
                perception,
                evidence=evidence,
                collision=geometry.collision,
            )
            result["_navigation_maps"]["gray"] = np.random.default_rng(42).integers(
                0, 255, (240, 320), dtype=np.uint8
            )
            result["overlay_geometry"] = analysis_geometry((320, 240), result, corridor)
            result.pop("_errors")
            return result

    loop = VisionLoop(clock=lambda: now[0])
    loop.depth_detector = Detector()
    for sequence in range(1, 4):
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
        )
        reply = await loop.decide("fixture", frame, b"offline")
        replies.append({"decision": reply, "captured": now[0]})
        now[0] += 0.07
    assert reply["temporal"]["state"] == "obstacle"
    assert reply["temporal"]["near_guard_clear"] is True
    completed = subprocess.run(
        [pi_python, str(Path(pi_root) / "tests/fixtures/replay_collision_lease.py")],
        input=json.dumps({"config": policy, "replies": replies}),
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    received = json.loads(completed.stdout)
    assert received["outputs"][-1] == ["steer", "right" if mirrored else "left", 0.1]
    assert not received["latched"]
    assert reply["sectors"]["left"]["state"] == "obstacle"
    assert reply["sectors"]["right"]["state"] == "obstacle"
    await loop.close()


def test_identity_or_no_progress_cannot_replenish_maneuver_budget():
    result = assess(scene(FRONT, *LATERAL))
    state = None
    turns = []
    for _ in range(10):
        action, reason, state, _, _, terminal = plan(
            deepcopy(result), state, settings(), "ready", True, corridor=ROI
        )
        if action.startswith("steer_"):
            turns.append(action)
    assert len(turns) == 3
    assert reason == "avoidance_no_progress"
    assert terminal


@pytest.mark.parametrize(
    "changes", [{"mode": "ground"}, {"collision": {"version": True}}]
)
def test_ground_or_unknown_contract_never_silently_falls_back(changes):
    from venom_module_rider.ground_config import GroundGeometry

    with pytest.raises(ValueError):
        GroundGeometry.model_validate(
            {"mode": "image", "collision": COLLISION, **changes}
        )


def test_near_veto_cannot_be_overridden_by_temporal_slow():
    from types import SimpleNamespace

    from venom_module_rider.vision_loop import _apply_temporal_decision

    result = assess(scene())
    result["paths"]["guard"]["state"] = "obstacle"
    assert decision(result) == ("stop", "avoidance_near_guard")
    signals = SimpleNamespace(
        action="stop", reason="avoidance_near_guard", vision_action="go", permitted=True
    )
    run = SimpleNamespace(count=3, frame=SimpleNamespace(motion_phase="ready"))
    _apply_temporal_decision(
        run, result, signals, {"action": "slow", "reason": "noise"}
    )
    assert signals.action == signals.vision_action == "stop"


def test_temporal_slow_cannot_bypass_a_blocked_collision_corridor():
    from types import SimpleNamespace

    from venom_module_rider.vision_loop import _apply_temporal_decision

    result = assess(scene(FRONT))
    assert result["paths"]["center"]["state"] == "obstacle"
    signals = SimpleNamespace(
        action="stop",
        reason="avoidance_no_clear_maneuver",
        vision_action="go",
        permitted=True,
    )
    run = SimpleNamespace(count=3, frame=SimpleNamespace(motion_phase="ready"))
    _apply_temporal_decision(
        run, result, signals, {"action": "slow", "reason": "noise"}
    )
    assert signals.action == signals.vision_action == "stop"


def test_raster_export_uses_the_same_path_geometry():
    import io

    from PIL import Image

    from venom_module_rider.analysis_layers import classification_layer
    from venom_module_rider.depth_detector import render_depth

    result = assess(scene(FRONT))
    png, _ = classification_layer(
        (320, 240), deepcopy(result), ROI, PERCEPTION, "fixture.png", "a" * 64
    )
    assert Image.open(io.BytesIO(png)).size == (320, 240)
    jpeg = render_depth(
        Image.new("RGB", (320, 240)), scene(FRONT), result, ROI, PERCEPTION
    )
    assert Image.open(io.BytesIO(jpeg)).size == (320, 240)
    assert result["overlay_geometry"]["paths"]["left"]


def test_source_resolution_preserves_relative_corridor_area():
    config = settings(far_width_ratio=0.5)
    small = assess(scene(), config)
    large = assess(np.repeat(np.repeat(scene(), 2, axis=0), 2, axis=1), config)
    assert small["state"] == large["state"] == "clear"
    assert abs(large["zone_pixels"] / small["zone_pixels"] - 4) < 0.1


def test_host_and_pi_capabilities_are_both_required():
    from venom_module_rider.api.vision_routes import profile_presentation

    assert (
        profile_presentation({"collision_geometry_v1": True}, {})[
            "collision_geometry_supported"
        ]
        is False
    )
    assert (
        profile_presentation({}, {"collision_geometry_v1": True})[
            "collision_geometry_supported"
        ]
        is False
    )
    assert (
        profile_presentation(
            {"collision_geometry_v1": True}, {"collision_geometry_v1": True}
        )["collision_geometry_supported"]
        is True
    )


@pytest.mark.parametrize(
    "geometry", [{"collision": None}, {"mode": "ground", "collision": COLLISION}]
)
def test_explicit_invalid_geometry_is_not_interpreted_as_legacy(geometry):
    from venom_module_rider.ground_config import GroundGeometry

    with pytest.raises(ValueError):
        GroundGeometry.model_validate(geometry)


def test_crop_rejects_a_missing_floor_reference_before_inference():
    from venom_module_rider.vision_contract import VisionFrame

    with pytest.raises(ValueError, match="navigation_input_region_excludes_reference"):
        VisionFrame(
            protocol_version=3,
            run_id="offline",
            frame_id="f1",
            sequence=1,
            config_revision=0,
            age_ms=0.0,
            deadline_ms=300,
            avoidance=settings(reference_corridor=[0.2, 0.5, 0.8, 0.95]),
            perception={
                "engine": "depth_plane",
                "input_region": {"version": 1, "bounds": [0.3, 0.6, 0.7, 1.0]},
            },
        )


@pytest.mark.parametrize(
    "capture_evidence,video", [(False, False), (True, False), (False, True)]
)
def test_offline_near_zone_matches_live_policy(capture_evidence, video):
    from types import SimpleNamespace

    from venom_module_rider.analysis_worker import _worker_depth_geometry

    behavior = {
        "version": 1,
        "profile_id": "balanced",
        "profile_revision": 2,
        "overrides": {"near_zone_fraction": 0.5},
    }
    config = settings()
    state = SimpleNamespace(
        geometry=config.geometry,
        depth=scene(),
        spec={"corridor": ROI},
        settings=PERCEPTION,
        box=None,
        behavior=behavior,
        capture_evidence=capture_evidence,
        video=video,
    )
    _worker_depth_geometry(state)
    live = assess(scene(), evidence=parameters(behavior, PERCEPTION.model_dump()))
    default = assess(scene())
    assert state.assessment["paths"] == live["paths"]
    assert (
        live["paths"]["guard"]["zone_pixels"]
        != default["paths"]["guard"]["zone_pixels"]
    )
    assert ("_navigation_maps" in state.assessment) is (capture_evidence or video)
