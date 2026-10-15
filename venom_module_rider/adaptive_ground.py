"""One adaptive assessment from the same depth tensor; no additional model."""

import numpy as np

from .ground_fit import fit
from .ground_paths import assess as assess_paths


def assess(depth, corridor, settings, camera):
    if not camera.calibrated():
        return unknown(depth, corridor, "geometry_calibration_missing")
    if (
        depth.ndim != 2
        or min(depth.shape) < 32
        or not np.isfinite(depth).all()
        or (depth <= 0).any()
    ):
        raise ValueError("vision_model_output")
    reference = fit(depth, camera, settings.residual_limit)
    if reference is None:
        return unknown(depth, corridor, "ground_reference_uncertain")
    h, w = depth.shape
    y, x = np.mgrid[:h, :w]
    u, v = x / w, y / h
    coefficients = reference["coefficients"]
    errors = abs(
        1 - depth * (coefficients[0] * u + coefficients[1] * v + coefficients[2])
    )
    path = assess_paths(
        depth, reference, camera, settings.residual_limit, settings.outlier_limit
    )
    sectors = {side: {**row} for side, row in path["paths"].items() if side != "guard"}
    sectors["center"] = {
        "state": path["state"],
        "coverage_complete": path["paths"]["guard"]["coverage_complete"],
    }
    signature = {"normal": reference["normal"].tolist(), "height": reference["height"]}
    return {
        **path,
        "reason": (
            "ground_path_candidate"
            if path["state"] == "clear"
            else "ground_projection_incomplete"
            if path["state"] == "unknown"
            else "ground_path_blocked"
        ),
        "sectors": sectors,
        "ground_reference": signature,
        "ground_support": reference["support"],
        "pose_source": "current_depth_ground_plane",
        "height_source": "model_units_not_metres",
        "geometry": "ground_capsule",
        "plane_reference_corridor": (0.0, 0.35, 1.0, 0.98),
        "perception_engine": "depth_plane",
        "plane_slope": float(coefficients[1]),
        "plane_residual_p99": float(np.quantile(errors, 0.99)),
        "residual_limit": settings.residual_limit,
        "outlier_limit": settings.outlier_limit,
        "drive_supported": True,
        "distance_measurement": False,
        "_errors": errors,
    }


def unknown(depth, corridor, reason):
    return {
        "state": "unknown",
        "reason": reason,
        "perception_engine": "depth_plane",
        "geometry": "ground_capsule",
        "distance_measurement": False,
        "pose_source": "unavailable",
        "plane_reference_corridor": corridor,
        "_errors": np.full(depth.shape, np.nan),
    }
