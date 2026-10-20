"""Pi-owned maneuver epochs: never refresh a correction budget from one frame."""

from .errors import RiderError


def planner_history(frame, previous):
    prior = dict(previous.get("avoidance") or {}) if previous else {}
    if frame.journey is None:
        return prior
    epoch = frame.journey.maneuver_epoch
    if previous is None:
        if epoch != 0:
            raise RiderError("vision_journey_epoch_changed", 409)
        return prior
    if previous.get("journey_terminal"):
        raise RiderError("vision_journey_ended", 409)
    old_epoch = previous["journey_epoch"]
    if epoch == old_epoch:
        return prior
    if epoch != old_epoch + 1 or not previous.get("journey_completed"):
        raise RiderError("vision_journey_epoch_changed", 409)
    # Pi accepted our qualified straightening decision after a correction.
    # Pose/height changes or a single clear sample cannot replenish this budget.
    for key in ("side", "pending_progress", "pending_reference"):
        prior.pop(key, None)
    prior.update(
        turns=0,
        stalled=0,
        counts={"left": 0, "right": 0},
        front=0,
        progress_delta=None,
    )
    return prior


def record_result(state, frame, action, result):
    if frame.journey is None:
        return
    planner = state.get("avoidance", {})
    state["journey_epoch"] = frame.journey.maneuver_epoch
    state["journey_terminal"] = result.get("terminal_stop") is True
    state["journey_completed"] = (
        action == "go"
        and result["state"] == "clear"
        and frame.motion_phase == "ready"
        and planner.get("front", 0) >= 3
        and planner.get("turns", 0) > 0
    )
