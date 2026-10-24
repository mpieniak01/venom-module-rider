"""Operator corrections keep preset identity, peer digest and source evidence."""

import json
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from venom_module_rider.depth_detector import PerceptionSettings
from venom_module_rider.navigation_maps import assess_evidence
from venom_module_rider.navigation_policy import evaluate
from venom_module_rider.navigation_profiles import (
    catalog,
    parameters,
    policy_digest,
    validate_behavior,
)
from venom_module_rider.object_diagnostics import ObjectDiagnostics
from venom_module_rider.obstacle_persistence import ObstaclePersistence
from venom_module_rider.temporal_evidence import TemporalEvidence
from venom_module_rider.vision_contract import BehaviorSettings


def behavior(**overrides):
    value = {"version": 1, "profile_id": "fluid", "profile_revision": 2}
    if overrides:
        value["overrides"] = overrides
    return value


def test_v1_is_pinned_and_v2_corrections_are_explicit():
    v1 = {**behavior(), "profile_revision": 1}
    assert "obstacle_support_frames" not in parameters(v1)
    assert parameters(behavior())["obstacle_support_frames"] == 2
    corrected = behavior(obstacle_support_frames=3)
    assert parameters(corrected)["obstacle_support_frames"] == 3
    assert policy_digest(corrected) != policy_digest(behavior())
    assert parameters(behavior())["obstacle_support_frames"] == 2
    assert BehaviorSettings.model_validate(v1).model_dump() == v1
    assert BehaviorSettings.model_validate(corrected).model_dump() == corrected
    assert catalog(v1)["active"]["profile_revision"] == 1
    assert catalog(v1)["profiles"][-1]["profile_revision"] == 2


@pytest.mark.parametrize(
    "corrections",
    [
        {"enter_frames": True},
        {"enter_ms": 2.5},
        {"object_tracking_enabled": 1},
        {"pixel_confidence": float("nan")},
        {"min_region_fraction": float("inf")},
        {"object_growth_min": 10**1000},
        {"unknown": 1},
        {"obstacle_support_frames": 5, "obstacle_window_frames": 3},
        {"history_ms": 100, "max_gap_ms": 300},
    ],
)
def test_invalid_corrections_cannot_form_policy(corrections):
    with pytest.raises((ValueError, ValidationError)):
        BehaviorSettings.model_validate(behavior(**corrections))


def test_legacy_cannot_acquire_hidden_advanced_parameters():
    for selection in [
        {**behavior(enter_frames=2), "profile_id": "legacy", "profile_revision": 1},
        {**behavior(enter_frames=2), "profile_revision": 1},
    ]:
        with pytest.raises(ValueError):
            validate_behavior(selection)


class FixedMotion:
    def reset(self):
        pass

    def update(self, gray, ratio):
        return {
            "status": "ok",
            "_transform": np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
        }


def maps(x=4, *, critical=False):
    obstacle = np.zeros((32, 32), bool)
    obstacle[27:29, x : x + 3] = True
    return {
        "floor": ~obstacle,
        "obstacle": obstacle,
        "uncertain": np.zeros_like(obstacle),
        "critical": np.full_like(obstacle, critical),
        "valid": True,
        "gray": np.random.default_rng(4).integers(0, 256, (32, 32), dtype=np.uint8),
    }


def apply(detector, data, n, **extra):
    return detector.apply(
        data,
        (0.0, 0.0, 1.0, 1.0),
        context={"model": "fixture", **extra},
        sequence=n,
        frame_id=f"f{n}",
        captured_s=n * 0.06,
        params=parameters(behavior()),
    )


def test_transient_is_uncertain_not_new_floor_and_persistent_near_region_stops():
    detector = ObstaclePersistence()
    detector.motion = FixedMotion()
    original = maps()
    snapshot = original["floor"].copy()
    first, info = apply(detector, original, 1)
    assert not first["obstacle"].any() and first["uncertain"].sum() == 6
    assert np.array_equal(first["floor"], snapshot)
    assert original["obstacle"].sum() == 6 and not original["uncertain"].any()
    assert info["suppression_is_floor_evidence"] is False
    second, _ = apply(detector, original, 2)
    assert second["obstacle"].sum() == 6
    assert assess_evidence(second, (0.0, 0.0, 1.0, 1.0), parameters(behavior()))[
        "hard_stop"
    ]


