"""Causal component support; rejected residuals become uncertain, never floor."""

import json
import math
from collections import deque
from typing import Any

from .optical_motion import OpticalMotion
from .spatial_evidence import corridor_mask


class ObstaclePersistence:
    def __init__(self):
        self.motion = OpticalMotion()
        self.reset()

    def reset(self):
        self.motion.reset()
        self.history: deque[tuple[Any, Any, float]] = deque(maxlen=4)
        self.identity = None
        self.sequence = None
        self.frame_id = None
        self.captured_s = None

    def apply(self, maps, corridor, *, context, sequence, frame_id, captured_s, params):
        """Retain causal obstacle proofs while rejected residuals stay uncertain."""
        import numpy as np

        required = params.get("obstacle_support_frames", 1)
        if required <= 1:
            self.reset()
            return maps, {"enabled": False}
        identity = json.dumps(dict(context), sort_keys=True, allow_nan=False)
        invalid = _persistence_context_invalid(
            self, maps, identity, sequence, frame_id, captured_s, params
        )
        if invalid:
            self.reset()
            return maps, {"enabled": True, "state": "reset"}
        obstacle = maps["obstacle"]
        if obstacle.ndim != 2 or obstacle.dtype != np.bool_:
            raise ValueError("navigation_maps")
        corridor_mask(maps.get("corridor", corridor), obstacle.shape)
        visible_now = maps["floor"] | obstacle
        gray = maps.get("gray")
        if gray is None:
            self.reset()
            return maps, {"enabled": True, "state": "tracking_unavailable"}
        tracking = self.motion.update(gray, params["min_tracking_ratio"])
        transform = tracking.get("_transform")
        if transform is None and tracking["status"] not in {"initial"}:
            self.reset()
            return maps, {"enabled": True, "state": "tracking_unavailable"}
        h, w = obstacle.shape
        _project_persistence_history(self, transform, w, h)
        window = _trim_persistence_history(self, captured_s, params)
        confirmed, uncertain = _persistent_components(self, obstacle, params, required)
        self.history.append((obstacle.copy(), visible_now.copy(), captured_s))
        while len(self.history) > window - 1:
            self.history.popleft()
        self.identity, self.sequence, self.frame_id, self.captured_s = (
            identity,
            sequence,
            frame_id,
            captured_s,
        )
        return {
            **maps,
            "obstacle": confirmed,
            "uncertain": maps["uncertain"] | uncertain,
        }, {
            "enabled": True,
            "state": "current",
            "support_frames": required,
            "window_frames": window,
            "raw_pixels": int(obstacle.sum()),
            "confirmed_pixels": int(confirmed.sum()),
            "uncertain_pixels": int(uncertain.sum()),
            "suppression_is_floor_evidence": False,
        }


def _persistence_context_invalid(
    self, maps, identity, sequence, frame_id, captured_s, params
):
    """Preserve map, context, sequence, duplicate and capture-gap rejection order."""
    return (
        maps.get("valid", True) is not True
        or not math.isfinite(captured_s)
        or (self.identity is not None and identity != self.identity)
        or (self.sequence is not None and sequence <= self.sequence)
        or (self.frame_id is not None and frame_id == self.frame_id)
        or (
            self.captured_s is not None
            and (
                captured_s <= self.captured_s
                or (captured_s - self.captured_s) * 1000 > params["max_gap_ms"]
            )
        )
    )


def _project_persistence_history(self, transform, w, h):
    """Warp old masks with identical nearest-neighbor geometry."""
    import cv2
    import numpy as np

    if transform is not None:
        self.history = deque(
            (
                (
                    cv2.warpAffine(
                        old.astype(np.uint8),
                        transform,
                        (w, h),
                        flags=cv2.INTER_NEAREST,
                    ).astype(bool),
                    cv2.warpAffine(
                        visible.astype(np.uint8),
                        transform,
                        (w, h),
                        flags=cv2.INTER_NEAREST,
                    ).astype(bool),
                    observed_s,
                )
                for old, visible, observed_s in self.history
            ),
            maxlen=4,
        )


def _trim_persistence_history(self, captured_s, params):
    """Drop expired and over-window proofs before computing current support."""
    while (
        self.history and (captured_s - self.history[0][2]) * 1000 > params["history_ms"]
    ):
        self.history.popleft()
    window = int(params["obstacle_window_frames"])
    while len(self.history) >= window:
        self.history.popleft()
    return window


def _persistent_components(self, obstacle, params, required):
    """Score components against prior obstacle overlap and visible coverage."""
    import cv2
    import numpy as np

    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        obstacle.astype(np.uint8), connectivity=8
    )
    areas = stats[:, cv2.CC_STAT_AREA]
    support = np.ones(count, dtype=np.int32)
    radius = int(params["obstacle_match_tolerance_px"])
    kernel = np.ones((2 * radius + 1, 2 * radius + 1), np.uint8)
    for old, visible, _observed_s in self.history:
        expanded = cv2.dilate(old.astype(np.uint8), kernel).astype(bool)
        overlap = np.bincount(labels[expanded], minlength=count) / np.maximum(1, areas)
        coverage = np.bincount(labels[visible], minlength=count) / np.maximum(1, areas)
        support += (overlap >= params["obstacle_match_overlap"]) & (
            coverage >= params["obstacle_min_previous_coverage"]
        )
    accepted = support >= required
    accepted[0] = False
    confirmed = obstacle & accepted[labels]
    uncertain = obstacle & ~confirmed
    return confirmed, uncertain
