"""Directional diagnostics share the central ground fit; no new inference."""

import math


def assess_sectors(depth, corridor, coefficients, scale, settings, oriented):
    import numpy as np

    h, w = depth.shape
    y, x = np.mgrid[:h, :w]
    x0, y0, x1, y1 = corridor
    width = x1 - x0
    regions = {
        "left": (x0 - width, y0, x0, y1),
        "center": corridor,
        "right": (x1, y0, x1 + width, y1),
    }
    result = {}
    for name, bounds in regions.items():
        left, top, right, bottom = bounds
        complete = 0 <= left < right <= 1
        mask = (x >= left * w) & (x < right * w) & (y >= top * h) & (y < bottom * h)
        pixels = int(mask.sum())
        row = {"corridor": bounds, "coverage_complete": complete, "zone_pixels": pixels}
        if not complete or pixels < 64 or not oriented:
            result[name] = {
                **row,
                "state": "unknown",
                "reason": "directional_coverage_or_orientation",
            }
            continue
        design = np.column_stack((x[mask] / w, y[mask] / h, np.ones(pixels)))
        errors = (1 / depth[mask] - design @ coefficients) / scale
        outliers = abs(errors) > settings.residual_limit
        points_x, points_y = x[mask][outliers], y[mask][outliers]
        near = int((errors > settings.residual_limit).sum())
        far = int((errors < -settings.residual_limit).sum())
        allowed = math.floor(settings.outlier_limit * pixels)
        result[name] = {
            **row,
            "state": "obstacle" if near + far > allowed else "clear",
            "nearer_outlier_pixels": near,
            "farther_outlier_pixels": far,
            "outlier_fraction": (near + far) / pixels,
            "allowed_outlier_pixels": allowed,
            "outlier_centroid_image": (
                [float(points_x.mean() / w), float(points_y.mean() / h)]
                if near + far
                else None
            ),
            "outlier_bounds_image": (
                [
                    float(points_x.min() / w),
                    float(points_y.min() / h),
                    float((points_x.max() + 1) / w),
                    float((points_y.max() + 1) / h),
                ]
                if near + far
                else None
            ),
        }
    return result
