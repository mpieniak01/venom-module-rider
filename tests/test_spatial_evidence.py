"""Spatial aggregation fixtures use source masks, never rendered overlays."""

import numpy as np
import pytest

from venom_module_rider.spatial_evidence import assess_maps, corridor_mask


@pytest.fixture
def params():
    return {
        "min_region_fraction": 0.02,
        "thin_span_fraction": 0.4,
        "near_zone_fraction": 0.25,
        "min_floor_fraction": 0.95,
        "min_near_floor_fraction": 0.98,
        "max_uncertain_fraction": 0.04,
    }


def maps():
    floor = np.ones((100, 100), dtype=bool)
    return floor, np.zeros_like(floor), np.zeros_like(floor)


def test_positive_floor_boundary_and_counts(params):
    result = assess_maps(*maps(), [0, 0, 1, 1], params)
    assert result["candidate"] == "clear"
    assert result["guard_clear"] is True
    assert result["hard_stop"] is False
    assert result["counts"]["corridor_pixels"] == 10000
    assert result["counts"]["near_pixels"] == 2500
    assert len(result["boundary"]) == 33
    assert all(point["y"] == 0 for point in result["boundary"])


def test_isolated_uncertain_and_obstacle_pixels_do_not_become_regions(params):
    floor, obstacle, uncertain = maps()
    floor[10, 10] = floor[11, 11] = False
    obstacle[10, 10] = True
    uncertain[11, 11] = True
    result = assess_maps(floor, obstacle, uncertain, [0, 0, 1, 1], params)
    assert result["candidate"] == "clear"
    assert result["counts"]["ignored_obstacle_pixels"] == 1
    assert result["counts"]["uncertain_pixels"] == 1
    assert not result["_obstacle_mask"].any()


def test_thin_wire_across_corridor_is_preserved_despite_small_area(params):
    floor, obstacle, uncertain = maps()
    floor[20, :] = False
    obstacle[20, :] = True
    result = assess_maps(floor, obstacle, uncertain, [0, 0, 1, 1], params)
    assert result["candidate"] == "obstacle"
    assert result["hard_stop"] is False
    assert result["regions"][0]["thin"] is True
    assert result["counts"]["obstacle_pixels"] == 100


def test_thin_leg_in_near_zone_is_immediate_stop(params):
    floor, obstacle, uncertain = maps()
    floor[30:99, 50] = False
    obstacle[30:99, 50] = True
    result = assess_maps(floor, obstacle, uncertain, [0, 0, 1, 1], params)
    assert result["hard_stop"] is True
    assert result["guard_clear"] is False
    assert result["regions"][0]["near"] is True
    assert result["reason"] == "navigation_near_obstacle"


def test_large_far_obstacle_and_uncertain_near_floor(params):
    floor, obstacle, uncertain = maps()
    floor[10:35, 30:60] = False
    obstacle[10:35, 30:60] = True
    result = assess_maps(floor, obstacle, uncertain, [0, 0, 1, 1], params)
    assert result["candidate"] == "obstacle"
    assert result["guard_clear"] is True
    assert result["regions"][0]["thin"] is False
    floor, obstacle, uncertain = maps()
    floor[90:, :] = False
    uncertain[90:, :] = True
    result = assess_maps(floor, obstacle, uncertain, [0, 0, 1, 1], params)
    assert result["candidate"] == "uncertain"
    assert result["guard_clear"] is False
    assert all(point["y"] == 99 for point in result["boundary"])


def test_critical_support_is_never_filtered_but_must_intersect_route(params):
    critical = np.zeros((100, 100), dtype=bool)
    critical[1, 1] = True
    route = [0.2, 0.2, 0.8, 0.9]
    result = assess_maps(*maps(), route, params, critical_mask=critical)
    assert result["hard_stop"] is False
    critical[30, 40] = True
    result = assess_maps(*maps(), route, params, critical_mask=critical)
    assert result["reason"] == "navigation_critical_support"
    assert result["hard_stop"] is True


def test_nonrectangular_source_route_and_empty_columns(params):
    route = np.zeros((100, 100), dtype=bool)
    route[30:99, 10:20] = True
    route[70:99, 80:90] = True
    result = assess_maps(*maps(), route, params)
    assert result["counts"]["corridor_pixels"] == int(route.sum())
    assert all(route[point["y"], point["x"]] for point in result["boundary"])
    assert np.array_equal(corridor_mask(route, route.shape), route)


@pytest.mark.parametrize(
    "corridor",
    [
        None,
        [],
        [0, 0, 0, 1],
        [0, 0, 1.1, 1],
        [0, 0, float("nan"), 1],
        [False, 0, 1, 1],
        np.zeros((100, 100), dtype=bool),
        np.ones((10, 10), dtype=bool),
        np.ones((100, 100), dtype=np.uint8),
    ],
)
def test_invalid_corridor_rejected(params, corridor):
    with pytest.raises(ValueError, match="navigation_corridor"):
        assess_maps(*maps(), corridor, params)


@pytest.mark.parametrize(
    "name",
    [
        "min_region_fraction",
        "thin_span_fraction",
        "near_zone_fraction",
        "min_floor_fraction",
        "min_near_floor_fraction",
        "max_uncertain_fraction",
    ],
)
@pytest.mark.parametrize("value", [None, True, -0.1, 1.1, float("inf"), "0.1"])
def test_invalid_parameters_rejected(params, name, value):
    params[name] = value
    with pytest.raises(ValueError, match="navigation_spatial_params"):
        assess_maps(*maps(), [0, 0, 1, 1], params)


