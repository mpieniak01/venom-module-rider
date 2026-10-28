"""Bounded directional planner; Pi alone enforces actuation and consent."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .ground_config import GroundGeometry
from .motion_limits import MAX_MOTION_MS, MAX_SPEED


class AvoidanceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    protocol_version: Literal[2, 3] = 3
    enabled: bool = False
    sweep_verified: bool = False
    mirrored: bool = False
    turn_speed: float = Field(default=0.05, ge=0.01, le=MAX_SPEED)
    turn_ms: int = Field(default=100, ge=50, le=MAX_MOTION_MS)
    max_turn_pulses: int = Field(default=3, ge=1, le=5)
    max_stalled_corrections: int = Field(default=2, ge=1, le=5)
    progress_min_delta: float = Field(default=0.03, ge=0.0, le=0.3)
    geometry: GroundGeometry = Field(
        default_factory=lambda: GroundGeometry(mode="image")
    )


def plan(result, previous, settings, phase, eligible):
    """Require a STOP cycle, stable side, and Pi-confirmed post-turn fresh input."""
    state = dict(previous or {})
    sectors = result.get("sectors", {})
    if settings.mirrored:
        sectors = {
            **sectors,
            "left": sectors.get("right", {}),
            "right": sectors.get("left", {}),
        }
    state.setdefault("turns", 0)
    counts = dict(state.get("counts", {"left": 0, "right": 0}))
    state["counts"] = counts
    _advance_side_counts(counts, sectors)
    motion = "stop"
    reason = "avoidance_waiting"
    if not eligible or not settings.sweep_verified:
        reason = "avoidance_not_accepted"
    elif phase != "ready":
        counts.update(left=0, right=0)
        state["front"] = 0
        reason = "avoidance_turn_settling"
    elif result.get("state") == "clear":
        state["front"] = state.get("front", 0) + 1
        state.pop("side", None)
        motion = "go" if state["front"] >= 3 else "stop"
        reason = "clear_frames_ready" if motion == "go" else "clear_frames_pending"
    elif result.get("state") == "obstacle":
        state["front"] = 0
        motion, reason = _obstacle_action(result, state, counts, sectors, settings)
        state["paused"] = True
    else:
        state["front"] = 0
        state["paused"] = True
        reason = "avoidance_uncertain"
    return motion, reason, state, sectors


MotionPhase = Literal["ready", "turn_settling", "steer_wait"]


def _obstacle_action(result, state, counts, sectors, settings):
    """Select the same bounded side only after an accepted obstacle observation."""
    if result.get("farther_outlier_pixels", 0):
        return "stop", "avoidance_recess"
    if state["turns"] >= settings.max_turn_pulses:
        return "stop", "avoidance_turn_limit"
    choices = [side for side in ("left", "right") if counts[side] >= 3]
    chosen = state.get("side")
    if chosen and chosen not in choices:
        return "stop", "avoidance_selected_side_blocked"
    if not choices:
        return "stop", "avoidance_no_clear_side"
    chosen = chosen or min(choices, key=lambda side: sectors[side]["outlier_fraction"])
    state["side"] = chosen
    if not state.get("paused"):
        return "stop", "avoidance_stop_before_turn"
    state["turns"] += 1
    counts.update(left=0, right=0)
    return "turn_" + chosen, "avoidance_turn"


def _advance_side_counts(counts, sectors):
    """Accumulate each original complete-clear side before eligibility is evaluated."""
    for side in ("left", "right"):
        row = sectors.get(side, {})
        counts[side] = (
            counts.get(side, 0) + 1
            if row.get("state") == "clear" and row.get("coverage_complete")
            else 0
        )
