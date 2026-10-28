"""Stored model policies, inheritance and hash scope; no robot or model load."""

import math

import pytest

from venom_module_rider.depth_detector import PerceptionSettings
from venom_module_rider.navigation_profiles import (
    DEFAULT_BEHAVIOR,
    parameters,
    policy_digest,
)
from venom_module_rider.perception_tuning import (
    SEMANTIC_MODELS,
    active_model_id,
    has_semantic_tuning,
    presentation,
    semantic_parameters,
    validate_resolved_semantic,
    validate_semantic_models,
)

SEG, YOLO = SEMANTIC_MODELS


def setting(**kwargs):
    return {"version": 1, "surface": "floor_rug", **kwargs}


def perception(**kwargs):
    return {
        "engine": "segformer",
        "model_id": SEG,
        "semantic_models": {SEG: setting()},
        **kwargs,
    }


def test_defaults_and_surface_are_independent_from_profile():
    old = {"engine": "segformer"}
    assert active_model_id(old) == SEG
    assert not has_semantic_tuning(old)
    raw = semantic_parameters(old)
    assert raw["traversable_labels"] == [3]
    assert raw["pixel_confidence"] == 0.8
    assert raw["min_floor_fraction"] == 1
    assert raw["min_region_fraction"] == 0
    fluid = {**DEFAULT_BEHAVIOR, "profile_id": "fluid"}
    tuned = semantic_parameters(perception(), fluid)
    assert tuned["traversable_labels"] == [3, 28]
    assert tuned["pixel_confidence"] == 0.7
    assert tuned["min_floor_fraction"] == 0.92


def test_per_model_overrides_and_inheritance():
    policy = perception(
        semantic_models={
            SEG: setting(pixel_confidence=0.63, min_region_fraction=0.004),
            YOLO: setting(surface="floor_only", pixel_confidence=0.87),
        }
    )
    behavior = {**DEFAULT_BEHAVIOR, "profile_id": "balanced"}
    seg = semantic_parameters(policy, behavior)
    yolo = semantic_parameters(policy, behavior, YOLO)
    assert seg["pixel_confidence"] == 0.63
    assert seg["min_floor_fraction"] == 0.95
    assert yolo["pixel_confidence"] == 0.87
    assert yolo["traversable_labels"] == [3]
    assert policy["semantic_models"][SEG]["surface"] == "floor_rug"


def test_effective_hash_binds_active_rules_and_ignores_other_model_rules():
    behavior = {**DEFAULT_BEHAVIOR, "profile_id": "fluid"}
    original = policy_digest(behavior)
    policy = perception()
    current = policy_digest(behavior, policy)
    assert current != original
    params = parameters(behavior, policy)
    assert params["traversable_labels"] == [3, 28]
    policy["semantic_models"][YOLO] = setting(pixel_confidence=0.9)
    assert policy_digest(behavior, policy) == current
    policy["semantic_models"][SEG]["pixel_confidence"] = 0.9
    assert policy_digest(behavior, policy) != current


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {"unknown": {}},
        {SEG: {"version": True}},
        {SEG: {"version": 2}},
        {SEG: {"surface": "road"}},
        {SEG: {"traversable_labels": [3, 6]}},
        {SEG: {"pixel_confidence": True}},
        {SEG: {"pixel_confidence": math.nan}},
        {SEG: {"pixel_confidence": 0.01}},
        {SEG: {"min_floor_fraction": 0.49}},
        {SEG: {"max_uncertain_fraction": 0.51}},
        {SEG: {"min_region_fraction": 0.03}},
    ],
)
def test_storage_contract_rejects_unknown_or_invalid_policy(bad):
    with pytest.raises(ValueError, match="tuning_invalid"):
        validate_semantic_models(bad)


@pytest.mark.parametrize("labels", [[3, 6], [28], [3, True], [3, 28, 11], "floor"])
def test_resolved_semantic_rejects_other_traversable_classes(labels):
    params = semantic_parameters(perception())
    params["traversable_labels"] = labels
    with pytest.raises(ValueError, match="tuning_invalid"):
        validate_resolved_semantic(params)


def test_schema_and_presentation_share_storage():
    model = PerceptionSettings(**perception())
    assert model.semantic_models[SEG]["surface"] == "floor_rug"
    visible = presentation(
        {"perception": model.model_dump(), "behavior": DEFAULT_BEHAVIOR}
    )
    assert visible["active_model_id"] == SEG
    assert visible["configured"]["surface"] == "floor_rug"
    assert visible["effective"]["traversable_labels"] == [3, 28]
    depth = presentation(
        {"perception": {"engine": "depth_plane", "residual_limit": 0.05}}
    )
    assert depth["active_model_id"] == "depth-anything-v2-indoor-small"
    assert depth["effective"]["residual_limit"] == 0.05
    with pytest.raises(ValueError, match="unsupported"):
        semantic_parameters({"engine": "depth_plane"})


def test_legacy_adapter_receives_selected_surface_without_explicit_model():
    from types import SimpleNamespace

    from venom_module_rider.vision_loop import VisionLoop

    seen = []

    class Detector:
        def detect(self, payload, corridor, **options):
            seen.append(options)
            return {"state": "clear", "reason": "floor_candidate"}

    frame = SimpleNamespace(
        corridor=(0.0, 0.0, 1.0, 1.0),
        perception=PerceptionSettings(semantic_models={SEG: setting()}),
        behavior=SimpleNamespace(
            profile_id="legacy", model_dump=lambda: DEFAULT_BEHAVIOR
        ),
    )
    VisionLoop._detect_floor(SimpleNamespace(detector=Detector()), b"fixture", frame)
    assert seen[0]["semantic"]["traversable_labels"] == [3, 28]


def test_legacy_digest_distinguishes_selected_surface_without_changing_old_default():
    plain = {"engine": "segformer"}
    assert policy_digest(DEFAULT_BEHAVIOR, plain) == policy_digest(DEFAULT_BEHAVIOR)
    rug = perception()
    floor = perception(semantic_models={SEG: setting(surface="floor_only")})
    assert policy_digest(DEFAULT_BEHAVIOR, rug) != policy_digest(
        DEFAULT_BEHAVIOR, floor
    )