@pytest.mark.parametrize("name", ["near_zone_fraction", "thin_span_fraction"])
def test_zero_near_or_thin_span_rejected(params, name):
    params[name] = 0
    with pytest.raises(ValueError, match="navigation_spatial_params"):
        assess_maps(*maps(), [0, 0, 1, 1], params)


def test_bad_masks_and_partition_rejected(params):
    floor, obstacle, uncertain = maps()
    cases = [
        (None, obstacle, uncertain),
        (floor.astype(float), obstacle, uncertain),
        (floor[:1], obstacle, uncertain),
        (np.ones((721, 100), dtype=bool),) * 3,
        (np.ones((100, 1281), dtype=bool),) * 3,
        (floor.reshape(-1),) * 3,
    ]
    for case in cases:
        with pytest.raises(ValueError, match="navigation_maps"):
            assess_maps(*case, [0, 0, 1, 1], params)
    obstacle[0, 0] = True
    with pytest.raises(ValueError, match="navigation_maps_partition"):
        assess_maps(floor, obstacle, uncertain, [0, 0, 1, 1], params)
    obstacle[0, 0] = floor[0, 0] = False
    with pytest.raises(ValueError, match="navigation_maps_partition"):
        assess_maps(floor, obstacle, uncertain, [0, 0, 1, 1], params)
    for bad in [[], np.zeros((100, 100)), np.zeros((20, 20), dtype=bool)]:
        with pytest.raises(ValueError, match="navigation_critical_mask"):
            assess_maps(*maps(), [0, 0, 1, 1], params, critical_mask=bad)


def test_semantic_thresholds_preserve_only_confident_class_three():
    from venom_module_rider.spatial_evidence import semantic_masks

    labels = np.array([[3, 28, 11, 6], [149, 0, 3, 3]], dtype=np.uint8)
    confidence = np.array(
        [[0.9, 0.9, 0.9, 0.9], [0.9, 0.7, 0.76, 0.81]], dtype=np.float32
    )
    cautious = semantic_masks(labels, confidence, 0.8)
    balanced = semantic_masks(labels, confidence, 0.75)
    assert cautious["floor"].sum() == 2
    assert balanced["floor"].sum() == 3
    assert cautious["obstacle"].sum() == balanced["obstacle"].sum() == 4
    assert np.all(cautious["floor"] | cautious["obstacle"] | cautious["uncertain"])
    assert not (cautious["floor"] & cautious["uncertain"]).any()
    assert np.array_equal(
        semantic_masks(labels, confidence.astype(np.float16), 0.75)["floor"],
        balanced["floor"],
    )


@pytest.mark.parametrize(
    "bad",
    [
        None,
        np.full((2, 2), np.nan, dtype=np.float32),
        np.full((2, 2), np.inf, dtype=np.float32),
        np.full((2, 2), -0.01, dtype=np.float32),
        np.full((2, 2), 1.01, dtype=np.float32),
        np.ones((2, 3), dtype=np.float32),
        np.ones((2, 2), dtype=np.uint8),
        np.ones((2, 2), dtype=np.float64),
    ],
)
def test_semantic_invalid_confidence_never_clamped_to_clear(bad):
    from venom_module_rider.spatial_evidence import semantic_masks

    labels = np.full((2, 2), 3, dtype=np.uint8)
    with pytest.raises(ValueError, match="navigation_semantic_maps"):
        semantic_masks(labels, bad, 0.8)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        np.full((2, 2), 150, dtype=np.uint8),
        np.full((2, 2), 255, dtype=np.uint8),
        np.full((2, 2), 3, dtype=np.int32),
        np.full((721, 2), 3, dtype=np.uint8),
        np.full((2, 1281), 3, dtype=np.uint8),
        np.ones((2, 2, 1), dtype=np.uint8),
        np.ones((0, 0), dtype=np.uint8),
    ],
)
def test_semantic_invalid_labels_rejected(bad):
    from venom_module_rider.spatial_evidence import semantic_masks

    confidence = np.ones((2, 2), dtype=np.float16)
    with pytest.raises(ValueError, match="navigation_semantic_maps"):
        semantic_masks(bad, confidence, 0.8)


@pytest.mark.parametrize("threshold", [None, True, 0, -0.1, 1.01, float("nan"), "0.8"])
def test_semantic_invalid_threshold_rejected(threshold):
    from venom_module_rider.spatial_evidence import semantic_masks

    with pytest.raises(ValueError, match="navigation_semantic_params"):
        semantic_masks(
            np.full((2, 2), 3, dtype=np.uint8),
            np.ones((2, 2), dtype=np.float16),
            threshold,
        )


def test_float16_threshold_is_compared_in_float32_without_scalar_rounding():
    from venom_module_rider.spatial_evidence import semantic_masks

    confidence = np.full((2, 2), 0.8, dtype=np.float16)
    assert float(confidence[0, 0]) < 0.8
    result = semantic_masks(np.full((2, 2), 3, dtype=np.uint8), confidence, 0.8)
    assert result["uncertain"].all()
