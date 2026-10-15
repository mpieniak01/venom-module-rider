"""Sector decisions use one common reference, including obstacle/recess signs."""

import numpy as np
import pytest

from venom_module_rider.depth_detector import PerceptionSettings, assess_depth

ROI = (0.35, 0.80, 0.65, 0.95)
SETTINGS = PerceptionSettings(engine="depth_plane")


def plane():
    y, x = np.mgrid[:240, :320]
    return 0.1 * x / 320 + 3.5 * y / 240 - 2


@pytest.mark.parametrize(
    "side,columns", [("left", slice(25, 90)), ("right", slice(230, 295))]
)
@pytest.mark.parametrize("delta", [0.2, -0.2])
def test_sided_obstacle_or_recess_does_not_change_central_fit(side, columns, delta):
    inverse = np.maximum(plane(), 0.1)
    inverse[200:220, columns] += delta
    result = assess_depth(1 / inverse, ROI, SETTINGS)
    assert result["state"] == "clear"
    sectors = result["sectors"]
    assert sectors[side]["state"] == "obstacle"
    other = "right" if side == "left" else "left"
    assert sectors[other]["state"] == "clear"
    assert (
        sectors[side][
            "nearer_outlier_pixels" if delta > 0 else "farther_outlier_pixels"
        ]
        > 0
    )


def test_clipped_side_is_unknown_not_clear():
    inverse = np.maximum(plane(), 0.1)
    result = assess_depth(1 / inverse, (0.1, 0.8, 0.6, 0.95), SETTINGS)
    assert result["sectors"]["left"]["state"] == "unknown"
    assert result["sectors"]["left"]["coverage_complete"] is False
