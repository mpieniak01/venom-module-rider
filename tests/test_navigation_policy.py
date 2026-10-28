"""New behavior integrated with real live admission, not only helper functions."""

import json
from copy import deepcopy

import numpy as np
import pytest
from pydantic import ValidationError
from test_adaptive_ground import CAMERA, SETTINGS, scene

from venom_module_rider.adaptive_ground import assess
from venom_module_rider.navigation_maps import depth_maps, semantic_maps
from venom_module_rider.navigation_profiles import (
    DEFAULT_BEHAVIOR,
    catalog,
    parameters,
    policy_digest,
)
from venom_module_rider.vision_contract import BehaviorSettings, VisionFrame
from venom_module_rider.vision_loop import VisionLoop


def selected(name="balanced"):
    return {**DEFAULT_BEHAVIOR, "profile_id": name}


def frame(seq, **changes):
    return VisionFrame(
        protocol_version=1,
        run_id="run",
        frame_id=f"f{seq}",
        sequence=seq,
        config_revision=1,
        age_ms=0.0,
        deadline_ms=300,
        mode="drive",
        verified=True,
        calibration_id="fixture",
        model_sha256="a" * 64,
        corridor=(0.0, 0.0, 1.0, 1.0),
        behavior=selected(),
        **changes,
    )


class MapDetector:
    def __init__(self):
        self.case = "clear"

    def status(self):
        return {"sha256": "a" * 64}

    def detect(self, *_args, **options):
        assert options.get("evidence")
        floor = np.ones((100, 100), dtype=bool)
        obstacle = np.zeros_like(floor)
        uncertain = np.zeros_like(floor)
        if self.case == "pixel":
            uncertain[90, 50] = True
        elif self.case == "far_uncertain":
            uncertain[:10] = True
        elif self.case == "wire":
            obstacle[85, 30:70] = True
        floor[obstacle | uncertain] = False
        return {
            "state": "unknown"
            if uncertain.any()
            else "obstacle"
            if obstacle.any()
            else "clear",
            "reason": "raw_model",
            "floor_fraction": float(floor.mean()),
            "_navigation_maps": {
                "floor": floor,
                "obstacle": obstacle,
                "uncertain": uncertain,
                "valid": True,
            },
        }


async def test_one_pixel_flicker_becomes_supported_clear_after_three_fresh_frames():
    detector = MapDetector()
    detector.case = "pixel"
    now = [0.0]
    loop = VisionLoop(detector, clock=lambda: now[0])
    results = []
    try:
        for seq in range(1, 4):
            now[0] = seq * 0.06
            results.append(await loop.decide("pi", frame(seq), b"fixture"))
        assert [r["action"] for r in results] == ["stop", "stop", "go"]
        assert results[-1]["raw_state"] == "unknown"
        assert results[-1]["state"] == "clear"
        assert results[-1]["effective_policy_sha256"] == policy_digest(selected())
        assert len(json.dumps(results[-1])) < 8000
        assert "_navigation_maps" not in results[-1]
    finally:
        await loop.close()


async def test_soft_uncertainty_slows_but_thin_near_obstacle_stops_immediately():
    detector = MapDetector()
    now = [0.0]
    loop = VisionLoop(detector, clock=lambda: now[0])
    try:
        for seq in range(1, 4):
            now[0] = seq * 0.06
            result = await loop.decide("pi", frame(seq), b"fixture")
        assert result["action"] == "go"
        detector.case = "far_uncertain"
        now[0] += 0.06
        result = await loop.decide("pi", frame(4), b"fixture")
        assert result["action"] == "slow"
        assert result["clear_count"] == 3
        assert result["temporal"]["near_guard_clear"] is True
        detector.case = "wire"
        now[0] += 0.06
        result = await loop.decide("pi", frame(5), b"fixture")
        assert result["action"] == "stop"
        assert result["temporal"]["risk"] == "blocked"
        assert result["clear_count"] == 0
    finally:
        await loop.close()


