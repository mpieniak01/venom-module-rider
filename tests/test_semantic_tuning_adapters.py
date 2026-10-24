"""Per-model semantic interpretation is shared by native, preview and replay."""

import io
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from venom_module_rider.analysis_semantic import SemanticCandidate
from venom_module_rider.floor_detector import (
    FloorDetector,
    classify_logits,
    render_preview,
)
from venom_module_rider.navigation_maps import assess_evidence, semantic_maps
from venom_module_rider.navigation_profiles import DEFAULT_BEHAVIOR
from venom_module_rider.perception_catalog import DEPTH, SEGFORMER, YOLO
from venom_module_rider.perception_runtime import ModelRuntime
from venom_module_rider.perception_tuning import semantic_parameters
from venom_module_rider.spatial_evidence import semantic_masks

CORRIDOR = (0, 0, 1, 1)


def options(surface="floor_only", profile="balanced", model=SEGFORMER, **overrides):
    return semantic_parameters(
        {
            "model_id": model,
            "semantic_models": {model: {"version": 1, "surface": surface, **overrides}},
        },
        {**DEFAULT_BEHAVIOR, "profile_id": profile},
        model,
    )


def logits(label=3, shape=(100, 100)):
    result = np.zeros((1, 150, *shape), np.float32)
    result[:, label] = 20
    return result


def replace_label(values, label, region):
    y, x = region
    values[0, :, y, x] = 0
    values[0, label, y, x] = 20


def png():
    image = Image.fromarray(
        np.random.default_rng(5).integers(40, 210, (60, 100, 3), dtype=np.uint8)
    )
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize("model", [SEGFORMER, YOLO])
def test_rug_requires_explicit_model_specific_surface_selection(model):
    values = logits(28)
    assert classify_logits(values, CORRIDOR)["state"] == "obstacle"
    assert (
        classify_logits(values, CORRIDOR, semantic=options(model=model))["state"]
        == "obstacle"
    )
    chosen = options("floor_rug", model=model)
    assessment = classify_logits(values, CORRIDOR, semantic=chosen)
    assert assessment["state"] == "clear"
    assert assessment["floor_fraction"] == 1
    assert assessment["floor_candidate_fraction"] == 1
    assert assessment["unsupported_ground_fraction"] == 0
    maps = semantic_maps(values, (100, 100), semantic=chosen)
    assert maps["floor"].all() and not maps["obstacle"].any()
    assert assess_evidence(maps, CORRIDOR, chosen)["candidate"] == "clear"


@pytest.mark.parametrize("label", [6, 11])
def test_road_and_sidewalk_never_implicitly_become_traversable(label):
    chosen = options("floor_rug")
    result = classify_logits(logits(label), CORRIDOR, semantic=chosen)
    assert result["state"] == "obstacle"
    assert result["floor_candidate_fraction"] == 0
    assert result["unsupported_ground_fraction"] == 1


def test_spatial_tolerance_filters_single_pixel_but_preserves_coherent_and_thin():
    values = logits()
    replace_label(values, 0, (10, 10))
    assert classify_logits(values, CORRIDOR)["state"] == "obstacle"
    result = classify_logits(values, CORRIDOR, semantic=options())
    assert result["state"] == "clear"
    assert result["semantic_spatial"]["counts"]["ignored_obstacle_pixels"] == 1
    replace_label(values, 0, (slice(10, 20), slice(10, 20)))
    result = classify_logits(values, CORRIDOR, semantic=options())
    assert result["state"] == "obstacle"
    assert result["semantic_spatial"]["hard_stop"] is False

    values = logits()
    replace_label(values, 0, (10, slice(20, 40)))
    chosen = options(min_region_fraction=0.02)
    result = classify_logits(values, CORRIDOR, semantic=chosen)
    assert result["state"] == "obstacle"
    assert result["semantic_spatial"]["regions"][0]["thin"] is True


