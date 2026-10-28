"""Offline v3 maneuvers: separate destination and actual image corridor evidence."""

import importlib.util
import os
from copy import deepcopy

import numpy as np
import pytest

from venom_module_rider.avoidance import AvoidanceSettings
from venom_module_rider.depth_detector import PerceptionSettings, assess_depth
from venom_module_rider.steering import plan

SETTINGS = AvoidanceSettings(
    enabled=True, sweep_verified=True, geometry={"mode": "image"}
)


def obstacle(side="left"):
    clear = {"state": "clear", "coverage_complete": True, "outlier_fraction": 0.0}
    blocked = {**clear, "state": "obstacle", "outlier_fraction": 0.2}
    return {
        "state": "obstacle",
        "reason": "depth_ground_deviation",
        "farther_outlier_pixels": 0,
        "sectors": {side: blocked, ("right" if side == "left" else "left"): clear},
        "paths": {"left": dict(clear), "right": dict(clear), "guard": dict(clear)},
    }


@pytest.mark.parametrize(
    "side,expected", [("left", "steer_right"), ("right", "steer_left")]
)
def test_opposite_clear_path_then_stop_after_bounded_corrections(side, expected):
    state = None
    actions = []
    for _ in range(10):
        action, reason, state, _, _, terminal = plan(
            obstacle(side), state, SETTINGS, "ready", True
        )
        actions.append(action)
        if terminal:
            break
    assert actions == [
        "stop",
        "stop",
        expected,
        "stop",
        "stop",
        expected,
        "stop",
        "stop",
        expected,
        "stop",
    ]
    assert terminal
    assert reason == "avoidance_no_progress"


@pytest.mark.parametrize(
    "where", ["side", "path", "guard", "recess", "unknown", "settling"]
)
def test_free_side_alone_does_not_authorize_a_curve(where):
    result = obstacle()
    if where == "side":
        result["sectors"]["right"]["state"] = "unknown"
    if where == "path":
        result["paths"]["right"]["state"] = "obstacle"
    if where == "guard":
        result["paths"]["guard"]["state"] = "obstacle"
    if where == "recess":
        result["farther_outlier_pixels"] = 1
    if where == "unknown":
        result["state"] = "unknown"
    state = None
    for _ in range(5):
        action, _, state, _, _, _ = plan(
            result,
            state,
            SETTINGS,
            "steer_wait" if where == "settling" else "ready",
            True,
        )
        assert action == "stop"


def test_mirroring_and_fresh_clear_straighten_without_resetting_budget():
    state = None
    settings = SETTINGS.model_copy(update={"mirrored": True})
    for _ in range(3):
        action, _, state, _, _, _ = plan(obstacle(), state, settings, "ready", True)
    assert action == "steer_left"
    for index in range(3):
        result = {**obstacle(), "state": "clear"}
        action, _, state, _, _, _ = plan(result, state, settings, "ready", True)
        assert action == ("go" if index == 2 else "stop")
    assert state["turns"] == 1


def test_obstacle_in_swept_path_is_not_hidden_by_clear_destination():
    y, x = np.mgrid[:240, :320]
    inverse = np.maximum(0.1 * x / 320 + 3.5 * y / 240 - 2, 0.1)
    inverse[209:213, 197:202] += 0.5
    result = assess_depth(
        1 / inverse, (0.35, 0.8, 0.65, 0.95), PerceptionSettings(engine="depth_plane")
    )
    assert result["sectors"]["right"]["state"] == "clear"
    assert result["paths"]["right"]["state"] == "obstacle"


async def test_real_vision_loop_and_pi_lease_curve_then_terminal_stop():
    source = os.getenv("RIDER_TEST_PI_LEASE")
    if not source:
        pytest.skip("Explicit actual Pi lease fixture required; no hardware")
    from venom_module_rider.vision_contract import VisionFrame
    from venom_module_rider.vision_loop import VisionLoop

    spec = importlib.util.spec_from_file_location("steering_pi", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    now, outputs = [0.0], []

    class Detector:
        def status(self):
            return {"sha256": "a" * 64}

        def detect(self, *_):
            return deepcopy(obstacle())

    loop = VisionLoop(Detector(), clock=lambda: now[0])
    loop.depth_detector = Detector()
    policy = {
        "revision": 0,
        "mode": "drive",
        "verified": True,
        "model_sha256": "a" * 64,
        "speed": 0.1,
        "max_motion_ms": 150,
        "decision_budget_ms": 300,
        "avoidance": SETTINGS.model_dump(),
    }
    lease = module.DecisionLease(
        policy,
        "run",
        lambda *x: outputs.append(("go", *x)),
        lambda: outputs.append(("halt",)),
        clock=lambda: now[0],
        pause=lambda *x: outputs.append(("zero", *x)),
        steer=lambda *x: outputs.append(("steer", *x)),
        feedback_wait=True,
    )
    for seq in range(1, 11):
        f = VisionFrame(
            protocol_version=3,
            run_id="run",
            frame_id="f" + str(seq),
            sequence=seq,
            config_revision=0,
            age_ms=0.0,
            deadline_ms=300,
            mode="drive",
            verified=True,
            calibration_id="fixture",
            model_sha256="a" * 64,
            perception={"engine": "depth_plane"},
            avoidance=SETTINGS,
        )
        row = await loop.decide("fixture", f, b"fixture")
        lease.accept(row, f.frame_id, seq, now[0])
        now[0] += 0.07
    assert [row for row in outputs if row[0] == "steer"] == [
        ("steer", "right", 0.1)
    ] * 3
    assert outputs[-1] == ("halt",)
    assert lease.reason == "avoidance_no_progress"
    assert lease.latched
    await loop.close()
