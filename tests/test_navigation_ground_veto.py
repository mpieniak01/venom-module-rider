"""Calibrated body-volume evidence must survive compact floor-map generation."""

import numpy as np
import pytest
from test_adaptive_ground import CAMERA, SETTINGS, scene

from venom_module_rider.adaptive_ground import assess
from venom_module_rider.navigation_profiles import DEFAULT_BEHAVIOR, parameters

CORRIDOR = (0.35, 0.8, 0.65, 0.95)


def profile(name):
    return parameters({**DEFAULT_BEHAVIOR, "profile_id": name})


@pytest.mark.parametrize("name", ["cautious", "balanced", "fluid"])
def test_elevated_near_box_keeps_full_body_collision_and_guard(name):
    # A hanging object can be above the projected floor footprint while its
    # 3D points lie inside the swept body corridor.
    depth = scene(box=((-0.1, 0.1), (0.4, 0.5), (0.8, 1.0)))
    legacy = assess(depth, CORRIDOR, SETTINGS, CAMERA)
    current = assess(depth, CORRIDOR, SETTINGS, CAMERA, evidence=profile(name))
    assert legacy["state"] == current["state"] == "obstacle"
    assert legacy["paths"] == current["paths"]
    assert current["paths"]["guard"]["state"] == "obstacle"
    assert current["heading_candidates"][24]["obstacle_points"] > 1000
    maps = current["_navigation_maps"]
    assert maps["floor"][maps["corridor"]].all()
    assert maps["geometry_guard_clear"] is False
    assert maps["geometry_collision"] is True
    assert maps["geometry_blocked"] is True
    assert maps["geometry_complete"] is True


@pytest.mark.parametrize("name", ["cautious", "balanced", "fluid"])
def test_elevated_far_collision_is_not_deleted_by_ground_footprint(name):
    depth = scene(box=((-0.1, 0.1), (1.3, 1.4), (0.4, 0.6)))
    legacy = assess(depth, CORRIDOR, SETTINGS, CAMERA)
    current = assess(depth, CORRIDOR, SETTINGS, CAMERA, evidence=profile(name))
    assert current["state"] == legacy["state"] == "obstacle"
    assert current["heading_candidates"] == legacy["heading_candidates"]
    maps = current["_navigation_maps"]
    assert maps["geometry_collision"] is True
    assert maps["geometry_blocked"] is True
    assert maps["geometry_guard_clear"] is True
    assert current["heading_candidates"][24]["obstacle_points"] > 50


@pytest.mark.parametrize("name", ["cautious", "balanced", "fluid"])
@pytest.mark.parametrize(
    "box",
    [
        ((-0.02, 0.02), (0.6, 0.8), (0.0, 0.8)),  # narrow vertical leg
        ((-0.2, 0.2), (0.55, 0.85), (0.0, 0.6)),  # ordinary floor-supported box
    ],
)
def test_legs_and_floor_supported_boxes_keep_legacy_geometry_veto(name, box):
    depth = scene(box=box)
    legacy = assess(depth, CORRIDOR, SETTINGS, CAMERA)
    current = assess(depth, CORRIDOR, SETTINGS, CAMERA, evidence=profile(name))
    assert current["state"] == legacy["state"] == "obstacle"
    assert current["paths"] == legacy["paths"]
    assert current["gaps"] == legacy["gaps"]
    maps = current["_navigation_maps"]
    assert maps["geometry_guard_clear"] is False
    assert maps["geometry_collision"] is True


@pytest.mark.parametrize("name", ["cautious", "balanced", "fluid"])
@pytest.mark.parametrize("pitch,height", [(45.0, 0.7), (52.0, 1.0), (60.0, 1.4)])
def test_free_plane_retains_legacy_paths_and_source_compact_maps(name, pitch, height):
    depth = scene(pitch=pitch, height=height)
    legacy = assess(depth, CORRIDOR, SETTINGS, CAMERA)
    current = assess(depth, CORRIDOR, SETTINGS, CAMERA, evidence=profile(name))
    assert legacy["state"] == current["state"] == "clear"
    assert current["paths"] == legacy["paths"]
    assert current["heading_candidates"] == legacy["heading_candidates"]
    assert "_navigation_maps" not in legacy
    maps = current["_navigation_maps"]
    assert maps["corridor"].dtype == np.bool_
    assert maps["corridor"].shape == maps["floor"].shape == depth.shape
    assert maps["valid"] is True
    assert maps["geometry_guard_clear"] is True
    assert maps["geometry_collision"] is False
    assert maps["geometry_blocked"] is False
    assert maps["geometry_complete"] is True


def test_incomplete_projection_is_not_made_valid_by_compact_floor_maps():
    current = assess(
        scene(pitch=30.0), CORRIDOR, SETTINGS, CAMERA, evidence=profile("balanced")
    )
    assert current["state"] == "unknown"
    maps = current["_navigation_maps"]
    assert maps["valid"] is False
    assert maps["geometry_complete"] is False
    assert maps["geometry_guard_clear"] is False
