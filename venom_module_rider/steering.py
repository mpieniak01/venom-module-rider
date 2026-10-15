"""Protocol v3: bounded forward/yaw corrections, never an unlimited search."""


def plan(result, previous, settings, phase, eligible):
    state = dict(previous or {})
    sectors = result.get("sectors", {})
    paths = result.get("paths", {})
    if settings.mirrored:
        sectors = {
            **sectors,
            "left": sectors.get("right", {}),
            "right": sectors.get("left", {}),
        }
        paths = {
            **paths,
            "left": paths.get("right", {}),
            "right": paths.get("left", {}),
        }
    counts = dict(state.get("counts", {"left": 0, "right": 0}))
    state["counts"] = counts
    state.setdefault("turns", 0)
    for side in ("left", "right"):
        clear = all(
            row.get("state") == "clear" and row.get("coverage_complete") is True
            for row in (sectors.get(side, {}), paths.get(side, {}))
        )
        counts[side] = counts.get(side, 0) + 1 if clear else 0
    action, reason, terminal = "stop", "avoidance_uncertain", False
    guard = paths.get("guard", {})
    if not eligible or not settings.sweep_verified:
        reason = "avoidance_not_accepted"
    elif phase != "ready":
        counts.update(left=0, right=0)
        state["front"] = 0
        reason = "avoidance_capture_pending"
    elif state["turns"] >= settings.max_turn_pulses and result.get("state") != "clear":
        reason, terminal = "avoidance_no_progress", True
    elif result.get("state") == "clear":
        state["front"] = state.get("front", 0) + 1
        if state["front"] >= 3:
            action, reason = "go", "clear_frames_ready"
            state.pop("side", None)
        else:
            reason = "clear_frames_pending"
    elif result.get("state") == "obstacle":
        state["front"] = 0
        if result.get("farther_outlier_pixels", 0):
            reason = "avoidance_recess"
        elif (
            guard.get("state") != "clear" or guard.get("coverage_complete") is not True
        ):
            reason = "avoidance_near_guard"
        else:
            choices = [side for side in ("left", "right") if counts[side] >= 3]
            chosen = state.get("side")
            if chosen and chosen not in choices:
                reason = "avoidance_selected_side_blocked"
            elif choices:
                chosen = chosen or min(
                    choices, key=lambda s: paths[s]["outlier_fraction"]
                )
                state["side"] = chosen
                action, reason = "steer_" + chosen, "avoidance_steer"
                state["turns"] += 1
                counts.update(left=0, right=0)
            else:
                reason = "avoidance_no_clear_side"
    else:
        state["front"] = 0
    return action, reason, state, sectors, paths, terminal
