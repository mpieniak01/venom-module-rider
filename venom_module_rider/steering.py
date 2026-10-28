"""Protocol v3: ground-relative gaps, pose-aware progress and bounded corrections."""

from .ground_fit import pose_change


def plan(result, previous, settings, phase, eligible):
    """Advance bounded side history and select a qualified correction."""
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
    reference = result.get("ground_reference")
    _update_reference(state, reference, result, settings, phase)
    counts = _advance_side_counts(state, sectors, paths)
    action, reason, terminal = "stop", "avoidance_uncertain", False
    if not eligible or not settings.sweep_verified:
        reason = "avoidance_not_accepted"
    elif phase != "ready":
        counts.update(left=0, right=0)
        state["front"] = 0
        reason = "avoidance_capture_pending"
    elif (
        state["turns"] >= settings.max_turn_pulses
        or state.get("stalled", 0) >= settings.max_stalled_corrections
    ) and result.get("state") != "clear":
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
        action, reason = _obstacle_action(result, state, counts, paths, reference)
    else:
        state["front"] = 0
    return action, reason, state, sectors, paths, terminal


def _update_reference(state, reference, result, settings, phase):
    """Compare the same ground reference before updating clear-side counters."""
    if reference and phase == "ready":
        if pose_change(state.get("geometry_reference"), reference):
            state.update(
                counts={"left": 0, "right": 0},
                front=0,
                pending_progress=None,
                geometry_reference=reference,
                pose_resets=state.get("pose_resets", 0) + 1,
            )
        score = result.get("forward_clear_fraction")
        baseline = state.pop("pending_progress", None)
        pending_reference = state.pop("pending_reference", None)
        comparable = pending_reference is not None and not pose_change(
            pending_reference, reference, angle_limit=1.0, scale_limit=0.05
        )
        if score is not None and baseline is not None and comparable:
            delta = score - baseline
            state["progress_delta"] = delta
            state["stalled"] = (
                0
                if delta >= settings.progress_min_delta
                else state.get("stalled", 0) + 1
            )


def _obstacle_action(result, state, counts, paths, reference):
    """Keep the original guard and chosen-side priority for an obstacle."""
    guard = paths.get("guard", {})
    if result.get("farther_outlier_pixels", 0):
        return "stop", "avoidance_recess"
    if guard.get("state") != "clear" or guard.get("coverage_complete") is not True:
        return "stop", "avoidance_near_guard"
    choices = [side for side in ("left", "right") if counts[side] >= 3]
    chosen = state.get("side")
    if chosen and chosen not in choices:
        return "stop", "avoidance_selected_side_blocked"
    if not choices:
        return "stop", "avoidance_no_clear_side"
    chosen = chosen or min(
        choices,
        key=lambda side: (
            paths[side]["outlier_fraction"]
            - paths[side].get("gap_width_deg", 0) / 300
            + abs(paths[side].get("heading_deg", 0)) / 180
        ),
    )
    state["side"] = chosen
    action, reason = "steer_" + chosen, "avoidance_steer"
    state["turns"] += 1
    if reference and result.get("forward_clear_fraction") is not None:
        state["pending_progress"] = result["forward_clear_fraction"]
        state["pending_reference"] = reference
    counts.update(left=0, right=0)
    return action, reason


def _advance_side_counts(state, sectors, paths):
    """Advance the same complete-clear sectors and paths before policy gating."""
    counts = dict(state.get("counts", {"left": 0, "right": 0}))
    state["counts"] = counts
    state.setdefault("turns", 0)
    for side in ("left", "right"):
        clear = all(
            row.get("state") == "clear" and row.get("coverage_complete") is True
            for row in (sectors.get(side, {}), paths.get(side, {}))
        )
        counts[side] = counts.get(side, 0) + 1 if clear else 0
    return counts
