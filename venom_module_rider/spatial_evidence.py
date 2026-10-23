"""Deterministic image-space evidence shared by live and offline navigation.

All fractions refer to the source-resolution corridor. No distance or physical
clearance is inferred from a pixel coordinate.
"""

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any


def _fraction(params: Mapping[str, Any], name: str) -> float:
    value = params.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("navigation_spatial_params")
    result = float(value)
    if not isfinite(result) or not 0 <= result <= 1:
        raise ValueError("navigation_spatial_params")
    return result


def corridor_mask(corridor: Any, shape: tuple[int, int]) -> Any:
    """Resolve a source mask or normalized xyxy rectangle without resizing."""
    import numpy as np

    if isinstance(corridor, np.ndarray):
        if corridor.shape != shape or corridor.dtype != np.bool_:
            raise ValueError("navigation_corridor")
        result = corridor.copy()
    elif isinstance(corridor, Sequence) and len(corridor) == 4:
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
            for value in corridor
        ):
            raise ValueError("navigation_corridor")
        x0, y0, x1, y1 = (float(value) for value in corridor)
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("navigation_corridor")
        height, width = shape
        result = np.zeros(shape, dtype=bool)
        result[
            int(y0 * height) : max(int(y0 * height) + 1, int(y1 * height)),
            int(x0 * width) : max(int(x0 * width) + 1, int(x1 * width)),
        ] = True
    else:
        raise ValueError("navigation_corridor")
    if not result.any():
        raise ValueError("navigation_corridor")
    return result


def semantic_masks(
    labels: Any, confidence: Any, pixel_confidence: Any
) -> dict[str, Any]:
    """Threshold compact source maps identically for replay and live inference.

    Confidence is a model score, not a calibrated collision probability. ADE20k
    floor remains class 3; rug/road/sidewalk are not silently made traversable.
    """
    import numpy as np

    if (
        not isinstance(labels, np.ndarray)
        or not isinstance(confidence, np.ndarray)
        or labels.ndim != 2
        or min(labels.shape) < 1
        or labels.shape[0] > 720
        or labels.shape[1] > 1280
        or labels.dtype != np.uint8
        or confidence.shape != labels.shape
        or confidence.dtype not in (np.float16, np.float32)
        or not np.isfinite(confidence).all()
        or (confidence < 0).any()
        or (confidence > 1).any()
        or (labels >= 150).any()
    ):
        raise ValueError("navigation_semantic_maps")
    if (
        isinstance(pixel_confidence, bool)
        or not isinstance(pixel_confidence, (int, float))
        or not isfinite(float(pixel_confidence))
        or not 0 < pixel_confidence <= 1
    ):
        raise ValueError("navigation_semantic_params")
    confident = confidence.astype(np.float32, copy=False) >= float(pixel_confidence)
    return {
        "floor": (labels == 3) & confident,
        "obstacle": (labels != 3) & confident,
        "uncertain": ~confident,
        "valid": True,
    }


