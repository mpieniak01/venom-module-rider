"""One region/time evaluator for current captures, independent of actuation."""

from __future__ import annotations

from .navigation_maps import assess_evidence, public_spatial
from .navigation_profiles import parameters


def evaluate(result, frame, observer, *, context, captured_s, source=None):
    raw_state, raw_reason = result["state"], result["reason"]
    maps = result.pop("_navigation_maps", None)
    params = parameters(frame.behavior.model_dump())
    spatial = {"valid": False, "reason": raw_reason, "guard_clear": False}
    gray = None
    try:
        if maps is not None:
            spatial = assess_evidence(
                maps, frame.corridor, params, raw_reason=raw_reason
            )
            gray = maps.get("gray")
        temporal = observer.update(
            spatial,
            gray,
            context=context,
            sequence=source["sequence"] if source is not None else frame.sequence,
            frame_id=source["frame_id"] if source is not None else frame.frame_id,
            captured_s=captured_s,
            params=params,
        )
    except (ImportError, ValueError, KeyError):
        observer.reset()
        temporal = {
            "state": "unknown",
            "action": "stop",
            "risk": "unknown",
            "reason": "temporal_evidence_unavailable",
            "speed_factor": 0,
            "evidence_frames": 0,
            "near_guard_clear": False,
            "history_ms": 0,
            "tracking": {"status": "unavailable"},
        }
    if "_preview" in result:
        from .navigation_overlay import render_evidence

        result["_preview"] = render_evidence(result["_preview"], spatial)
    temporal = {**temporal, "version": 1, "spatial": public_spatial(spatial)}
    visible_state = temporal["state"]
    if (
        spatial.get("valid") is True
        and spatial.get("candidate") == "clear"
        and spatial.get("guard_clear") is True
        and temporal["reason"] == "navigation_clear_confirmation"
    ):
        # Both peers count the same fresh positive captures during startup.
        # Temporal waiting remains STOP; do not stack another three-frame gate.
        visible_state = "clear"
    result.update(
        raw_state=raw_state,
        raw_reason=raw_reason,
        temporal=temporal,
        state=visible_state,
        reason=temporal["reason"],
    )
    return temporal
