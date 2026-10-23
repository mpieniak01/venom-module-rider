"""Bounded sparse optical-flow diagnostics, without motion permission."""

from math import isfinite
from typing import Any


class OpticalMotion:
    """Track at most 160 image features and approximate shared camera motion."""

    def __init__(self) -> None:
        self.previous: Any = None
        self.established = False

    def reset(self) -> None:
        self.previous = None
        self.established = False

    def update(self, gray: Any, minimum_ratio: float) -> dict[str, Any]:
        import cv2
        import numpy as np

        if (
            not isinstance(gray, np.ndarray)
            or gray.ndim != 2
            or gray.dtype != np.uint8
            or min(gray.shape) < 8
            or gray.shape[0] > 720
            or gray.shape[1] > 1280
        ):
            self.reset()
            return {"status": "lost", "reason": "navigation_tracking_image"}
        previous = self.previous
        self.previous = gray.copy()
        if previous is None:
            return {"status": "initial", "points": 0, "retained_ratio": 0.0}
        if previous.shape != gray.shape:
            self.established = False
            return {"status": "lost", "reason": "navigation_tracking_shape"}
        points = cv2.goodFeaturesToTrack(
            previous, maxCorners=160, qualityLevel=0.01, minDistance=5, blockSize=5
        )
        if points is None or len(points) < 8:
            status = "lost" if self.established else "unavailable"
            self.established = False
            return {"status": status, "points": 0, "retained_ratio": 0.0}
        options: dict[str, Any] = {
            "winSize": (21, 21),
            "maxLevel": 3,
            "criteria": (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03),
        }
        forward, valid, _ = cv2.calcOpticalFlowPyrLK(
            previous, gray, points, points.copy(), **options
        )
        if forward is None or valid is None:
            self.established = False
            return {"status": "lost", "points": 0, "retained_ratio": 0.0}
        backward, back_valid, _ = cv2.calcOpticalFlowPyrLK(
            gray, previous, forward, forward.copy(), **options
        )
        if backward is None or back_valid is None:
            self.established = False
            return {"status": "lost", "points": 0, "retained_ratio": 0.0}
        keep = (
            valid.ravel().astype(bool)
            & back_valid.ravel().astype(bool)
            & np.isfinite(forward).all(axis=(1, 2))
            & np.isfinite(backward).all(axis=(1, 2))
            & (np.linalg.norm(backward - points, axis=2).ravel() <= 1.5)
        )
        ratio = float(keep.mean())
        start = points[keep].reshape(-1, 2)
        end = forward[keep].reshape(-1, 2)
        result: dict[str, Any] = {
            "status": "lost",
            "points": int(len(points)),
            "retained_points": int(keep.sum()),
            "retained_ratio": ratio,
        }
        if keep.sum() < 8 or ratio < minimum_ratio:
            self.established = False
            return result
        transform, inliers = cv2.estimateAffinePartial2D(
            start, end, method=cv2.RANSAC, ransacReprojThreshold=2.5
        )
        if transform is None or inliers is None:
            self.established = False
            return result
        scale = float(np.linalg.norm(transform[:, 0]))
        inlier_ratio = float(inliers.mean())
        if not isfinite(scale) or not 0.5 <= scale <= 2.0 or inlier_ratio < 0.4:
            self.established = False
            return result
        predicted = start @ transform[:, :2].T + transform[:, 2]
        result.update(
            status="ok",
            shared_scale=scale,
            inlier_ratio=inlier_ratio,
            median_motion_px=float(np.median(np.linalg.norm(end - start, axis=1))),
            residual_motion_px=float(
                np.median(np.linalg.norm(end - predicted, axis=1))
            ),
            _transform=transform,
        )
        self.established = True
        return result
