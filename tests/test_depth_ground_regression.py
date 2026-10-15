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
