"""One region/time evaluator for current captures, independent of actuation."""

from __future__ import annotations

from .navigation_maps import assess_evidence, public_spatial, resolve_semantic_evidence
from .navigation_profiles import parameters


def evaluate(result, frame, observer, *, context, captured_s, source=None):
    raw_state, raw_reason = result["state"], result["reason"]
    maps = result.pop("_navigation_maps", None)
    params = parameters(frame.behavior.model_dump(), frame.perception.model_dump())
    spatial = {"valid": False, "reason": raw_reason, "guard_clear": False}
    gray = None
    persistence = None
    objects = None
    try:
        if maps is not None:
            options = {
                "context": context,
                "sequence": source["sequence"]
                if source is not None
                else frame.sequence,
                "captured_s": captured_s,
                "params": params,
            }
            if params.get("object_tracking_enabled", False):
                diagnostic_maps = result.pop("_object_maps", maps)
                try:
                    objects = observer.observe_objects(
                        diagnostic_maps, frame.corridor, **options
                    )
                except (ImportError, ValueError, KeyError, TypeError):
                    objects = {
                        "diagnostic_only": True,
                        "state": "unavailable",
                        "objects": [],
                    }
            if params.get("obstacle_support_frames", 1) > 1:
                maps = resolve_semantic_evidence(maps, params)
                maps, persistence = observer.prepare_maps(
                    maps,
                    frame.corridor,
                    frame_id=source["frame_id"]
                    if source is not None
                    else frame.frame_id,
                    **options,
                )
                maps = {
                    key: value
                    for key, value in maps.items()
                    if key not in {"labels", "confidence"}
                }
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
        if objects is not None:
            from .object_diagnostics import render_objects

            result["_preview"] = render_objects(result["_preview"], objects)
    temporal = {**temporal, "version": 1, "spatial": public_spatial(spatial)}
    if persistence is not None:
        temporal["persistence"] = persistence
    if objects is not None:
        temporal["objects"] = objects
    result.pop("_object_maps", None)
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
