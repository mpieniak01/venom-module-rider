"""A contaminated reference cannot confidently label every side as an obstacle."""

import numpy as np
from test_depth_ground_regression import NEAR_CORRIDOR, SETTINGS, ground_depth

from venom_module_rider.depth_detector import assess_depth


def test_incoherent_reference_is_unknown_including_directional_paths():
    inverse = 1 / ground_depth()
    y, x = np.mgrid[:240, :320]
    noisy = (x >= 112) & (x < 208) & (y >= 156) & (y < 228)
    inverse[noisy] += np.where((x[noisy] + y[noisy]) % 2, 0.3, -0.3)
    result = assess_depth(1 / inverse, NEAR_CORRIDOR, SETTINGS)
    assert result["state"] == "unknown"
    assert result["reason"] == "ground_reference_uncertain"
    assert result["ground_reference_valid"] is False
    assert all(row["state"] == "unknown" for row in result["sectors"].values())
    assert all(row["state"] == "unknown" for row in result["paths"].values())


def test_small_real_obstacle_remains_obstacle_with_good_reference():
    inverse = 1 / ground_depth()
    inverse[205:215, 155:165] += 0.2
    result = assess_depth(1 / inverse, NEAR_CORRIDOR, SETTINGS)
    assert result["state"] == "obstacle"
    assert result["ground_reference_valid"] is True
