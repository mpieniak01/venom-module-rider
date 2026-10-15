"""Adaptive geometric tensor -> real Venom loop -> real Pi permit; no motors."""

import importlib.util
import json
import os
from copy import deepcopy

import pytest
from test_adaptive_ground import CAMERA, SETTINGS, scene

from venom_module_rider.adaptive_ground import assess
from venom_module_rider.avoidance import AvoidanceSettings
from venom_module_rider.vision_contract import VisionFrame
from venom_module_rider.vision_loop import VisionLoop

SHA = "a" * 64


@pytest.mark.parametrize("side", [-1, 1])
async def test_ground_loop_to_actual_pi_steers_then_stops_without_progress(
    side, monkeypatch
):
    source = os.getenv("RIDER_TEST_PI_LEASE")
    if not source:
        pytest.skip("Explicit Pi lease source required; never physical motors")
    spec = importlib.util.spec_from_file_location("ground_pi_lease", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    now, outputs = [0.0], []
    geometry = CAMERA.model_dump()
    settings = AvoidanceSettings(
        enabled=True, sweep_verified=True, max_turn_pulses=5, geometry=geometry
    )
    x = side * 0.4
    depth = scene(box=((x - 0.12, x + 0.12), (0.9, 1.1), (0, 0.5)))
    row = assess(depth, (0.35, 0.8, 0.65, 0.95), SETTINGS, CAMERA)
    row.pop("_errors")
    row.pop("_path_overlay", None)
    row["_trace_depth"] = depth
    traces = []
    monkeypatch.setattr(
        "venom_module_rider.vision_loop.depth_trace.submit",
        lambda _payload, _depth, assessment, *_args: traces.append(assessment),
    )

    class Detector:
        def status(self):
            return {"sha256": SHA}

        def detect_ground(self, _payload, _roi, _settings, camera):
            assert camera == CAMERA
            return deepcopy(row)

    loop = VisionLoop(clock=lambda: now[0])
    loop.depth_detector = Detector()
    policy = dict(
        revision=0,
        mode="drive",
        verified=True,
        model_sha256=SHA,
        speed=0.1,
        max_motion_ms=150,
        decision_budget_ms=300,
        avoidance=settings.model_dump(),
    )
    lease = module.DecisionLease(
        policy,
        "run",
        lambda *args: outputs.append(("go", *args)),
        lambda: outputs.append(("halt",)),
        clock=lambda: now[0],
        pause=lambda *args: outputs.append(("zero", *args)),
        steer=lambda *args: outputs.append(("steer", *args)),
        feedback_wait=True,
    )
    for seq in range(1, 11):
        frame = VisionFrame(
            protocol_version=3,
            run_id="run",
            frame_id=f"f{seq}",
            sequence=seq,
            config_revision=0,
            age_ms=0.0,
            deadline_ms=300,
            mode="drive",
            verified=True,
            calibration_id="fixture",
            model_sha256=SHA,
            perception=SETTINGS,
            avoidance=settings,
        )
        reply = await loop.decide("fixture", frame, b"fixture")
        assert len(json.dumps(reply)) < 8000  # Pi body and preview header stay bounded.
        lease.accept(reply, frame.frame_id, seq, now[0])
        now[0] += 0.07
        if lease.latched:
            break
    curves = [o for o in outputs if o[0] == "steer"]
    assert curves == [("steer", "right" if side < 0 else "left", 0.1)] * 2
    assert lease.latched and lease.reason == "avoidance_no_progress"
    assert outputs[-1] == ("halt",)
    assert traces and traces[-1]["avoidance"]["geometry"] == geometry
    assert len(traces[-1]["heading_candidates"]) == 49
    await loop.close()


async def test_missing_calibration_cannot_build_a_positive_history():
    class Detector:
        def status(self):
            return {"sha256": SHA}

        def detect_ground(self, _payload, roi, settings, camera):
            result = assess(scene(), roi, settings, camera)
            result.pop("_errors")
            return result

    loop = VisionLoop()
    loop.depth_detector = Detector()
    for seq in range(1, 5):
        frame = VisionFrame(
            protocol_version=3,
            run_id="run",
            frame_id=f"f{seq}",
            sequence=seq,
            config_revision=0,
            age_ms=0.0,
            deadline_ms=300,
            perception=SETTINGS,
            avoidance={"enabled": True, "geometry": {"mode": "ground"}},
        )
        result = await loop.decide("fixture", frame, b"fixture")
        assert result["action"] == "stop"
        assert result["reason"] == "geometry_calibration_missing"
        assert result["clear_count"] == 0
    await loop.close()