async def test_gap_cannot_reuse_old_positive_history():
    now = [0.0]
    loop = VisionLoop(MapDetector(), clock=lambda: now[0])
    try:
        for seq in range(1, 4):
            now[0] = seq * 0.06
            await loop.decide("pi", frame(seq), b"fixture")
        now[0] += 0.4
        result = await loop.decide("pi", frame(4), b"fixture")
        assert result["action"] == "stop"
        assert result["clear_count"] == 0
    finally:
        await loop.close()


async def test_observation_never_grants_motion_with_supported_regions():
    now = [0.0]
    loop = VisionLoop(MapDetector(), clock=lambda: now[0])
    try:
        for seq in range(1, 4):
            now[0] = seq * 0.06
            request = frame(seq).model_copy(update={"mode": "observe"})
            result = await loop.decide("pi", request, b"fixture")
            assert result["action"] == "stop"
        assert result["vision_action"] == "go"
    finally:
        await loop.close()


def test_neural_negative_outlier_is_not_independently_confirmed_critical():
    errors = np.zeros((64, 96))
    signed = errors.copy()
    errors[60, 48] = 0.07
    signed[60, 48] = -0.07
    maps = depth_maps(errors, signed, 0.06, valid=True)
    assert maps["obstacle"][60, 48]
    assert maps["negative_support"][60, 48]
    assert not maps["critical"].any()


def test_semantic_compact_maps_keep_source_shape_and_probabilities():
    logits = np.zeros((1, 150, 8, 8), np.float32)
    logits[:, 3] = 20
    maps = semantic_maps(logits, (96, 64))
    assert maps["floor"].shape == (64, 96)
    assert maps["floor"].all()
    assert maps["labels"].dtype == np.uint8
    assert maps["confidence"].dtype == np.float32
    assert maps["valid"]


def test_ground_profile_maps_use_projected_corridor_and_keep_near_box():
    free = assess(
        scene(),
        (0.35, 0.8, 0.65, 0.95),
        SETTINGS,
        CAMERA,
        evidence=parameters(selected()),
    )
    assert free["state"] == "clear"
    assert free["_navigation_maps"]["corridor"].dtype == np.bool_
    box = scene(box=((-0.2, 0.2), (0.55, 0.85), (0.0, 0.6)))
    blocked = assess(
        box, (0.35, 0.8, 0.65, 0.95), SETTINGS, CAMERA, evidence=parameters(selected())
    )
    assert blocked["state"] != "clear"


@pytest.mark.parametrize(
    "value",
    [
        {"version": True},
        {"profile_revision": True},
        {"profile_id": "bad"},
        {"profile_id": "fluid", "extra": 1},
    ],
)
def test_behavior_schema_rejects_ambiguous_or_extended_values(value):
    with pytest.raises(ValidationError):
        BehaviorSettings.model_validate(value)


def test_profile_catalog_isolated_and_deterministic():
    first = catalog(selected())
    first["profiles"][2]["parameters"]["release_ms"] = 999
    assert parameters(selected())["release_ms"] == 100
    assert catalog(selected())["effective_policy_sha256"] == policy_digest(selected())
    assert deepcopy(DEFAULT_BEHAVIOR)["profile_id"] == "legacy"


async def test_quality_rejection_cannot_be_promoted_by_confident_semantic_logits():
    class DarkDetector(MapDetector):
        def detect(self, *_args, **options):
            result = super().detect(*_args, **options)
            result.update(state="unknown", reason="vision_image_quality")
            # Reproduce adapters producing a confident map for a bad capture.
            result["_navigation_maps"].update(
                labels=np.full((100, 100), 3, dtype=np.uint8),
                confidence=np.ones((100, 100), dtype=np.float32),
            )
            return result

    now = [0.0]
    loop = VisionLoop(DarkDetector(), clock=lambda: now[0])
    try:
        for seq in range(1, 5):
            now[0] = seq * 0.06
            result = await loop.decide("pi", frame(seq), b"fixture")
            assert result["action"] == result["vision_action"] == "stop"
            assert result["clear_count"] == 0
            assert result["raw_reason"] == "vision_image_quality"
    finally:
        await loop.close()


