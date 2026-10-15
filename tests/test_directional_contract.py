"""Real Venom loop -> real Pi permit, outputs recorded instead of hardware."""

import importlib.util
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from venom_module_rider.avoidance import AvoidanceSettings, plan
from venom_module_rider.sector_trend import update
from venom_module_rider.vision_contract import VisionFrame
from venom_module_rider.vision_loop import VisionLoop

SHA = "a" * 64


def frame(seq, phase="ready"):
    return VisionFrame(
        protocol_version=2,
        run_id="run",
        frame_id="f" + str(seq),
        sequence=seq,
        config_revision=0,
        age_ms=0.0,
        deadline_ms=300,
        mode="drive",
        verified=True,
        calibration_id="accepted",
        model_sha256=SHA,
        perception={"engine": "depth_plane"},
        avoidance={"enabled": True, "sweep_verified": True},
        motion_phase=phase,
    )


class Detector:
    state = "obstacle"

    def status(self):
        return {"sha256": SHA}

    def detect(self, *_):
        return {
            "state": self.state,
            "reason": "depth_ground_deviation",
            "farther_outlier_pixels": 0,
            "sectors": {
                "left": {
                    "state": "obstacle",
                    "coverage_complete": True,
                    "outlier_fraction": 0.2,
                },
                "right": {
                    "state": "clear",
                    "coverage_complete": True,
                    "outlier_fraction": 0.0,
                },
            },
        }


async def test_venom_to_actual_pi_lease_stop_turn_new_frames_forward():
    source = os.getenv("RIDER_TEST_PI_LEASE")
    if not source:
        pytest.skip("Explicit Pi lease source required; never access physical motors")
    spec = importlib.util.spec_from_file_location("pi_directional_lease", Path(source))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    clock = [0.0]
    outputs = []
    policy = dict(
        revision=0,
        mode="drive",
        verified=True,
        model_sha256=SHA,
        speed=0.1,
        max_motion_ms=150,
        decision_budget_ms=300,
        avoidance=AvoidanceSettings(enabled=True, sweep_verified=True).model_dump(),
    )
    lease = module.DecisionLease(
        policy,
        "run",
        lambda *args: outputs.append(("go", *args)),
        lambda: outputs.append(("halt",)),
        lambda: clock[0],
        pause=lambda duration: outputs.append(("zero", duration)),
        turn=lambda *args: outputs.append(("turn", *args)),
        feedback_wait=True,
    )
    loop = VisionLoop(Detector(), clock=lambda: clock[0])
    loop.depth_detector = Detector()
    for seq, phase in enumerate(["ready"] * 3 + ["turn_settling"] + ["ready"] * 3, 1):
        if seq >= 5:
            loop.depth_detector.state = "clear"
        result = await loop.decide("fixture", frame(seq, phase), b"fixture")
        lease.accept(result, "f" + str(seq), seq, clock[0])
        clock[0] += 0.07
    assert [row[0] for row in outputs] == [
        "zero",
        "zero",
        "turn",
        "zero",
        "zero",
        "zero",
        "go",
    ]
    assert outputs[2][1:] == ("right", 0.1)
    assert outputs[-1][1] == 0.1
    clock[0] += 0.5
    assert not lease.tick()
    assert outputs[-1] == ("halt",)
    await loop.close()


@pytest.mark.parametrize(
    "changes", [{"protocol_version": 1}, {"perception": {"engine": "segformer"}}]
)
def test_enabled_avoidance_requires_explicit_v2_depth(changes):
    values = frame(1).model_dump()
    values.update(changes)
    with pytest.raises(ValidationError, match="avoidance_requires_depth_protocol_v2"):
        VisionFrame(**values)


def test_planner_does_not_mutate_previous_counts():
    previous = {"counts": {"left": 1, "right": 1}, "turns": 0}
    plan(Detector().detect(), previous, AvoidanceSettings(), "ready", False)
    assert previous["counts"] == {"left": 1, "right": 1}


def test_occupancy_trend_requires_three_valid_samples_and_resets_unknown():
    history = None
    for fraction in [0.01, 0.03, 0.06]:
        sectors, history = update(
            {"left": {"state": "obstacle", "outlier_fraction": fraction}}, history
        )
    assert sectors["left"]["occupancy_trend"] == "growing"
    sectors, history = update({"left": {"state": "unknown"}}, history)
    assert sectors["left"]["occupancy_trend"] == "insufficient"
    assert history["left"] == []
    for fraction in [0.06, 0.03, 0.01]:
        sectors, history = update(
            {"left": {"state": "obstacle", "outlier_fraction": fraction}}, history
        )
    assert sectors["left"]["occupancy_trend"] == "receding"
    sectors, _ = update(
        {"left": {"state": "obstacle", "outlier_fraction": 0.01}}, history
    )
    assert sectors["left"]["occupancy_trend"] == "stable"


async def test_settling_and_gap_do_not_reuse_front_clear_history():
    now = [0.0]
    detector = Detector()
    detector.state = "clear"
    loop = VisionLoop(detector, clock=lambda: now[0])
    loop.depth_detector = detector
    for seq in range(1, 4):
        result = await loop.decide("fixture", frame(seq), b"fixture")
    assert result["action"] == "go"
    result = await loop.decide("fixture", frame(4, "turn_settling"), b"fixture")
    assert result["action"] == "stop"
    result = await loop.decide("fixture", frame(5), b"fixture")
    assert result["action"] == "stop"
    now[0] += 0.6
    result = await loop.decide("fixture", frame(6), b"fixture")
    assert result["action"] == "stop"
    await loop.close()
