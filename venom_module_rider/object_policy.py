"""Whole-object confirmation for the explicit known-room preset.

Only current model obstacle pixels can become new obstacle evidence. Retained
objects can hold STOP briefly, but never manufacture floor or extend capture TTL.
"""

import json
import math
from collections import deque

from .object_diagnostics import ObjectDiagnostics
from .spatial_evidence import corridor_mask


class RoomObjectPolicy:
    """Track complete proposals before testing their reliable route intersection."""

    def __init__(self):
        self.tracker = ObjectDiagnostics()
        self.reset()

    def reset(self):
        self.tracker.reset()
        self.history = {}
        self.tick = 0
        self.identity = None
        self.sequence = None
        self.frame_id = None
        self.captured_s = None

    def observe(
        self, maps, corridor, *, context, sequence, frame_id, captured_s, params
    ):
        """Track reliable whole-object support without extending frame authority."""
        import numpy as np

        identity = json.dumps(dict(context), sort_keys=True, allow_nan=False)
        invalid = _object_context_invalid(
            self, maps, sequence, frame_id, captured_s, params
        )
        changed = self.identity is not None and identity != self.identity
        if invalid or changed:
            self.reset()
            return self.unavailable("object_context_reset")
        self.identity, self.sequence, self.frame_id, self.captured_s = (
            identity,
            sequence,
            frame_id,
            captured_s,
        )
        raw = maps["obstacle"]
        if not isinstance(raw, np.ndarray) or raw.ndim != 2 or raw.dtype != np.bool_:
            raise ValueError("navigation_object_maps")
        route = corridor_mask(corridor, raw.shape)
        output = self.tracker.observe(
            maps,
            corridor,
            context=context,
            sequence=sequence,
            captured_s=captured_s,
            params=params,
        )
        motion = output.pop("_motion", {})
        if output.get("state") != "current" or motion.get("status") not in {
            "initial",
            "ok",
        }:
            self.reset()
            return self.unavailable("object_tracking_unavailable")
        self.tick += 1
        self._project(motion.get("_transform"), raw.shape)
        entries = []
        supported = np.zeros_like(raw)
        for item in output["objects"]:
            entries.append(
                _object_support(self, item, raw, route, captured_s, params, supported)
            )
        entries.extend(self._held(route, captured_s, params))
        active = set(self.tracker.tracks)
        self.history = {
            tid: history for tid, history in self.history.items() if tid in active
        }
        return {
            **output,
            "diagnostic_only": False,
            "objects": entries[:32],
            "_supported": supported,
            "policy": "known_room",
            "support_required": params["obstacle_support_frames"],
            "support_window": params["obstacle_window_frames"],
            "confirmed_pixels": int(supported.sum()),
        }

    def _project(self, transform, shape):
        if transform is None:
            return
        import cv2
        import numpy as np

        if transform.shape != (2, 3) or not np.isfinite(transform).all():
            raise ValueError("navigation_object_motion")
        scale = abs(float(np.linalg.det(transform[:, :2])))
        for history in self.history.values():
            for point in history["points"]:
                x0, y0, x1, y1 = point["bbox"]
                corners = np.array([[x0, y0], [x0, y1], [x1, y0], [x1, y1]])
                projected = corners @ transform[:, :2].T + transform[:, 2]
                point["bbox"] = [*projected.min(axis=0), *projected.max(axis=0)]
                point["area"] *= scale
            if "last_mask" in history:
                history["last_mask"] = cv2.warpAffine(
                    history["last_mask"].astype(np.uint8),
                    transform,
                    (shape[1], shape[0]),
                    flags=cv2.INTER_NEAREST,
                ).astype(bool)

    def _held(self, route, captured_s, params):
        result = []
        for history in self.history.values():
            if "last_mask" not in history:
                continue
            missing = self.tick - history["last_tick"]
            age = (captured_s - history["last_s"]) * 1000
            if (
                missing <= 0
                or missing > params["object_max_gap_frames"]
                or age > min(params["max_gap_ms"], params["history_ms"])
                or not (history["last_mask"] & route).any()
            ):
                continue
            y, x = history["last_mask"].nonzero()
            box = [int(x.min()), int(y.min()), int(x.max() + 1), int(y.max() + 1)]
            # A current low-confidence proposal cannot renew the last proof.
            result.append(
                {
                    **history["last_item"],
                    "bbox": box,
                    "confirmed": True,
                    "observed_now": False,
                    "age_ms": age,
                    "evidence": "held",
                    "approaching_candidate": False,
                    "_mask": history["last_mask"],
                }
            )
        return result

    @staticmethod
    def unavailable(reason):
        return {
            "diagnostic_only": False,
            "state": "unavailable",
            "reason": reason,
            "objects": [],
        }


