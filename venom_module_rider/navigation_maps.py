"""Compact source-space perception maps; private arrays never enter the wire."""

from __future__ import annotations

import numpy as np
from PIL import Image


def semantic_maps(logits, source_size, *, semantic=None):
    """Compute pmax in bounded workspace instead of another 150-channel tensor."""
    if (
        not isinstance(logits, np.ndarray)
        or logits.ndim != 4
        or logits.shape[:2] != (1, 150)
        or min(logits.shape[2:]) < 4
        or logits.shape[2] > 720
        or logits.shape[3] > 1280
        or not np.isfinite(logits).all()
    ):
        raise ValueError("vision_model_output")
    values = logits[0]
    maximum = values.max(axis=0)
    denominator = np.zeros_like(maximum, dtype=np.float32)
    for channel in values:
        denominator += np.exp(channel - maximum)
    confidence = 1.0 / denominator
    labels = values.argmax(axis=0).astype(np.uint8)
    return semantic_maps_from_compact(
        labels, confidence, source_size, semantic=semantic
    )


def semantic_maps_from_compact(labels, confidence, source_size, *, semantic=None):
    """Resize compact scores once; do not resize a 150-channel model tensor."""
    from .spatial_evidence import semantic_masks

    if (
        not isinstance(source_size, (list, tuple))
        or len(source_size) != 2
        or any(type(value) is not int for value in source_size)
        or not (1 <= source_size[0] <= 1280 and 1 <= source_size[1] <= 720)
    ):
        raise ValueError("vision_image_bounds")
    options = {"pixel_confidence": 0.8, "traversable_labels": (3,)}
    if semantic is not None:
        from .perception_tuning import validate_resolved_semantic

        options = validate_resolved_semantic(semantic)
    # Validate the incoming compact maps before interpolation or casting.
    semantic_masks(
        labels, confidence, options["pixel_confidence"], options["traversable_labels"]
    )
    labels = np.asarray(
        Image.fromarray(labels).resize(source_size, Image.Resampling.NEAREST)
    )
    confidence = np.asarray(
        Image.fromarray(confidence).resize(source_size, Image.Resampling.BILINEAR)
    ).astype(np.float32)
    return {
        **semantic_masks(
            labels,
            confidence,
            options["pixel_confidence"],
            options["traversable_labels"],
        ),
        "labels": labels,
        "confidence": confidence,
    }


def letterbox_semantic_maps(logits, crop, *, output_size=(640, 640), batch_size=8):
    """Inverse-letterbox logits in bounded channel batches.

    Streaming log-sum-exp retains the native bilinear channel interpolation,
    first-index argmax tie handling and pixel probabilities without allocating
    a second full 150 x 640 x 640 tensor. Compact output is still in the native
    cropped grid; source resizing happens only afterwards.
    """
    import torch

    if (
        not isinstance(logits, np.ndarray)
        or logits.ndim != 4
        or logits.shape[:2] != (1, 150)
        or min(logits.shape[2:]) < 4
        or logits.shape[2] > 720
        or logits.shape[3] > 1280
        or not np.isfinite(logits).all()
        or not isinstance(output_size, (list, tuple))
        or len(output_size) != 2
        or any(type(value) is not int for value in output_size)
        or not (1 <= output_size[0] <= 720 and 1 <= output_size[1] <= 1280)
        or type(batch_size) is not int
        or not 1 <= batch_size <= 16
        or not isinstance(crop, (list, tuple))
        or len(crop) != 4
        or any(type(value) is not int for value in crop)
    ):
        raise ValueError("vision_model_output")
    left, top, width, height = crop
    if not (
        0 <= left < left + width <= output_size[1]
        and 0 <= top < top + height <= output_size[0]
    ):
        raise ValueError("vision_model_output")
    maximum = np.full((height, width), -np.inf, dtype=np.float32)
    denominator = np.zeros((height, width), dtype=np.float32)
    labels = np.zeros((height, width), dtype=np.uint8)
    tensor = torch.from_numpy(logits.astype(np.float32, copy=False))
    for offset in range(0, 150, batch_size):
        values = torch.nn.functional.interpolate(
            tensor[:, offset : offset + batch_size],
            size=output_size,
            mode="bilinear",
            align_corners=False,
        ).numpy()[0, :, top : top + height, left : left + width]
        best = values.max(axis=0)
        updated = np.maximum(maximum, best)
        denominator *= np.exp(maximum - updated)
        denominator += np.exp(values - updated).sum(axis=0)
        changed = best > maximum
        labels[changed] = (values.argmax(axis=0) + offset)[changed]
        maximum = updated
    return semantic_maps_from_compact(labels, 1.0 / denominator, (width, height))


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
                maps["labels"],
                maps["confidence"],
                params["pixel_confidence"],
                params.get("traversable_labels", (3,)),
            ),
            # Thresholding cannot validate a previously rejected source.
            "valid": maps.get("valid", True),
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