def test_jump_context_change_duplicate_and_source_gap_do_not_accumulate_support():
    detector = ObstaclePersistence()
    detector.motion = FixedMotion()
    apply(detector, maps(2), 1)
    second, _ = apply(detector, maps(20), 2)
    assert not second["obstacle"].any()
    _, info = apply(detector, maps(20), 2)
    assert info["state"] == "reset"
    third, _ = apply(detector, maps(20), 3)
    assert not third["obstacle"].any()
    _, info = apply(detector, maps(20), 4, revision=2)
    assert info["state"] == "reset"
    _, info = apply(detector, maps(20), 20, revision=2)
    assert info["state"] != "current" or info["confirmed_pixels"] == 0


def test_critical_and_geometry_evidence_are_independent_of_component_filter():
    detector = ObstaclePersistence()
    detector.motion = FixedMotion()
    first, _ = apply(detector, maps(critical=True), 1)
    spatial = assess_evidence(first, (0.0, 0.0, 1.0, 1.0), parameters(behavior()))
    assert spatial["hard_stop"] and spatial["candidate"] == "obstacle"
    first["critical"][:] = False
    first["geometry_collision"] = True
    assert (
        assess_evidence(first, (0.0, 0.0, 1.0, 1.0), parameters(behavior()))[
            "candidate"
        ]
        == "obstacle"
    )


def test_missing_tracking_or_invalid_reference_does_not_suppress_raw_obstacle():
    detector = ObstaclePersistence()
    data = maps()
    del data["gray"]
    result, info = apply(detector, data, 1)
    assert result is data and info["state"] == "tracking_unavailable"
    data = maps()
    data["valid"] = False
    result, info = apply(detector, data, 2)
    assert result is data and info["state"] == "reset"


def test_object_diagnostics_are_bounded_and_do_not_change_decision():
    settings = behavior(object_tracking_enabled=True, object_min_area_px=8)
    options = parameters(settings)
    tracker = ObjectDiagnostics()
    tracker.motion = FixedMotion()
    data = maps()
    untouched = data["obstacle"].copy()
    first = tracker.observe(
        data,
        (0.0, 0.0, 1.0, 1.0),
        context={"model": "fixture"},
        sequence=1,
        captured_s=0.06,
        params=options,
    )
    assert first["diagnostic_only"] and np.array_equal(data["obstacle"], untouched)
    json.dumps(first, allow_nan=False)
    ordinary = TemporalEvidence()
    diagnostic = TemporalEvidence()
    frame = SimpleNamespace(
        behavior=BehaviorSettings.model_validate(behavior(obstacle_support_frames=1)),
        perception=PerceptionSettings(engine="depth_plane"),
        corridor=(0.0, 0.0, 1.0, 1.0),
        sequence=1,
        frame_id="f1",
    )
    result = {
        "state": "obstacle",
        "reason": "depth_ground_deviation",
        "_navigation_maps": deepcopy(data),
    }
    a = evaluate(result, frame, ordinary, context={"fixture": True}, captured_s=0.06)
    frame.behavior = BehaviorSettings.model_validate(
        behavior(
            obstacle_support_frames=1,
            object_tracking_enabled=True,
            object_min_area_px=8,
        )
    )
    result = {
        "state": "obstacle",
        "reason": "depth_ground_deviation",
        "_navigation_maps": deepcopy(data),
    }
    b = evaluate(result, frame, diagnostic, context={"fixture": True}, captured_s=0.06)
    assert (a["action"], a["reason"]) == (b["action"], b["reason"])
    assert b["objects"]["diagnostic_only"] is True


def test_component_is_tracked_before_intersection_with_small_route():
    detector = ObstaclePersistence()
    detector.motion = FixedMotion()
    data = maps()
    data["obstacle"][:] = False
    data["obstacle"][8:29, 13:18] = True
    data["floor"] = ~data["obstacle"]
    options = dict(
        context={"model": "full-map"},
        captured_s=0.06,
        sequence=1,
        frame_id="f1",
        params=parameters(behavior()),
    )
    detector.apply(data, (0.4, 0.8, 0.6, 0.95), **options)
    options.update(captured_s=0.12, sequence=2, frame_id="f2")
    confirmed, _ = detector.apply(data, (0.4, 0.8, 0.6, 0.95), **options)
    assert confirmed["obstacle"].sum() == data["obstacle"].sum()


def test_history_time_limit_prevents_old_support_from_confirming_new_component():
    detector = ObstaclePersistence()
    detector.motion = FixedMotion()
    params = parameters(
        behavior(
            obstacle_support_frames=3,
            history_ms=100,
            max_gap_ms=100,
            max_uncertain_ms=100,
        )
    )
    for n in range(1, 4):
        result, _ = detector.apply(
            maps(),
            (0.0, 0.0, 1.0, 1.0),
            context={"fixture": True},
            sequence=n,
            frame_id=f"f{n}",
            captured_s=n * 0.06,
            params=params,
        )
    assert not result["obstacle"].any()


