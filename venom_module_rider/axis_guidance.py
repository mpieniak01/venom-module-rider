"""Closed-loop image guidance; the Pi remains the actuator authority."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class AxisGuidance(BaseModel):
    """Explicit guidance mode and optional swept rotation bounds."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    version: Annotated[StrictInt, Field(ge=1, le=1)]
    motion_mode: Literal["rotate_then_drive", "steer", "auto"]
    center_deadband: float = Field(ge=0, le=0.25)
    rotation_bounds: list[float] | None = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def valid_bounds(self):
        """Reject incomplete or inverted rotation rectangles."""
        if self.rotation_bounds is not None:
            a, b, c, d = self.rotation_bounds
            if not (0 <= a < c <= 1 and 0 <= b < d <= 1):
                raise ValueError("navigation_rotation_geometry_invalid")
        return self

    def validate_corridor(self, corridor):
        """Require the rotation footprint to cover the forward corridor."""
        if self.rotation_bounds is not None:
            a, b, c, d = self.rotation_bounds
            x0, _, x1, y1 = corridor
            if not (a <= x0 and c >= x1 and b < y1 <= d):
                raise ValueError("navigation_rotation_geometry_incomplete")


def axis_evidence(errors, mask, limit, corridor, deadband):
    """Obstacle location in the forward body corridor, never unrelated frame edges."""
    import numpy as np

    h, w = mask.shape
    axis = (corridor[0] + corridor[2]) / 2
    ys, xs = np.nonzero(mask & np.isfinite(errors) & (errors > limit))
    half = (corridor[2] - corridor[0]) / 2
    offset = float(np.mean((xs / w - axis) / half)) if xs.size else 0.0
    side = "left" if offset < -deadband else "right" if offset > deadband else "center"
    return {
        "version": 1,
        "axis_x": axis,
        "offset": offset,
        "obstacle_side": side if xs.size else "none",
        "obstacle_pixels": int(xs.size),
        "corridor_pixels": int(mask.sum()),
        "intrusion": float(xs.size / mask.sum()),
        "exit_left": float(
            (xs.max() + 1 - corridor[0] * w) / ((corridor[2] - corridor[0]) * w)
        )
        if xs.size
        else 0.0,
        "exit_right": float(
            (corridor[2] * w - xs.min()) / ((corridor[2] - corridor[0]) * w)
        )
        if xs.size
        else 0.0,
        "bounds": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
        if xs.size
        else None,
    }


def _clear(paths, key):
    row = paths.get(key, {})
    return row.get("state") == "clear" and row.get("coverage_complete") is True


def _advance(state, paths, result):
    state["counts"] = {
        side: min(64, state.get("counts", {}).get(side, 0) + 1)
        if _clear(paths, side)
        else 0
        for side in ("left", "right")
    }
    state["rotation_count"] = (
        min(64, state.get("rotation_count", 0) + 1) if _clear(paths, "rotation") else 0
    )
    state["obstacle_count"] = (
        min(64, state.get("obstacle_count", 0) + 1)
        if result.get("state") == "obstacle"
        else 0
    )


def reset_history(state):
    """Clear transient guidance state after an incompatible observation."""
    state.update(
        counts={"left": 0, "right": 0},
        front=0,
        rotation_count=0,
        obstacle_count=0,
        rotation_zero=False,
    )


def _remaining(result, side, settings):
    image_side = ("right" if side == "left" else "left") if settings.mirrored else side
    # Turning right moves the obstacle toward the left edge of the forward corridor.
    return result.get("axis_evidence", {}).get(
        "exit_left" if image_side == "right" else "exit_right"
    )


def _progress(state, result, settings):
    previous = state.pop("pending_exit", None)
    current = _remaining(result, state.get("side"), settings)
    if previous is not None and current is not None:
        delta = (previous - current) / max(previous, 1e-9)
        state["progress_delta"] = delta
        state["stalled"] = (
            0
            if delta > 0 and delta >= settings.progress_min_delta
            else state.get("stalled", 0) + 1
        )


def _kind(side, paths, state, mode):
    if (
        mode != "rotate_then_drive"
        and state["counts"][side] >= 3
        and _clear(paths, side)
    ):
        return "steer"
    if mode != "steer" and state["rotation_count"] >= 3 and _clear(paths, "rotation"):
        return "turn"
    return None


