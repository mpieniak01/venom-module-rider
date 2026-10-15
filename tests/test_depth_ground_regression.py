"""Ground geometry keeps small obstacles and recesses visible without hardware."""

import os
from pathlib import Path

import numpy as np
import pytest

from venom_module_rider.depth_detector import PerceptionSettings, assess_depth

CORRIDOR = (0.35, 0.65, 0.65, 0.95)
SETTINGS = PerceptionSettings(engine="depth_plane")


def ground_depth():
    y, x = np.mgrid[:240, :320]
    inverse = 0.1 * x / 320 + 3.5 * y / 240 - 2.0
    return 1.0 / np.maximum(inverse, 0.1)


@pytest.mark.parametrize("scale", [0.2, 1.0, 5.0])
def test_planar_ground_is_clear_at_relative_depth_scales(scale):
    # The synthetic plane need only be valid in the camera's analysed corridor.
    depth = ground_depth()
    depth[:156] = 2.0
    result = assess_depth(depth * scale, CORRIDOR, SETTINGS)
    assert result["state"] == "clear"
    assert result["nearer_outlier_fraction"] == 0.0
    assert result["farther_outlier_fraction"] == 0.0


@pytest.mark.parametrize("delta", [0.2, -0.2])
@pytest.mark.parametrize("shape", ["thin_horizontal", "thin_vertical", "patch"])
def test_thin_obstacles_and_recesses_both_stop(delta, shape):
    depth = ground_depth()
    depth[:156] = 2.0
    inverse = 1.0 / depth
    region = {
        "thin_horizontal": (slice(180, 182), slice(135, 185)),
        "thin_vertical": (slice(165, 215), slice(159, 161)),
        "patch": (slice(181, 191), slice(155, 165)),
    }[shape]
    inverse[region] += delta
    result = assess_depth(1.0 / inverse, CORRIDOR, SETTINGS)
    assert result["state"] == "obstacle"
    assert result["reason"] == "depth_ground_deviation"
    direction = "nearer" if delta > 0 else "farther"
    other = "farther" if delta > 0 else "nearer"
    assert result[f"{direction}_outlier_fraction"] > SETTINGS.outlier_limit
    assert result[f"{other}_outlier_fraction"] == 0.0
    assert result["outlier_fraction"] == pytest.approx(
        result["nearer_outlier_fraction"] + result["farther_outlier_fraction"]
    )


def test_recorded_motion_floor_deviation_is_reproduced_without_relaxing_limits():
    fixture = os.getenv("RIDER_TEST_RECORDED_DEPTH_TRIAL")
    if not fixture:
        pytest.skip("private recorded depth fixture not configured")
    outcomes = []
    for index in range(4):
        with np.load(Path(fixture) / f"frame-{index}-depth.npz") as archive:
            outcomes.append(assess_depth(archive["depth"], CORRIDOR, SETTINGS))
    assert [row["state"] for row in outcomes] == ["clear", "clear", "clear", "obstacle"]
    terminal = outcomes[-1]
    assert terminal["outlier_fraction"] == pytest.approx(54 / 6912)
    assert terminal["nearer_outlier_fraction"] == 0.0
    assert terminal["farther_outlier_fraction"] == terminal["outlier_fraction"]


@pytest.mark.parametrize(
    ("extent", "delta"), [(16, 0.055), (24, -0.055), (64, 0.07), (80, -0.06)]
)
def test_large_flat_deviations_cannot_be_explained_away_as_curvature(extent, delta):
    # Holdout examples rejected a quadratic fit, even with its correction capped
    # at 10% of residual_limit. Keep these as independent negative regressions.
    inverse = 1.0 / ground_depth()
    inverse[
        196 - extent // 2 : 196 + extent // 2,
        160 - extent // 2 : 160 + extent // 2,
    ] += delta
    result = assess_depth(1.0 / inverse, CORRIDOR, SETTINGS)
    assert result["state"] == "obstacle"
    assert result["outlier_fraction"] > SETTINGS.outlier_limit


@pytest.mark.parametrize("delta", [-0.06, 0.06])
def test_smooth_local_deviations_remain_visible(delta):
    y, x = np.mgrid[:240, :320]
    inverse = 1.0 / ground_depth()
    inverse += delta * np.exp(-(((x - 160) / 8) ** 2 + ((y - 196) / 8) ** 2) / 2)
    result = assess_depth(1.0 / inverse, CORRIDOR, SETTINGS)
    assert result["state"] == "obstacle"
    assert result["outlier_fraction"] > SETTINGS.outlier_limit


NEAR_CORRIDOR = (0.35, 0.80, 0.65, 0.95)


