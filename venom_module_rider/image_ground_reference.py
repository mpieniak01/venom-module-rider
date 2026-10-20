"""Conservative recovery of a contaminated image-plane reference."""


def recover(design, inverse, scale, settings):
    """Accept a trimmed plane only with broad 80% support, including far rows.

    The STOP zone is never shrunk and all its pixels are still scored. A plane
    supported only by a large near obstacle cannot replace the reference.
    """
    import numpy as np

    rng = np.random.default_rng(267)
    y = design[:, 1]
    bands = np.minimum(((y - y.min()) / max(float(np.ptp(y)), 1e-9) * 4).astype(int), 3)
    best, count = None, 0
    for _ in range(64):
        indices = rng.choice(inverse.size, 3, replace=False)
        try:
            candidate = np.linalg.solve(design[indices], inverse[indices])
        except np.linalg.LinAlgError:
            continue
        inliers = (
            abs(inverse - design @ candidate) / scale <= settings.residual_limit / 2
        )
        n = int(inliers.sum())
        if n <= count or n < 0.8 * inverse.size:
            continue
        if any(inliers[bands == band].mean() < 0.5 for band in range(4)):
            continue
        best, count = inliers, n
    if best is None:
        return None
    coefficients = np.linalg.lstsq(design[best], inverse[best], rcond=None)[0]
    final = abs(inverse - design @ coefficients) / scale <= settings.residual_limit
    if final.mean() < 0.8 or any(
        final[bands == band].mean() < 0.5 for band in range(4)
    ):
        return None
    return coefficients