def test_near_obstacle_is_hard_stop_with_every_surface_and_profile():
    for surface in ("floor_only", "floor_rug"):
        for profile in ("cautious", "balanced", "fluid"):
            values = logits(28 if surface == "floor_rug" else 3)
            replace_label(values, 0, (slice(70, 99), 50))
            result = classify_logits(
                values,
                CORRIDOR,
                semantic=options(surface, profile, min_region_fraction=0.02),
            )
            assert result["state"] == "obstacle"
            assert result["semantic_spatial"]["hard_stop"] is True
            assert result["semantic_spatial"]["guard_clear"] is False


@pytest.mark.parametrize(
    "patch",
    [
        {"traversable_labels": [3, 6]},
        {"traversable_labels": [True, 28]},
        {"pixel_confidence": float("nan")},
        {"min_region_fraction": -0.001},
        {"min_near_floor_fraction": None},
    ],
)
def test_invalid_resolved_policy_cannot_produce_clearance(patch):
    chosen = {**options("floor_rug"), **patch}
    with pytest.raises(ValueError, match="navigation_model_tuning_invalid"):
        classify_logits(logits(28), CORRIDOR, semantic=chosen)
    with pytest.raises(ValueError, match="navigation_model_tuning_invalid"):
        semantic_maps(logits(28), (100, 100), semantic=chosen)


@pytest.mark.parametrize("bad", [[3, 6], [3, 11], [3, 28, 6], [True], None])
def test_compact_masks_reject_unsupported_class_selection(bad):
    with pytest.raises(ValueError, match="navigation_semantic_params"):
        semantic_masks(
            np.full((4, 4), 3, dtype=np.uint8),
            np.ones((4, 4), dtype=np.float32),
            0.8,
            bad,
        )


def test_invalid_source_map_is_not_revalidated_by_surface_mapping():
    chosen = options("floor_rug")
    values = logits(28)
    maps = semantic_maps(values, (100, 100), semantic=chosen)
    maps["valid"] = False
    result = classify_logits(values, CORRIDOR, semantic=chosen, source_maps=maps)
    assert result["state"] == "unknown"
    assert assess_evidence(maps, CORRIDOR, chosen)["valid"] is False


def test_preview_rug_changes_to_floor_green_only_when_explicitly_selected():
    values = logits(28, (32, 32))
    rgb = np.full((64, 96, 3), 0.4, np.float32)
    default = np.asarray(Image.open(io.BytesIO(render_preview(rgb, values, CORRIDOR))))[
        32, 48
    ]
    selected = np.asarray(
        Image.open(
            io.BytesIO(
                render_preview(rgb, values, CORRIDOR, semantic=options("floor_rug"))
            )
        )
    )[32, 48]
    assert default[2] > default[1] > default[0]
    assert selected[1] > selected[0] and selected[1] > selected[2]


class Session:
    def get_inputs(self):
        return [SimpleNamespace(name="pixels")]

    def run(self, _outputs, _inputs):
        return [logits(28, (32, 32))]


def test_native_onnx_policy_and_evidence_share_source_geometry():
    detector = FloorDetector()
    detector.session = Session()
    result = detector.detect(
        png(), CORRIDOR, semantic=options("floor_rug"), evidence=True
    )
    assert result["state"] == "clear"
    assert result["_navigation_maps"]["floor"].shape == (60, 100)
    assert result["_navigation_maps"]["floor"].all()
    # Without the explicit policy the pinned model keeps legacy rug handling.
    assert detector.detect(png(), CORRIDOR)["state"] == "obstacle"


def test_dark_capture_does_not_become_clear_with_floor_rug():
    detector = FloorDetector()
    detector.session = Session()
    output = io.BytesIO()
    Image.new("RGB", (96, 64)).save(output, format="PNG")
    result = detector.detect(
        output.getvalue(),
        CORRIDOR,
        semantic=options("floor_rug"),
        evidence=True,
    )
    assert result["state"] == "unknown"
    assert result["reason"] == "vision_image_quality"
    assert "_navigation_maps" not in result