def _choose(result, state, paths, settings, mode):
    choices = {
        side: kind
        for side in ("left", "right")
        if (kind := _kind(side, paths, state, mode))
    }
    chosen = state.get("side")
    if chosen:
        return (
            (chosen, choices[chosen])
            if chosen in choices
            else (None, "avoidance_selected_side_blocked")
        )
    if not choices:
        return None, "avoidance_no_clear_maneuver"
    obstacle = result.get("axis_evidence", {}).get("obstacle_side", "center")
    preferred = {"left": "right", "right": "left"}.get(obstacle)
    if settings.mirrored and preferred:
        preferred = "right" if preferred == "left" else "left"
    collision = settings.geometry.collision
    # The obstacle's side is a preference among physically qualified motions.
    chosen = min(
        choices,
        key=lambda side: (
            0 if side == preferred else 1,
            paths.get(side, {}).get("outlier_fraction", 1),
            getattr(
                collision,
                ("right" if side == "left" else "left") + "_shift"
                if settings.mirrored
                else side + "_shift",
            ),
        ),
    )
    return chosen, choices[chosen]


def _obstacle(result, state, paths, settings, guidance):
    if result.get("farther_outlier_pixels", 0):
        return "stop", "avoidance_recess", False
    if guidance and state["obstacle_count"] < 3:
        return "stop", "avoidance_obstacle_confirmation", False
    if guidance:
        _progress(state, result, settings)
    if (
        state["turns"] >= settings.max_turn_pulses
        or state.get("stalled", 0) >= settings.max_stalled_corrections
    ):
        return "stop", "avoidance_no_progress", True
    mode = guidance.motion_mode if guidance else "steer"
    chosen, kind = _choose(result, state, paths, settings, mode)
    if chosen is None:
        return "stop", kind, False
    state["side"] = chosen
    if kind == "turn" and not state.get("rotation_zero"):
        state["rotation_zero"] = True
        return "stop", "avoidance_stop_before_rotation", False
    state["turns"] += 1
    reset_history(state)
    if guidance:
        state["pending_exit"] = _remaining(result, chosen, settings)
    state["maneuver_kind"] = kind
    return (
        kind + "_" + chosen,
        "avoidance_rotate" if kind == "turn" else "avoidance_steer",
        False,
    )


def plan_collision(result, previous, settings, phase, eligible, corridor):
    """Plan bounded motion from collision and axis evidence."""
    from .collision_geometry import proof_matches

    state = dict(previous or {})
    state.setdefault("turns", 0)
    paths, sectors = result.get("paths", {}), result.get("sectors", {})
    if settings.mirrored:
        paths = {
            **paths,
            "left": paths.get("right", {}),
            "right": paths.get("left", {}),
        }
        sectors = {
            **sectors,
            "left": sectors.get("right", {}),
            "right": sectors.get("left", {}),
        }
    collision = settings.geometry.collision
    _advance(state, paths, result)
    action, reason, terminal = "stop", "avoidance_uncertain", False
    if not proof_matches(result, collision, corridor):
        reset_history(state)
        reason = "collision_geometry_mismatch"
    elif not eligible or not settings.sweep_verified:
        reset_history(state)
        reason = "avoidance_not_accepted"
    elif not _clear(paths, "guard"):
        reset_history(state)
        reason = "avoidance_near_guard"
    elif phase != "ready":
        reset_history(state)
        reason = "avoidance_capture_pending"
    elif result.get("state") == "clear" and _clear(paths, "center"):
        state["front"] = state.get("front", 0) + 1
        state["rotation_zero"] = False
        reason = "clear_frames_pending"
        if state["front"] >= 3:
            action, reason = "go", "clear_frames_ready"
            state.pop("side", None)
            state.pop("pending_exit", None)
    elif result.get("state") == "obstacle":
        state["front"] = 0
        action, reason, terminal = _obstacle(
            result, state, paths, settings, collision.guidance
        )
    else:
        if collision.guidance is None:
            state["front"] = 0
        else:
            reset_history(state)
    return action, reason, state, sectors, paths, terminal