def test_diagnostic_depth_context_does_not_expand_stop_or_change_plane_fit():
    from venom_module_rider.depth_detector import assess_depth

    y, x = np.mgrid[:240, :320]
    depth = 1 / np.maximum(0.05, y / 240 - 0.5)
    settings = PerceptionSettings(engine="depth_plane")
    plain = assess_depth(
        depth, (0.35, 0.8, 0.65, 0.95), settings, evidence=parameters(behavior())
    )
    diagnostic = assess_depth(
        depth,
        (0.35, 0.8, 0.65, 0.95),
        settings,
        evidence=parameters(behavior(object_tracking_enabled=True)),
    )
    for key in [
        "state",
        "reason",
        "plane_reference_corridor",
        "ground_reference_valid",
        "outlier_pixels",
    ]:
        assert plain[key] == diagnostic[key]
    assert (
        diagnostic["_object_maps"]["uncertain"].sum()
        < diagnostic["_navigation_maps"]["uncertain"].sum()
    )


def test_object_identity_handles_skipped_sequences_and_mask_jump():
    tracker = ObjectDiagnostics()
    tracker.motion = FixedMotion()
    params = parameters(behavior(object_tracking_enabled=True, object_min_area_px=8))
    ids = []
    for index, (sequence, end) in enumerate([(1, 20), (20, 22), (40, 24)], 1):
        data = maps()
        data["obstacle"][:] = False
        data["obstacle"][10:end, 10:end] = True
        data["floor"] = ~data["obstacle"]
        result = tracker.observe(
            data,
            (0.35, 0.8, 0.65, 0.95),
            context={"model": "fixture"},
            sequence=sequence,
            captured_s=index * 0.06,
            params=params,
        )
        assert len(result["objects"]) == 1
        ids.append(result["objects"][0]["id"])
    assert len(set(ids)) == 1 and result["objects"][0]["approaching_candidate"]
    assert result["objects"][0]["route_pixels"] == 0
    data["obstacle"][:] = True
    data["floor"][:] = False
    jump = tracker.observe(
        data,
        (0.35, 0.8, 0.65, 0.95),
        context={"model": "fixture"},
        sequence=41,
        captured_s=0.24,
        params=params,
    )
    assert jump["objects"][0]["id"] != ids[0]
    assert jump["objects"][0]["observations"] == 1


def test_diagnostic_render_is_same_size_bounded_and_keeps_input_without_objects():
    import io

    from PIL import Image

    from venom_module_rider.object_diagnostics import render_objects

    buffer = io.BytesIO()
    Image.new("RGB", (32, 32)).save(buffer, format="JPEG")
    payload = buffer.getvalue()
    assert render_objects(payload, {"state": "disabled"}) is payload
    objects = {
        "state": "current",
        "objects": [{"id": 1, "bbox": [8, 12, 22, 28], "growth": 1.2, "lower_px": 3}],
    }
    rendered = render_objects(payload, objects)
    assert Image.open(io.BytesIO(rendered)).size == (32, 32)
    assert rendered != payload
    assert render_objects(b"invalid", objects) == b"invalid"


def test_model_threshold_and_custom_profile_precedence_is_visible_in_catalog():
    perception = {
        "engine": "segformer",
        "model_id": "segformer-b0-ade20k",
        "semantic_models": {
            "segformer-b0-ade20k": {
                "version": 1,
                "surface": "floor_rug",
                "pixel_confidence": 0.8,
            }
        },
    }
    chosen = behavior(pixel_confidence=0.9)
    from venom_module_rider.perception_tuning import presentation, semantic_parameters

    assert parameters(chosen, perception)["pixel_confidence"] == 0.9
    assert semantic_parameters(perception, chosen)["pixel_confidence"] == 0.9
    assert (
        presentation({"behavior": chosen, "perception": perception})["effective"][
            "pixel_confidence"
        ]
        == 0.9
    )
    published = catalog(chosen, perception)
    assert published["active_base_parameters"]["pixel_confidence"] == 0.8
    assert published["active_parameters"]["pixel_confidence"] == 0.9
    published["parameter_schema"]["enter_frames"]["max"] = 900
    assert catalog()["parameter_schema"]["enter_frames"]["max"] == 8
