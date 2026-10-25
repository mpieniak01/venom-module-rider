"""Directional diagnostics share the central ground fit; no new inference."""

import math


def sector_regions(corridor):
    """The shared configured layout; these are not measured distances."""
    x0, y0, x1, y1 = corridor
    width = x1 - x0
    return {
        "left": (x0 - width, y0, x0, y1),
        "center": corridor,
        "right": (x1, y0, x1 + width, y1),
    }


def assess_sectors(depth, corridor, coefficients, scale, settings, oriented):
    import numpy as np

    h, w = depth.shape
    y, x = np.mgrid[:h, :w]
    result = {}
    for name, bounds in sector_regions(corridor).items():
        left, top, right, bottom = bounds
        complete = 0 <= left < right <= 1
        mask = (x >= left * w) & (x < right * w) & (y >= top * h) & (y < bottom * h)
        complete = complete and bool(
            np.isfinite(depth[mask]).all() and (depth[mask] > 0).all()
        )
        pixels = int(mask.sum())
        row = {"corridor": bounds, "coverage_complete": complete, "zone_pixels": pixels}
        if not complete or pixels < 64 or not oriented:
            result[name] = {
                **row,
                "state": "unknown",
                "reason": "directional_coverage_or_orientation",
            }
            continue
        result[name] = {**row, **score_mask(depth, mask, coefficients, scale, settings)}
    return result


def score_mask(depth, mask, coefficients, scale, settings):
    import numpy as np

    h, w = depth.shape
    y, x = np.mgrid[:h, :w]
    pixels = int(mask.sum())
    design = np.column_stack((x[mask] / w, y[mask] / h, np.ones(pixels)))
    errors = (1 / depth[mask] - design @ coefficients) / scale
    outliers = abs(errors) > settings.residual_limit
    points_x, points_y = x[mask][outliers], y[mask][outliers]
    near = int((errors > settings.residual_limit).sum())
    far = int((errors < -settings.residual_limit).sum())
    allowed = math.floor(settings.outlier_limit * pixels)
    return {
        "state": "obstacle" if near + far > allowed else "clear",
        "nearer_outlier_pixels": near,
        "farther_outlier_pixels": far,
        "outlier_fraction": (near + far) / pixels,
        "allowed_outlier_pixels": allowed,
        "outlier_centroid_image": [
            float(points_x.mean() / w),
            float(points_y.mean() / h),
        ]
        if near + far
        else None,
        "outlier_bounds_image": [
            float(points_x.min() / w),
            float(points_y.min() / h),
            float((points_x.max() + 1) / w),
            float((points_y.max() + 1) / h),
        ]
        if near + far
        else None,
    }


def assess_paths(depth, corridor, coefficients, scale, settings, oriented):
    """Image-space swept-corridor approximation, not a metric vehicle model."""
    import numpy as np

    h, w = depth.shape
    y, x = np.mgrid[:h, :w]
    x0, y0, x1, y1 = corridor
    width = x1 - x0
    progress = np.clip((y1 - y / h) / (y1 - y0), 0, 1)
    result = {}
    for name, sign in (("left", -1), ("right", 1), ("guard", 0)):
        shift = sign * width * progress
        top = y0 if sign else y1 - (y1 - y0) / 3
        mask = (
            (y / h >= top) & (y / h < y1) & (x / w >= x0 + shift) & (x / w < x1 + shift)
        )
        complete = 0 <= x0 + sign * width and x1 + sign * width <= 1
        complete = complete and bool(
            np.isfinite(depth[mask]).all() and (depth[mask] > 0).all()
        )
        pixels = int(mask.sum())
        row = {
            "coverage_complete": complete,
            "zone_pixels": pixels,
            "geometry": "image_corridor_approximation",
        }
        if not oriented or not complete or pixels < 64:
            row.update(state="unknown", reason="directional_coverage_or_orientation")
        else:
            row.update(score_mask(depth, mask, coefficients, scale, settings))
        result[name] = row
    return result