async def test_canonical_source_duplicate_never_counts_as_three_observations():
    now = [0.0]
    loop = VisionLoop(MapDetector(), clock=lambda: now[0])
    source = {
        "camera_session": "camera",
        "transport_epoch": "transport",
        "pipeline_revision": 1,
        "frame_id": "camera-302",
        "sequence": 302,
    }
    try:
        for seq in range(1, 4):
            now[0] = seq * 0.06
            result = await loop.decide(
                "pi", frame(seq), b"fixture", source_reference=source
            )
            assert result["action"] == "stop"
        assert result["clear_count"] == 0
        assert result["reason"] == "navigation_duplicate_frame"
    finally:
        await loop.close()


async def test_temporal_near_guard_vetoes_existing_steering_maneuver():
    from venom_module_rider.depth_detector import assess_depth

    y, x = np.mgrid[:240, :320]
    depth = 1 / np.maximum(0.1 * x / 320 + 3.5 * y / 240 - 2, 0.1).astype(float)
    depth[215:216, 130:150] += 0.5

    class Depth:
        def status(self):
            return {"sha256": "a" * 64}

        def detect(self, _payload, roi, settings, *, evidence):
            return assess_depth(depth, roi, settings, evidence=evidence)

    now = [0.0]
    loop = VisionLoop(clock=lambda: now[0])
    loop.depth_detector = Depth()
    try:
        for seq in range(1, 5):
            now[0] = seq * 0.06
            request = VisionFrame.model_validate(
                {
                    **frame(seq).model_dump(),
                    "protocol_version": 3,
                    "corridor": (0.35, 0.8, 0.65, 0.95),
                    "perception": {"engine": "depth_plane"},
                    "avoidance": {
                        "enabled": True,
                        "sweep_verified": True,
                        "geometry": {"mode": "image"},
                    },
                }
            )
            result = await loop.decide("pi", request, b"fixture")
            assert result["temporal"]["spatial"]["hard_stop"] is True
            assert result["action"] == "stop"
    finally:
        await loop.close()


async def test_semantic_rethresholding_never_validates_a_rejected_source():
    class InvalidSource(MapDetector):
        def detect(self, *_args, **options):
            result = super().detect(*_args, **options)
            result.update(state="clear", reason="source_unverified")
            result["_navigation_maps"].update(
                valid=False,
                labels=np.full((100, 100), 3, dtype=np.uint8),
                confidence=np.ones((100, 100), dtype=np.float32),
            )
            return result

    now = [0.0]
    loop = VisionLoop(InvalidSource(), clock=lambda: now[0])
    try:
        for seq in range(1, 5):
            now[0] = seq * 0.06
            result = await loop.decide("pi", frame(seq), b"fixture")
            assert result["action"] == result["vision_action"] == "stop"
            assert result["clear_count"] == 0
            assert result["temporal"]["spatial"]["valid"] is False
            assert result["raw_state"] == "clear"
    finally:
        await loop.close()


def test_replay_semantic_invalid_flag_survives_new_confidence_threshold(tmp_path):
    from scripts.replay_navigation_policy import replay

    labels = np.full((100, 100), 3, dtype=np.uint8)
    np.savez_compressed(
        tmp_path / "map.npz",
        labels=labels,
        confidence=np.ones((100, 100), np.float32),
        valid=np.array(False),
    )
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        "".join(
            json.dumps(
                {
                    "maps": "map.npz",
                    "sequence": i,
                    "frame_id": f"f{i}",
                    "captured_s": i * 0.06,
                    "context": {"source": "fixture"},
                    "raw_state": "clear",
                    "raw_reason": "source_unverified",
                }
            )
            + "\n"
            for i in range(1, 5)
        )
    )
    summary = replay(
        manifest, parameters(selected()), tmp_path / "out.jsonl", behavior=selected()
    )
    assert summary["actions"] == {"stop": 4}
