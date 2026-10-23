"""Compact source-space perception maps; private arrays never enter the wire."""

from __future__ import annotations

import numpy as np
from PIL import Image


def semantic_maps(logits, source_size):
    """Compute pmax in bounded workspace instead of another 150-channel tensor."""
    values = logits[0]
    maximum = values.max(axis=0)
    denominator = np.zeros_like(maximum, dtype=np.float32)
    for channel in values:
        denominator += np.exp(channel - maximum)
    confidence = 1.0 / denominator
    labels = values.argmax(axis=0).astype(np.uint8)
    labels = np.asarray(
        Image.fromarray(labels).resize(source_size, Image.Resampling.NEAREST)
    )
    confidence = np.asarray(
        Image.fromarray(confidence).resize(source_size, Image.Resampling.BILINEAR)
    )
    certain = confidence >= 0.8
    return {
        "floor": (labels == 3) & certain,
        "obstacle": (labels != 3) & certain,
        "uncertain": ~certain,
        "labels": labels,
        "confidence": confidence.astype(np.float32),
        "valid": True,
    }


def depth_maps(errors, signed, limit, *, valid):
    finite = np.isfinite(errors)
    return {
        "floor": finite & (errors <= limit),
        "obstacle": finite & (errors > limit),
        "uncertain": ~finite,
        # A lone neural depth residual is not independently confirmed support loss.
        # Coherent near obstacles (including negative support) are still handled
        # by the spatial evaluator; external confirmed hazards can supply critical.
        "critical": np.zeros_like(finite, dtype=bool),
        "negative_support": np.isfinite(signed) & (signed < -limit),
        "valid": bool(valid),
    }


def source_gray(image):
    return np.asarray(image.convert("L"), dtype=np.uint8)


def public_spatial(value):
    """Keep the HTTP response and preview metadata within their existing bounds."""
    return {
        **{
            key: item
            for key, item in value.items()
            if not key.startswith("_") and key not in {"regions", "boundary"}
        },
        "regions": value.get("regions", [])[:16],
        "boundary": value.get("boundary", [])[
            :: max(1, len(value.get("boundary", [])) // 16 + 1)
        ],
    }


def assess_evidence(maps, corridor, params, *, raw_reason=None):
    """Identical live/replay spatial evidence, including independent 3D vetoes."""
    from .spatial_evidence import assess_maps, semantic_masks

    if "labels" in maps and "confidence" in maps:
        maps = {
            **maps,
            **semantic_masks(
                maps["labels"], maps["confidence"], params["pixel_confidence"]
            ),
        }
    spatial = assess_maps(
        maps["floor"],
        maps["obstacle"],
        maps["uncertain"],
        maps.get("corridor", corridor),
        params,
        critical_mask=maps.get("critical"),
    )
    if maps.get("geometry_guard_clear") is False:
        spatial.update(
            candidate="obstacle",
            guard_clear=False,
            hard_stop=True,
            reason="navigation_geometry_near_obstacle",
        )
    elif maps.get("geometry_collision") is True or maps.get("geometry_blocked") is True:
        spatial.update(candidate="obstacle", reason="navigation_geometry_collision")
    if maps.get("valid", True) is not True or raw_reason == "vision_image_quality":
        spatial.update(
            valid=False,
            reason=raw_reason or "navigation_invalid_evidence",
            guard_clear=False,
        )
    return spatial