def test_offline_adapter_forwards_same_policy_without_mutating_it(monkeypatch):
    adapter = SemanticCandidate.__new__(SemanticCandidate)
    adapter.identity = SEGFORMER, "a" * 64
    calls = []

    def detect(*args, **kwargs):
        calls.append(kwargs)
        return {"state": "clear", "_preview": b"preview"}

    adapter.detector = SimpleNamespace(detect=detect)
    monkeypatch.setattr(
        "venom_module_rider.perception_runtime.source_overlay",
        lambda *args: b"source-overlay",
    )
    chosen = options("floor_rug")
    frozen = deepcopy(chosen)
    result, _ = adapter.detect(png(), CORRIDOR, semantic=chosen, evidence=True)
    assert result["drive_supported"] is False
    assert calls == [{"evidence": True, "semantic": chosen}]
    assert chosen == frozen


def test_runtime_re_resolves_each_model_and_does_not_cache_policy(monkeypatch):
    from venom_module_rider.vision_contract import VisionFrame

    class Catalog:
        def ready(self, _model):
            return {"sha256": "a" * 64}

    calls, builds = [], []

    def build(_catalog, model, _artifact):
        builds.append(model)

        def detect(_payload, _corridor, *_settings, **kwargs):
            calls.append(deepcopy(kwargs))
            return {"state": "clear", "reason": "floor_candidate"}

        return SimpleNamespace(detect=detect)

    monkeypatch.setattr("venom_module_rider.perception_runtime.build_detector", build)
    settings = {
        SEGFORMER: {"version": 1, "surface": "floor_rug"},
        YOLO: {"version": 1, "surface": "floor_only"},
    }
    runtime = ModelRuntime(Catalog())

    def request(model, revision=1):
        return VisionFrame(
            protocol_version=1,
            run_id="unit",
            frame_id="frame",
            sequence=revision,
            config_revision=revision,
            age_ms=0.0,
            deadline_ms=500,
            perception={
                "engine": "depth_plane" if model == DEPTH else "segformer",
                "model_id": model,
                "artifact_sha256": "a" * 64,
                "semantic_models": deepcopy(settings),
            },
        )

    runtime.detect(b"fixture", request(SEGFORMER))
    settings[SEGFORMER]["surface"] = "floor_only"
    runtime.detect(b"fixture", request(SEGFORMER, 2))
    runtime.detect(b"fixture", request(YOLO, 3))
    assert builds == [SEGFORMER, YOLO]
    assert [row["semantic"]["traversable_labels"] for row in calls] == [
        [3, 28],
        [3],
        [3],
    ]
    runtime.detect(b"fixture", request(DEPTH, 4))
    assert calls[-1] == {}


@pytest.mark.parametrize("adapter_name", ["TorchSegformer", "YoloSemanticOnnx"])
def test_native_semantic_adapters_receive_policy_and_keep_source_overlay(
    monkeypatch, adapter_name
):
    import sys

    import torch

    from venom_module_rider import perception_runtime

    monkeypatch.setattr("venom_module_rider.cuda_execution.require_cuda", lambda: "cpu")
    # Both model adapters share semantic_output; the fixture still exercises
    # their native RGB preprocessor and actual classification/preview path.
    if adapter_name == "TorchSegformer":
        monkeypatch.setitem(
            sys.modules,
            "transformers",
            SimpleNamespace(SegformerForSemanticSegmentation=SimpleNamespace()),
        )

        class Model:
            config = SimpleNamespace(id2label={3: "floor", 28: "rug"})

            def __call__(self, *, pixel_values):
                assert tuple(pixel_values.shape) == (1, 3, 512, 512)
                return SimpleNamespace(logits=torch.from_numpy(logits(28, (32, 32))))

        detector = perception_runtime.TorchSegformer(None)
        detector.model = Model()
    else:
        detector = perception_runtime.YoloSemanticOnnx(None)
        detector.session = Session()
    output = io.BytesIO()
    image = np.random.default_rng(6).integers(30, 220, (96, 96, 3), dtype=np.uint8)
    Image.fromarray(image).save(output, format="PNG")
    payload = output.getvalue()
    result = detector.detect(
        payload, CORRIDOR, evidence=True, semantic=options("floor_rug")
    )
    assert result["state"] == "clear"
    assert result["_navigation_maps"]["floor"].shape == (96, 96)
    assert result["_navigation_maps"]["floor"].all()
    preview = perception_runtime.source_overlay(payload, result["_preview"])
    assert Image.open(io.BytesIO(preview)).size == (96, 96)


