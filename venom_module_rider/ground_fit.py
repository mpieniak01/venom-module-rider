"""Robust pinhole-ground reference, scale invariant and estimated each capture."""

import math


def normal_from_plane(coefficients, camera):
    import numpy as np

    a, b, c = coefficients
    raw = np.asarray([a * camera.fx, b * camera.fy, a * camera.cx + b * camera.cy + c])
    norm = float(np.linalg.norm(raw))
    if norm <= 1e-8 or not np.isfinite(norm):
        return None
    normal = raw / norm
    # Broad physical orientation constraint, not the old image slope 2..6.
    # A wall or box face cannot replace a floor just by being the largest plane.
    if normal[1] < 0.2:
        return None
    return normal, 1.0 / norm


def fit(depth, camera, tolerance):
    import numpy as np

    h, w = depth.shape
    ys, xs = np.mgrid[
        int(h * 0.35) : int(h * 0.98) : max(1, h // 32), : w : max(1, w // 48)
    ]
    u, v, z = xs.ravel() / w, ys.ravel() / h, depth[ys, xs].ravel()
    design = np.column_stack((u, v, np.ones(z.size)))
    inverse = 1 / z
    rng = np.random.default_rng(258)
    best, best_count = None, 0
    threshold = min(tolerance / 2, 0.04)
    for _ in range(128):
        indices = rng.choice(z.size, 3, replace=False)
        try:
            coefficients = np.linalg.solve(design[indices], inverse[indices])
        except np.linalg.LinAlgError:
            continue
        orientation = normal_from_plane(coefficients, camera)
        if orientation is None:
            continue
        inliers = np.abs(z * (design @ coefficients) - 1) <= threshold
        count = int(inliers.sum())
        if count <= best_count or count < 0.3 * z.size:
            continue
        if not supported(inliers, u, v):
            continue
        best, best_count = inliers, count
    if best is None:
        return None
    for _ in range(3):
        coefficients = np.linalg.lstsq(design[best], inverse[best], rcond=None)[0]
        updated = np.abs(z * (design @ coefficients) - 1) <= threshold
        if not supported(updated, u, v):
            return None
        best = updated
    orientation = normal_from_plane(coefficients, camera)
    if orientation is None:
        return None
    normal, height = orientation
    forward = np.asarray([0.0, 0.0, 1.0]) - normal[2] * normal
    length = np.linalg.norm(forward)
    if length < 0.1:
        return None
    forward /= length
    right = np.cross(normal, forward)
    return {
        "coefficients": coefficients,
        "normal": normal,
        "height": height,
        "forward": forward,
        "right": right,
        "support": float(best.mean()),
    }


def supported(inliers, u, v):
    """Keep wide, near and majority support through every trimmed refit."""
    import numpy as np

    if inliers.sum() < 0.3 * inliers.size:
        return False
    columns = np.unique(np.minimum((u[inliers] * 6).astype(int), 5)).size
    rows = np.unique(np.minimum(((v[inliers] - 0.35) / 0.63 * 3).astype(int), 2)).size
    near = v >= 0.85
    return columns >= 4 and rows >= 2 and near.any() and inliers[near].mean() >= 0.2


def pose_change(previous, reference, *, angle_limit=3.0, scale_limit=0.15):
    """Small pose changes are compensated in ground coordinates, not re-armed."""
    if previous is None:
        return True
    dot = sum(
        a * b for a, b in zip(previous["normal"], reference["normal"], strict=True)
    )
    angle = math.degrees(math.acos(max(-1.0, min(1.0, dot))))
    ratio = reference["height"] / previous["height"]
    return angle > angle_limit or not 1 - scale_limit <= ratio <= 1 + scale_limit
