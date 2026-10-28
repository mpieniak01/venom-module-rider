"""Source coordinates and unobserved pixels never become artificial free space."""

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from venom_module_rider.analysis_contract import AnalysisSpec
from venom_module_rider.depth_detector import PerceptionSettings, assess_depth
from venom_module_rider.model_input import InputRegion, input_box, restore_depth
from venom_module_rider.vision_contract import VisionFrame


def plane():
    """Create source-coordinate depth for an exact synthetic floor."""
    y, _ = np.mgrid[:240, :320]
    return 1.0 / np.maximum(0.1, 4 * (y / 240 - 0.5))


def test_full_view_omission_preserves_old_serialized_policy():
    """Keep legacy policy bytes and full-frame depth identity unchanged."""
    settings = PerceptionSettings(engine="depth_plane")
    assert "input_region" not in settings.model_dump()
    assert "input_region" not in settings.model_dump_json()
    depth = plane()
    assert restore_depth(depth, (320, 240), (0, 0, 320, 240)) is depth


def test_recommended_crop_retains_exact_source_plane_and_scored_pixels():
    """Restore crop coordinates without moving the reference plane or scored pixels."""
    depth = plane()
    settings = PerceptionSettings(engine="depth_plane", input_region=InputRegion())
    box = input_box(Image.new("RGB", (320, 240)), settings.input_region)
    assert box == (0, 96, 320, 240)
    restored = restore_depth(depth[96:, :], (320, 240), box)
    assert np.isnan(restored[:96]).all()
    before = assess_depth(depth, (0.35, 0.8, 0.65, 0.95), settings, evidence=True)
    after = assess_depth(
        restored, (0.35, 0.8, 0.65, 0.95), settings, evidence=True, input_bounds=box
    )
    for field in [
        "state",
        "plane_slope",
        "plane_residual_p99",
        "outlier_pixels",
        "reference_outlier_fraction",
    ]:
        assert after[field] == before[field]
    np.testing.assert_array_equal(after["_errors"], before["_errors"])
    assert after["_navigation_maps"]["input_bounds"] == box


def test_narrow_input_cannot_authorize_unseen_side_paths():
    """Keep partially unobserved side paths unknown even when the centre is clear."""
    depth = plane()
    box = (64, 96, 256, 240)
    restored = restore_depth(depth[96:240, 64:256], (320, 240), box)
    result = assess_depth(
        restored,
        (0.35, 0.8, 0.65, 0.95),
        PerceptionSettings(engine="depth_plane"),
        input_bounds=box,
    )
    assert result["state"] == "clear"
    for side in ["left", "right"]:
        assert result["sectors"][side]["state"] == "unknown"
        assert result["sectors"][side]["coverage_complete"] is False
        assert result["paths"][side]["state"] == "unknown"


@pytest.mark.parametrize("bounds", [[0.0, 0.9, 1.0, 1.0], [0.4, 0.4, 0.6, 1.0]])
def test_input_cannot_cut_off_floor_reference_or_scoring_region(bounds):
    """Reject incomplete reference coverage in both saved and live frame contracts."""
    settings = PerceptionSettings(
        engine="depth_plane", input_region=InputRegion(bounds=bounds)
    )
    with pytest.raises(ValidationError, match="excludes_reference"):
        AnalysisSpec(perception=settings)
    with pytest.raises(ValidationError, match="excludes_reference"):
        VisionFrame(
            protocol_version=1,
            run_id="test",
            frame_id="test",
            sequence=1,
            config_revision=1,
            age_ms=0.0,
            deadline_ms=300,
            perception=settings,
        )


@pytest.mark.parametrize(
    "value",
    [
        {"version": True},
        {"version": 2},
        {"bounds": [0.0, 0.0, 0.0, 1.0]},
        {"bounds": [0.0, float("nan"), 1.0, 1.0]},
        {"bounds": [0.0, 0.0, 1.0]},
        {"bounds": [False, 0.0, 1.0, 1.0]},
    ],
)
def test_malformed_regions_are_rejected(value):
    """Reject unsupported versions and malformed or non-finite normalized bounds."""
    with pytest.raises(ValidationError):
        InputRegion.model_validate(value)


def test_small_actual_crop_and_unsupported_offline_model_are_rejected():
    """Prevent undersized input and accidental crop inheritance by other models."""
    region = InputRegion()
    with pytest.raises(ValueError, match="too_small"):
        region.pixels((32, 32))
    settings = PerceptionSettings(engine="depth_plane", input_region=region)
    with pytest.raises(ValidationError, match="input_region_unsupported"):
        AnalysisSpec(profile="da3-small-280", perception=settings)


def test_saved_photo_worker_runs_crop_and_restores_full_source(tmp_path, monkeypatch):
    """Exercise worker crop inference while preserving the original artifact size."""
    import base64
    import io

    from test_analysis_worker import execute, job

    from venom_module_rider import cuda_execution

    monkeypatch.setattr(cuda_execution, "require_cuda", lambda: None)
    source, _store = job(tmp_path, monkeypatch, count=1)
    source["context"]["spec"]["perception"] = {
        "engine": "depth_plane",
        "input_region": {"version": 1, "bounds": [0.0, 0.2, 1.0, 1.0]},
    }
    messages, _report = execute(source, monkeypatch)
    frame = next(m for m in messages if m["type"] == "frame")
    assert frame["row"]["overlay_geometry"]["input_bounds"] == [0, 9, 64, 48]
    artifact = next(a for a in frame["artifacts"] if a["name"].endswith("depth.npy"))
    depth = np.load(io.BytesIO(base64.b64decode(artifact["data"])), allow_pickle=False)
    assert depth.shape == (48, 64)
    assert np.isnan(depth[:9]).all()
    assert np.isfinite(depth[9:]).all()
