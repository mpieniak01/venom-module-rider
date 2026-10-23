"""Fresh-frame hysteresis over spatial evidence; never extends a source TTL."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from json import dumps
from math import isfinite
from typing import Any

from .optical_motion import OpticalMotion


@dataclass(frozen=True)
class _Point:
    captured_s: float
    candidate: str
    region_link: bool


def _parameters(params: Mapping[str, Any]) -> dict[str, float]:
    names = (
        "enter_frames",
        "enter_ms",
        "release_frames",
        "release_ms",
        "history_ms",
        "max_gap_ms",
        "max_uncertain_ms",
        "min_tracking_ratio",
        "slow_speed_factor",
    )
    values = {}
    for name in names:
        value = params.get(name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
        ):
            raise ValueError("navigation_temporal_params")
        values[name] = float(value)
    for name in ("enter_frames", "release_frames"):
        if not 1 <= values[name] <= 64 or not values[name].is_integer():
            raise ValueError("navigation_temporal_params")
    for name in ("enter_ms", "release_ms", "max_uncertain_ms"):
        if not 0 <= values[name] <= values["history_ms"]:
            raise ValueError("navigation_temporal_params")
    if not (
        0 < values["history_ms"] <= 10000
        and 0 < values["max_gap_ms"] <= values["history_ms"]
        and 0 < values["min_tracking_ratio"] <= 1
        and 0 < values["slow_speed_factor"] <= 1
    ):
        raise ValueError("navigation_temporal_params")
    return values


def _region_link(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    transform: Any,
) -> tuple[bool, float]:
    """Associate regions in image space; growth is diagnostic, not distance."""
    import numpy as np

    growth = 1.0
    linked = False
    for old in previous[:64]:
        x0, y0, x1, y1 = (float(value) for value in old["bbox"])
        old_area = max(1.0, (x1 - x0) * (y1 - y0))
        shared_area_scale = 1.0
        if transform is not None:
            corners = np.array([[x0, y0], [x1, y0], [x0, y1], [x1, y1]])
            projected = corners @ transform[:, :2].T + transform[:, 2]
            x0, y0 = projected.min(axis=0)
            x1, y1 = projected.max(axis=0)
            shared_area_scale = max(0.01, float(np.linalg.det(transform[:, :2])))
        for new in current[:64]:
            nx0, ny0, nx1, ny1 = (float(value) for value in new["bbox"])
            intersection = max(0.0, min(x1, nx1) - max(x0, nx0)) * max(
                0.0, min(y1, ny1) - max(y0, ny0)
            )
            new_area = max(1.0, (nx1 - nx0) * (ny1 - ny0))
            union = max(1.0, (x1 - x0) * (y1 - y0) + new_area - intersection)
            if intersection / union >= 0.1:
                linked = True
                growth = max(growth, (new_area / old_area / shared_area_scale) ** 0.5)
    return linked, growth


class TemporalEvidence:
    """A bounded observer; GO/SLOW remain subject to the caller's fresh lease."""

    def __init__(self) -> None:
        self._motion = OpticalMotion()
        self.reset()

    def reset(self) -> None:
        self._points: deque[_Point] = deque(maxlen=64)
        self._context: str | None = None
        self._last_sequence: int | None = None
        self._last_frame: str | None = None
        self._seen_frames: deque[str] = deque(maxlen=64)
        self._last_s: float | None = None
        self._had_clear = False
        self._degraded_s: float | None = None
        self._regions: list[dict[str, Any]] = []
        self._motion.reset()

    def _result(
        self,
        action: str,
        reason: str,
        values: dict[str, float],
        observation: Mapping[str, Any],
        tracking: Mapping[str, Any],
        *,
        state: str = "unknown",
        growth: float = 1.0,
    ) -> dict[str, Any]:
        speed = 1.0 if action == "go" else values["slow_speed_factor"]
        if action == "stop":
            speed = 0.0
        span = (
            (self._points[-1].captured_s - self._points[0].captured_s) * 1000
            if len(self._points) > 1
            else 0.0
        )
        return {
            "action": action,
            "state": state,
            "risk": (
                "clear"
                if action == "go"
                else "caution"
                if action == "slow"
                else "blocked"
                if state == "obstacle"
                else "unknown"
            ),
            "reason": reason,
            "speed_factor": speed,
            "evidence_frames": len(self._points),
            "history_ms": max(0.0, span),
            "near_guard_clear": observation.get("guard_clear") is True,
            "counts": {
                "clear": sum(point.candidate == "clear" for point in self._points),
                "uncertain": sum(
                    point.candidate == "uncertain" for point in self._points
                ),
                "obstacle": sum(
                    point.candidate == "obstacle" for point in self._points
                ),
            },
            "tracking": {
                key: value for key, value in tracking.items() if not key.startswith("_")
            },
            "region_growth": growth,
            "distance_measurement": False,
        }

    def _streak(self, candidate: str) -> tuple[int, float]:
        matching = []
        for point in reversed(self._points):
            if point.candidate != candidate:
                break
            matching.append(point)
            if candidate == "obstacle" and not point.region_link:
                break
        if not matching:
            return 0, 0.0
        return (
            len(matching),
            (matching[0].captured_s - matching[-1].captured_s) * 1000,
        )

    def update(
        self,
        observation: Mapping[str, Any],
        gray: Any = None,
        *,
        context: Mapping[str, Any],
        sequence: int,
        frame_id: str,
        captured_s: float,
        params: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Consume one distinct model observation, not a reprojected stale mask.

        context must bind source/session/epoch, model and effective policy
        revision. Sequence may skip unprocessed camera frames; explicit
        source_gap or max_gap_ms resets continuity. No arrival clock or model
        anchor is renewed here.
        """
        values = _parameters(params)
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 0
            or not isinstance(frame_id, str)
            or not frame_id
            or not isinstance(captured_s, (int, float))
            or isinstance(captured_s, bool)
            or not isfinite(captured_s)
            or not isinstance(context, Mapping)
            or not context
        ):
            raise ValueError("navigation_frame_identity")
        identity = dumps(dict(context), sort_keys=True, allow_nan=False)
        reset_reason = None
        if self._context is not None and identity != self._context:
            reset_reason = "navigation_context_changed"
        elif self._last_sequence is not None and (
            sequence <= self._last_sequence or frame_id in self._seen_frames
        ):
            reset_reason = "navigation_duplicate_frame"
        elif self._last_s is not None and captured_s <= self._last_s:
            reset_reason = "navigation_time_discontinuity"
        elif self._last_s is not None and (
            (captured_s - self._last_s) * 1000 > values["max_gap_ms"]
        ):
            reset_reason = "navigation_frame_gap"
        elif observation.get("source_gap") is True:
            reset_reason = "navigation_frame_gap"
        if reset_reason is not None:
            seen_frames = list(self._seen_frames)
            last_sequence, last_frame, last_s = (
                self._last_sequence,
                self._last_frame,
                self._last_s,
            )
            self.reset()
            self._context = identity
            if reset_reason in {
                "navigation_duplicate_frame",
                "navigation_time_discontinuity",
            }:
                self._seen_frames.extend(seen_frames)
                self._last_sequence, self._last_frame, self._last_s = (
                    last_sequence,
                    last_frame,
                    last_s,
                )
            else:
                self._seen_frames.append(frame_id)
                self._last_sequence, self._last_frame, self._last_s = (
                    sequence,
                    frame_id,
                    captured_s,
                )
            return self._result(
                "stop", reset_reason, values, observation, {"status": "reset"}
            )
        self._context = identity
        self._seen_frames.append(frame_id)
        self._last_sequence, self._last_frame, self._last_s = (
            sequence,
            frame_id,
            captured_s,
        )
        candidate = observation.get("candidate")
        if observation.get("valid") is not True or candidate not in {
            "clear",
            "uncertain",
            "obstacle",
        }:
            self._points.clear()
            self._had_clear = False
            self._degraded_s = None
            self._regions = []
            self._motion.reset()
            return self._result(
                "stop",
                str(observation.get("reason", "navigation_invalid_evidence")),
                values,
                observation,
                {"status": "disabled" if gray is None else "reset"},
            )
        tracking = (
            self._motion.update(gray, values["min_tracking_ratio"])
            if gray is not None
            else {"status": "disabled", "mode": "temporal_only"}
        )
        if gray is None:
            self._motion.reset()
        if tracking["status"] == "lost":
            self._points.clear()
            self._had_clear = False
            self._degraded_s = None
            self._regions = []
            self._motion.reset()
            return self._result(
                "stop", "navigation_tracking_lost", values, observation, tracking
            )
        if candidate == "clear" and observation.get("guard_clear") is not True:
            candidate = "uncertain"
        regions = list(observation.get("regions", []))
        linked, growth = _region_link(
            self._regions, regions, tracking.get("_transform")
        )
        self._regions = regions[:64]
        self._points.append(_Point(captured_s, str(candidate), linked))
        while self._points and (
            (captured_s - self._points[0].captured_s) * 1000 > values["history_ms"]
        ):
            self._points.popleft()
        if observation.get("hard_stop") is True:
            self._had_clear = False
            self._degraded_s = None
            return self._result(
                "stop",
                str(observation.get("reason", "navigation_near_obstacle")),
                values,
                observation,
                tracking,
                state="obstacle",
                growth=growth,
            )
        guard_clear = observation.get("guard_clear") is True
        if candidate == "clear" and guard_clear:
            frames, duration = self._streak("clear")
            if frames >= values["release_frames"] and duration >= values["release_ms"]:
                self._had_clear = True
                self._degraded_s = None
                return self._result(
                    "go",
                    "navigation_stable_clear",
                    values,
                    observation,
                    tracking,
                    state="clear",
                    growth=growth,
                )
            reason = "navigation_clear_confirmation"
        elif candidate == "obstacle":
            frames, duration = self._streak("obstacle")
            if frames >= values["enter_frames"] and duration >= values["enter_ms"]:
                self._had_clear = False
                self._degraded_s = None
                return self._result(
                    "stop",
                    "navigation_obstacle_confirmed",
                    values,
                    observation,
                    tracking,
                    state="obstacle",
                    growth=growth,
                )
            reason = "navigation_obstacle_confirmation"
        else:
            reason = "navigation_local_uncertainty"
        if self._degraded_s is None:
            self._degraded_s = captured_s
        elapsed = (captured_s - self._degraded_s) * 1000
        if (
            self._had_clear
            and guard_clear
            and values["max_uncertain_ms"] > 0
            and elapsed < values["max_uncertain_ms"]
        ):
            return self._result(
                "slow", reason, values, observation, tracking, growth=growth
            )
        self._had_clear = False
        return self._result(
            "stop", reason, values, observation, tracking, growth=growth
        )