def apply_object_evidence(original, filtered, diagnostics):
    """Add reliable supported pixels; preserve the original floor partition."""
    import numpy as np

    supported = diagnostics.pop("_supported", None)
    if diagnostics.get("state") != "current" or supported is None:
        return {**filtered, "valid": False}
    if supported.shape != original["obstacle"].shape or supported.dtype != np.bool_:
        raise ValueError("navigation_object_maps")
    confirmed = original["obstacle"] & supported
    return {
        **filtered,
        "obstacle": filtered["obstacle"] | confirmed,
        "uncertain": filtered["uncertain"] & ~confirmed,
    }


def assess_objects(spatial, maps, corridor, diagnostics, params):
    """Use a whole reliable object even when the scored intersection is small."""
    import numpy as np

    route = corridor_mask(maps.get("corridor", corridor), maps["obstacle"].shape)
    ys = np.flatnonzero(route.any(axis=1))
    near = route.copy()
    height = int(ys[-1] - ys[0] + 1)
    near[: int(ys[-1] + 1) - max(1, round(height * params["near_zone_fraction"]))] = (
        False
    )
    regions = []
    stop_reason = None
    for item in diagnostics.get("objects", []):
        region, stop_reason = _object_route_region(
            item, maps, spatial, route, near, stop_reason
        )
        if region is not None:
            regions.append(region)
    if spatial.get("valid") is not True or spatial.get("hard_stop") is True:
        return spatial
    if not regions:
        return spatial
    result = {
        **spatial,
        "candidate": "obstacle",
        "regions": [*spatial.get("regions", []), *regions][:64],
        "reason": stop_reason or "navigation_object_in_corridor",
    }
    if stop_reason:
        result.update(hard_stop=True, guard_clear=False)
    return result


def public_objects(diagnostics):
    """Never put private masks or optical transforms on JSON/error responses."""
    return {
        **{key: value for key, value in diagnostics.items() if not key.startswith("_")},
        "objects": [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in diagnostics.get("objects", [])[:32]
        ],
    }


