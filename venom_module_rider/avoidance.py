"""Bounded directional planner; Pi alone enforces actuation and consent."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AvoidanceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    enabled: bool = False
    sweep_verified: bool = False
    mirrored: bool = False
    turn_speed: float = Field(default=0.05, ge=0.01, le=0.1)
    turn_ms: int = Field(default=100, ge=50, le=150)
    max_turn_pulses: int = Field(default=3, ge=1, le=5)


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
    for side in ("left", "right"):
        row = sectors.get(side, {})
        counts[side] = (
            counts.get(side, 0) + 1
            if row.get("state") == "clear" and row.get("coverage_complete")
            else 0
        )
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
        if result.get("farther_outlier_pixels", 0):
            reason = "avoidance_recess"
        elif state["turns"] >= settings.max_turn_pulses:
            reason = "avoidance_turn_limit"
        else:
            choices = [s for s in ("left", "right") if counts[s] >= 3]
            chosen = state.get("side")
            if chosen and chosen not in choices:
                reason = "avoidance_selected_side_blocked"
            elif choices:
                chosen = chosen or min(
                    choices, key=lambda s: sectors[s]["outlier_fraction"]
                )
                state["side"] = chosen
                if state.get("paused"):
                    motion = "turn_" + chosen
                    state["turns"] += 1
                    counts.update(left=0, right=0)
                    reason = "avoidance_turn"
                else:
                    reason = "avoidance_stop_before_turn"
            else:
                reason = "avoidance_no_clear_side"
        state["paused"] = True
    else:
        state["front"] = 0
        state["paused"] = True
        reason = "avoidance_uncertain"
    return motion, reason, state, sectors


MotionPhase = Literal["ready", "turn_settling"]
