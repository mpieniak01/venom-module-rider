"""The measured image reference changes actual scoring, not just the overlay."""

from copy import deepcopy

import numpy as np
import pytest
from test_collision_geometry import PERCEPTION, ROI, scene, settings

from venom_module_rider.depth_detector import assess_depth
from venom_module_rider.overlay_geometry import analysis_geometry
from venom_module_rider.steering import plan

# Same fixture as web reference_corridor.test.ts: saved Pi image reference,
# 11.5 cm robot width plus 1 cm per side, at y=0.80–0.95.
MEASURED = (0.320263, 0.8, 0.675029, 0.95)


def evaluate(depth, corridor, config):
    result = assess_depth(
        depth, corridor, PERCEPTION, collision=config.geometry.collision
    )
    state = None
    for _ in range(3):
        action, _, state, *_ = plan(
            deepcopy(result), state, config, "ready", True, corridor=corridor
        )
    return result, action


def test_real_scoring_uses_measured_width_without_blocking_unrelated_side_objects():
    config = settings(
        far_width_ratio=0.811906,
        reference_corridor=[MEASURED[0], 0.65, MEASURED[2], 0.95],
    )
    obstacle = scene((106, 215, 112, 221, 0.5))
    old, _ = evaluate(obstacle, ROI, settings())
    measured, action = evaluate(obstacle, MEASURED, config)
    assert old["state"] == "clear"
    assert measured["state"] == "obstacle"
    assert measured["axis_evidence"]["obstacle_side"] == "left"
    assert action == "stop"  # Close obstacle also enters the independent near guard.
    outside, action = evaluate(scene((60, 196, 67, 202, 0.5)), MEASURED, config)
    assert outside["state"] == "clear"
    assert action == "go"


def test_perspective_taper_does_not_stop_for_side_object_outside_body_projection():
    reference = [MEASURED[0], 0.65, MEASURED[2], 0.95]
    depth = scene((104, 196, 111, 202, 0.5))
    wide, _ = evaluate(depth, MEASURED, settings(reference_corridor=reference))
    tapered, action = evaluate(
        depth,
        MEASURED,
        settings(far_width_ratio=0.811906, reference_corridor=reference),
    )
    assert wide["state"] == "obstacle"
    assert tapered["state"] == "clear"
    assert action == "go"


@pytest.mark.parametrize("mirrored", [False, True])
async def test_measured_corridor_flows_through_real_module_and_pi_lease(mirrored):
    import json
    import os
    import subprocess
    from pathlib import Path

    from venom_module_rider.vision_contract import VisionFrame
    from venom_module_rider.vision_loop import VisionLoop

    pi_root = os.getenv("RIDER_COLLISION_PI_ROOT")
    pi_python = os.getenv("RIDER_COLLISION_PI_PYTHON")
    if not pi_root or not pi_python:
        pytest.skip("Explicit Pi checkout/interpreter required; no physical motion")
    config = settings(
        far_width_ratio=0.811906,
        reference_corridor=[MEASURED[0], 0.65, MEASURED[2], 0.95],
    )
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
        "corridor": list(MEASURED),
        "avoidance": config.model_dump(),
        "perception": PERCEPTION.model_dump(),
        "behavior": behavior,
    }

    class Detector:
        def status(self):
            return {"sha256": sha}

        def detect(self, _payload, corridor, perception, *, evidence, geometry):
            result = assess_depth(
                scene((130, 196, 140, 202, 0.5)),
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
    try:
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
                corridor=MEASURED,
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
        completed = subprocess.run(
            [
                pi_python,
                str(Path(pi_root) / "tests/fixtures/replay_collision_lease.py"),
            ],
            input=json.dumps({"config": policy, "replies": replies}),
            text=True,
            capture_output=True,
            check=True,
            timeout=15,
        )
        received = json.loads(completed.stdout)
        assert not received["latched"]
        assert received["outputs"][-1] == [
            "steer",
            "left" if mirrored else "right",
            0.1,
        ]
    finally:
        await loop.close()