def _object_context_invalid(self, maps, sequence, frame_id, captured_s, params):
    """Preserve source validity, sequence, frame and capture-gap reset guards."""
    return (
        maps.get("valid", True) is not True
        or not math.isfinite(captured_s)
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


def _object_support(self, item, raw, route, captured_s, params, supported):
    """Append one causal object proof before evaluating approach and route support."""
    import numpy as np

    tid = item["id"]
    mask = self.tracker.tracks[tid]["mask"]
    reliable = raw & mask
    history = self.history.setdefault(
        tid, {"points": deque(maxlen=5), "approach_streak": 0}
    )
    history["points"].append(
        {
            "tick": self.tick,
            "time": captured_s,
            "area": int(reliable.sum()),
            "bbox": list(item["bbox"]),
            "clipped": item["clipped"],
        }
    )
    support = [
        point
        for point in history["points"]
        if self.tick - point["tick"] < params["obstacle_window_frames"]
        and (captured_s - point["time"]) * 1000 <= params["history_ms"]
        and point["area"] >= params["object_min_area_px"]
    ]
    confirmed = (
        int(reliable.sum()) >= params["object_min_area_px"]
        and len(support) >= params["obstacle_support_frames"]
    )
    trend = list(history["points"])[-params["object_trend_frames"] :]
    complete = (
        len(trend) >= params["object_trend_frames"]
        and all(
            point["area"] >= params["object_min_area_px"] and not point["clipped"]
            for point in trend
        )
        and (captured_s - trend[0]["time"]) * 1000 <= params["history_ms"]
    )
    growth = int(reliable.sum()) / max(1, trend[0]["area"])
    lower = item["bbox"][3] - trend[0]["bbox"][3]
    trend_now = (
        confirmed
        and complete
        and growth >= params["object_growth_min"]
        and lower >= params["object_lower_min_px"]
    )
    history["approach_streak"] = history["approach_streak"] + 1 if trend_now else 0
    approaching = history["approach_streak"] >= max(
        2, params["obstacle_support_frames"]
    )
    qualified = confirmed and not item["clipped"]
    if qualified:
        supported |= reliable
    history.update(current_tick=self.tick, current_s=captured_s)
    return {
        **item,
        "reliable_area_pixels": int(reliable.sum()),
        "raw_route_pixels": int((reliable & route).sum()),
        "support_observations": len(support),
        "confirmed": bool(confirmed),
        "whole_object_qualified": bool(qualified),
        "approaching_candidate": bool(approaching),
        "growth": float(growth),
        "lower_px": float(lower),
        "observed_now": True,
        "age_ms": 0.0,
        "evidence": "approaching"
        if approaching
        else "confirmed"
        if confirmed
        else "transient",
        "_mask": reliable if confirmed else np.zeros_like(raw),
        "_history": history,
    }


def _object_route_region(item, maps, spatial, route, near, stop_reason):
    """Evaluate one whole-object route proof without renewing retained captures."""
    import numpy as np

    mask = item.pop("_mask", None)
    history = item.pop("_history", None)
    if mask is None:
        return None, stop_reason
    held = item["evidence"] == "held"
    evidence = _object_route_evidence(mask, route, held, maps, item, spatial)
    item["route_pixels"] = int(evidence.sum())
    item["intersects_route"] = bool(evidence.any())
    item["near"] = bool((evidence & near).any())
    if not item.get("confirmed") or not evidence.any():
        return None, stop_reason
    if held and not item["near"]:
        return None, stop_reason
    _remember_object_route(item, held, history, spatial, evidence)
    y, x = np.nonzero(evidence)
    region = {
        "bbox": [
            int(x.min()),
            int(y.min()),
            int(x.max() + 1),
            int(y.max() + 1),
        ],
        "area_pixels": int(evidence.sum()),
        "area_fraction": float(evidence.sum() / route.sum()),
        "near": item["near"],
        "thin": False,
        "object_id": item["id"],
        "whole_object": True,
    }
    if item["near"]:
        stop_reason = "navigation_object_held" if held else "navigation_object_near"
    elif item["approaching_candidate"]:
        stop_reason = stop_reason or "navigation_object_approaching"
    return region, stop_reason


def _remember_object_route(item, held, history, spatial, evidence):
    """Arm retention only from a current near-route obstacle that actually qualified."""
    if not held and history is not None and item["near"] and spatial.get("valid"):
        # Arm retention only from an obstacle that actually qualified in route.
        history.update(
            last_mask=evidence.copy(),
            last_tick=history["current_tick"],
            last_s=history["current_s"],
            last_item={
                key: value for key, value in item.items() if not key.startswith("_")
            },
        )


def _object_route_evidence(mask, route, held, maps, item, spatial):
    """Intersect proof with reliable masks while preserving held-object support."""
    import numpy as np

    evidence = mask & route
    if not held:
        evidence &= maps["obstacle"]
        if not item.get("whole_object_qualified"):
            # Clipped/merged borders cannot rescue a rejected tiny component.
            evidence &= spatial.get("_obstacle_mask", np.zeros_like(route))
    return evidence