def assess_maps(
    floor_mask: Any,
    obstacle_mask: Any,
    uncertain_mask: Any,
    corridor: Any,
    params: Mapping[str, Any],
    *,
    critical_mask: Any = None,
) -> dict[str, Any]:
    """Aggregate confident source masks; preserve thin intersecting obstacles.

    critical_mask denotes an already established unsafe support/geometry signal,
    not a low-confidence semantic label. It is never subjected to noise filtering.
    Returned internal masks start with an underscore and are not JSON diagnostics.
    """
    import cv2
    import numpy as np

    masks = (floor_mask, obstacle_mask, uncertain_mask)
    if any(not isinstance(mask, np.ndarray) for mask in masks):
        raise ValueError("navigation_maps")
    shape = floor_mask.shape
    if (
        len(shape) != 2
        or min(shape) < 1
        or shape[0] > 720
        or shape[1] > 1280
        or any(mask.shape != shape or mask.dtype != np.bool_ for mask in masks)
    ):
        raise ValueError("navigation_maps")
    floor, obstacle, uncertain = masks
    if (
        (floor & obstacle).any()
        or (floor & uncertain).any()
        or (obstacle & uncertain).any()
        or not (floor | obstacle | uncertain).all()
    ):
        raise ValueError("navigation_maps_partition")
    if critical_mask is not None and (
        not isinstance(critical_mask, np.ndarray)
        or critical_mask.shape != shape
        or critical_mask.dtype != np.bool_
    ):
        raise ValueError("navigation_critical_mask")
    minimum = _fraction(params, "min_region_fraction")
    thin_span = _fraction(params, "thin_span_fraction")
    near_fraction = _fraction(params, "near_zone_fraction")
    required_floor = _fraction(params, "min_floor_fraction")
    required_near = _fraction(params, "min_near_floor_fraction")
    max_uncertain = _fraction(params, "max_uncertain_fraction")
    if near_fraction <= 0 or thin_span <= 0:
        raise ValueError("navigation_spatial_params")
    route = corridor_mask(corridor, shape)
    ys, xs = np.nonzero(route)
    top, bottom = int(ys.min()), int(ys.max()) + 1
    left, right = int(xs.min()), int(xs.max()) + 1
    near = route.copy()
    near[: max(top, bottom - max(1, round((bottom - top) * near_fraction)))] = False
    pixels = int(route.sum())
    near_pixels = int(near.sum())
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        (obstacle & route).astype(np.uint8), connectivity=8
    )
    areas = stats[:, cv2.CC_STAT_AREA]
    spans = np.maximum(
        stats[:, cv2.CC_STAT_WIDTH] / (right - left),
        stats[:, cv2.CC_STAT_HEIGHT] / (bottom - top),
    )
    near_counts = np.bincount(labels[near], minlength=count)
    kept = (areas / pixels >= minimum) | (spans >= thin_span)
    kept[0] = False
    accepted = kept[labels]
    kept_ids = np.flatnonzero(kept)
    has_near = bool((near_counts[kept_ids] > 0).any())
    critical_pixels = (
        int((critical_mask & route).sum()) if critical_mask is not None else 0
    )
    regions = []
    for label in kept_ids[:64]:
        x, y, width, height, area = (int(value) for value in stats[label])
        regions.append(
            {
                "bbox": [x, y, x + width, y + height],
                "area_pixels": area,
                "area_fraction": area / pixels,
                "near": bool(near_counts[label]),
                "thin": bool(spans[label] >= thin_span and area / pixels < minimum),
            }
        )
    floor_fraction = float(floor[route].mean())
    uncertain_fraction = float(uncertain[route].mean())
    near_floor = float(floor[near].mean())
    near_uncertain = float(uncertain[near].mean())
    hard_stop = bool(critical_pixels or has_near)
    guard_clear = bool(
        not hard_stop
        and near_floor >= required_near
        and near_uncertain <= max_uncertain
    )
    if hard_stop:
        candidate, reason = "obstacle", "navigation_near_obstacle"
        if critical_pixels:
            reason = "navigation_critical_support"
    elif kept_ids.size:
        candidate, reason = "obstacle", "navigation_region_in_corridor"
    elif (
        guard_clear
        and floor_fraction >= required_floor
        and uncertain_fraction <= max_uncertain
    ):
        candidate, reason = "clear", "navigation_floor_supported"
    else:
        candidate, reason = "uncertain", "navigation_local_uncertainty"
    # A diagnostic floor boundary follows connected floor up from the bottom.
    # It is image geometry, not calibrated distance or evidence of obstacle height.
    boundary = []
    for x in np.linspace(left, right - 1, min(33, right - left), dtype=int):
        column = np.flatnonzero(route[:, x])
        if column.size == 0:
            continue
        y = int(column[-1])
        if not floor[y, x]:
            boundary.append({"x": int(x), "y": y})
            continue
        while y > int(column[0]) and route[y - 1, x] and floor[y - 1, x]:
            y -= 1
        boundary.append({"x": int(x), "y": y})
    return {
        "valid": True,
        "reason": reason,
        "candidate": candidate,
        "guard_clear": guard_clear,
        "hard_stop": hard_stop,
        "floor_fraction": floor_fraction,
        "uncertain_fraction": uncertain_fraction,
        "obstacle_fraction": float(accepted[route].mean()),
        "near_floor_fraction": near_floor,
        "near_uncertain_fraction": near_uncertain,
        "regions": regions,
        "boundary": boundary,
        "counts": {
            "corridor_pixels": pixels,
            "near_pixels": near_pixels,
            "floor_pixels": int((floor & route).sum()),
            "uncertain_pixels": int((uncertain & route).sum()),
            "obstacle_pixels": int(accepted.sum()),
            "critical_pixels": critical_pixels,
            "regions": int(kept_ids.size),
            "regions_returned": len(regions),
            "ignored_obstacle_pixels": int(((obstacle & route) & ~accepted).sum()),
        },
        "_floor_mask": floor.copy(),
        "_obstacle_mask": accepted,
        "_uncertain_mask": uncertain.copy(),
    }