@pytest.mark.parametrize("scale", [0.2, 1.0, 5.0])
def test_near_zone_keeps_original_plane_support(scale):
    result = assess_depth(ground_depth() * scale, NEAR_CORRIDOR, SETTINGS)
    assert result["state"] == "clear"
    assert result["plane_reference_corridor"] == CORRIDOR
    assert np.isfinite(result["_errors"]).sum() == 3456


@pytest.mark.parametrize("delta", [0.08, -0.08, 0.2, -0.2])
@pytest.mark.parametrize("shape", ["thin_horizontal", "thin_vertical", "patch"])
def test_near_zone_stops_on_near_obstacles_and_recesses(delta, shape):
    inverse = 1.0 / ground_depth()
    region = {
        "thin_horizontal": (slice(210, 212), slice(135, 185)),
        "thin_vertical": (slice(194, 225), slice(159, 161)),
        "patch": (slice(205, 215), slice(155, 165)),
    }[shape]
    inverse[region] += delta
    result = assess_depth(1.0 / inverse, NEAR_CORRIDOR, SETTINGS)
    assert result["state"] == "obstacle"
    assert result["reason"] == "depth_ground_deviation"


@pytest.mark.parametrize("delta", [0.12, -0.12, 0.2, -0.2])
def test_near_obstacle_covering_whole_stop_zone_does_not_become_the_plane(delta):
    inverse = 1.0 / ground_depth()
    inverse[192:228, 112:208] += delta
    result = assess_depth(1.0 / inverse, NEAR_CORRIDOR, SETTINGS)
    assert result["state"] == "obstacle"


def test_far_local_deviation_is_not_an_immediate_near_zone_stop():
    inverse = 1.0 / ground_depth()
    inverse[162:164, 135:185] += 0.2
    depth = 1.0 / inverse
    assert assess_depth(depth, CORRIDOR, SETTINGS)["state"] == "obstacle"
    near = assess_depth(depth, NEAR_CORRIDOR, SETTINGS)
    assert near["state"] == "clear"
    assert np.isnan(near["_errors"][162:164, 135:185]).all()


def test_recorded_motion_floor_is_clear_with_near_horizon():
    fixture = os.getenv("RIDER_TEST_RECORDED_DEPTH_TRIAL")
    if not fixture:
        pytest.skip("private recorded depth fixture not configured")
    for index in range(4):
        with np.load(Path(fixture) / f"frame-{index}-depth.npz") as archive:
            outcome = assess_depth(archive["depth"], NEAR_CORRIDOR, SETTINGS)
        assert outcome["state"] == "clear"
        assert outcome["plane_reference_corridor"] == CORRIDOR


def test_pixel_count_explains_stop_even_when_p99_is_below_residual_limit():
    # At 0.5%, P99 need not cross the residual threshold to trigger STOP.
    inverse = 1.0 / ground_depth()
    inverse[210:212, 150:165] -= 0.09
    outcome = assess_depth(1.0 / inverse, NEAR_CORRIDOR, SETTINGS)
    assert outcome["state"] == "obstacle"
    assert outcome["zone_pixels"] == 3456
    assert outcome["allowed_outlier_pixels"] == 17
    assert 18 <= outcome["outlier_pixels"] < 35
    assert outcome["plane_residual_p99"] < SETTINGS.residual_limit
    assert outcome["farther_outlier_pixels"] == outcome["outlier_pixels"]
    assert outcome["nearer_outlier_pixels"] == 0
    assert outcome["outlier_fraction"] == outcome["outlier_pixels"] / 3456


def test_preview_border_preserves_every_scored_edge_pixel():
    from PIL import Image, ImageDraw

    from venom_module_rider.depth_detector import draw_stop_border

    for corridor in (NEAR_CORRIDOR, (0.351, 0.801, 0.649, 0.949), (0.0, 0.0, 1.0, 1.0)):
        image = Image.new("RGB", (320, 240), "red")
        draw_stop_border(ImageDraw.Draw(image), (240, 320), corridor)
        pixels = np.asarray(image)
        y, x = np.mgrid[:240, :320]
        x0, y0, x1, y1 = corridor
        mask = (x >= x0 * 320) & (x < x1 * 320) & (y >= y0 * 240) & (y < y1 * 240)
        assert (pixels[mask] == [255, 0, 0]).all()


def test_stop_uses_the_displayed_pixel_allowance_at_float_rounding_boundary():
    inverse = 1.0 / ground_depth()
    inverse[210, 125:186] -= 0.09
    # This representable fraction multiplies back to 60.99999999999999.
    settings = SETTINGS.model_copy(update={"outlier_limit": 61 / 3456})
    outcome = assess_depth(1.0 / inverse, NEAR_CORRIDOR, settings)
    assert outcome["outlier_pixels"] == 61
    assert outcome["allowed_outlier_pixels"] == 60
    assert outcome["outlier_fraction"] == settings.outlier_limit
    assert outcome["state"] == "obstacle"