def test_torch_rug_policy_rejects_checkpoint_with_incompatible_class_mapping(
    monkeypatch,
):
    import sys

    import torch

    monkeypatch.setattr("venom_module_rider.cuda_execution.require_cuda", lambda: "cpu")
    from venom_module_rider.perception_runtime import TorchSegformer

    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(SegformerForSemanticSegmentation=SimpleNamespace()),
    )

    class Model:
        config = SimpleNamespace(id2label={3: "floor", 28: "different"})

        def __call__(self, **kwargs):
            return SimpleNamespace(logits=torch.from_numpy(logits(28, (32, 32))))

    detector = TorchSegformer(None)
    detector.model = Model()
    with pytest.raises(ValueError, match="model_labels_invalid"):
        detector.detect(png(), CORRIDOR, semantic=options("floor_rug"))


@pytest.mark.parametrize("batch_size", [1, 8, 16])
def test_bounded_yolo_reduction_matches_full_native_interpolation(
    monkeypatch, batch_size
):
    import torch

    from venom_module_rider.floor_detector import classify_compact_maps
    from venom_module_rider.navigation_maps import letterbox_semantic_maps

    rng = np.random.default_rng(12)
    values = rng.normal(0, 1, (1, 150, 8, 12)).astype(np.float32)
    values[:, 3] += 6
    values[:, 149, 3:5, 5:7] += 12
    # Exact ties across batches must retain the first class, like np.argmax.
    values[:, 28, :2, :2] = values[:, 3, :2, :2]
    full = torch.nn.functional.interpolate(
        torch.from_numpy(values),
        size=(64, 96),
        mode="bilinear",
        align_corners=False,
    ).numpy()[:, :, 7:57, 9:89]
    shifted = full[0] - full[0].max(axis=0)
    probabilities = np.exp(shifted)
    pmax = probabilities.max(axis=0) / probabilities.sum(axis=0)
    native_interpolate = torch.nn.functional.interpolate
    batches = []

    def bounded(tensor, **kwargs):
        batches.append(tensor.shape[1])
        return native_interpolate(tensor, **kwargs)

    monkeypatch.setattr(torch.nn.functional, "interpolate", bounded)
    maps = letterbox_semantic_maps(
        values, (9, 7, 80, 50), output_size=(64, 96), batch_size=batch_size
    )
    assert max(batches) <= batch_size
    assert sum(batches) == 150
    np.testing.assert_array_equal(maps["labels"], full[0].argmax(axis=0))
    np.testing.assert_allclose(maps["confidence"], pmax, atol=1e-6, rtol=3e-6)
    expected = classify_logits(full, (0.25, 0.3, 0.75, 0.9))
    compact = classify_compact_maps(maps, (0.25, 0.3, 0.75, 0.9))
    for key in (
        "state",
        "reason",
        "corridor_pixels",
        "uncertain_pixels",
        "non_floor_pixels",
        "dominant_class_id",
    ):
        assert compact[key] == expected[key]
    for key, value in expected.items():
        if isinstance(value, float):
            assert compact[key] == pytest.approx(value, abs=1e-6)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"batch_size": True},
        {"batch_size": 0},
        {"batch_size": 17},
        {"crop": (0, 0, 97, 64)},
        {"crop": (0, -1, 96, 64)},
        {"output_size": (float("nan"), 96)},
    ],
)
def test_bounded_yolo_rejects_invalid_geometry_or_memory_budget(kwargs):
    from venom_module_rider.navigation_maps import letterbox_semantic_maps

    arguments = {"crop": (0, 0, 96, 64), "output_size": (64, 96), **kwargs}
    with pytest.raises(ValueError, match="vision_model_output"):
        letterbox_semantic_maps(logits(shape=(8, 8)), **arguments)
