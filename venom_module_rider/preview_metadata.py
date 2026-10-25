"""Small image-bound preview contract, independent of planner diagnostics."""

from __future__ import annotations

import json

from .errors import RiderError
from .vision_overlay import preview_sectors

# This is also enforced by the browser's readAnalysis. Full decision diagnostics
# belong to navigation/state, never to an image's repeatedly transferred header.
MAX_PREVIEW_ANALYSIS_CHARS = 4096
_FIELDS = (
    "state",
    "reason",
    "run_id",
    "frame_id",
    "sequence",
    "config_revision",
    "perception_engine",
    "model_id",
    "model_sha256",
    "artifact_sha256",
    "model_generation",
    "clear_count",
    "vision_action",
    "capture_key",
    "age_source",
    "observed_at",
    "preview_only",
    "sector_space",
    "floor_fraction",
    "confidence_fraction",
    "overlay_geometry",
)
_SOURCE_FIELDS = (
    "camera_session",
    "sequence",
    "transport_epoch",
    "pipeline_revision",
    "frame_id",
    "sha256",
)


def analysis_header(result):
    """Project only the visible image's identity and assessment, without mutation.

    Neither the source timestamp nor any movement deadline is changed. A preview
    cannot carry a motion permit; its visual assessment remains available for
    the existing explicit confirmation flow.
    """
    data = {
        "preview_contract_version": 1,
        **{key: result[key] for key in _FIELDS if key in result},
        "action": "stop",
        "sectors": preview_sectors(result),
    }
    if isinstance(result.get("source"), dict):
        data["source"] = {
            key: result["source"][key]
            for key in _SOURCE_FIELDS
            if key in result["source"]
        }
    try:
        encoded = json.dumps(data, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise RiderError("vision_preview_metadata_invalid", 503) from None
    if len(encoded) > MAX_PREVIEW_ANALYSIS_CHARS and "overlay_geometry" in data:
        # Drop only optional drawing detail. Identity and deadlines are immutable.
        geometry = dict(data["overlay_geometry"])
        for key in ("objects", "floor_boundary", "near", "guard_clear"):
            geometry.pop(key, None)
        data["overlay_geometry"] = geometry
        encoded = json.dumps(data, separators=(",", ":"), allow_nan=False)
    if len(encoded) > MAX_PREVIEW_ANALYSIS_CHARS:
        raise RiderError("vision_preview_metadata_invalid", 503)
    return encoded
