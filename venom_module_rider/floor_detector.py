"""Lazy SegFormer ONNX inference on the host, independent of chat runtime."""

import hashlib
import io
import os
from pathlib import Path

FLOOR_LABEL = 3
# ADE20k rug is enabled only by an explicit per-model surface policy.
UNSUPPORTED_GROUND_LABELS = (6, 11, 28)


class FloorDetector:
    def __init__(self):
        self.path = Path(
            os.getenv("RIDER_VISION_MODEL", "/models/rider-floor/model.onnx")
        )
        self.expected = os.getenv("RIDER_VISION_SHA256", "")
        self.threads = 2
        self.session = None
        self.digest = None

    def status(self):
        return {
            "configured": self.path.is_file() and len(self.expected) == 64,
            "loaded": self.session is not None,
            "device": "cuda"
            if self.session is not None
            and self.session.get_providers()[0] == "CUDAExecutionProvider"
            else None,
            "model": "segformer-b0-ade",
            "sha256": self.digest or self.expected,
            "purpose": "floor_segmentation",
            "distance_measurement": False,
        }

    def _load(self):
        if self.session is not None:
            return
        if not self.path.is_file() or len(self.expected) != 64:
            raise ValueError("vision_model_unconfigured")
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        if digest != self.expected:
            raise ValueError("vision_model_hash_mismatch")
        from .cuda_execution import require_cuda

        require_cuda()
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = self.threads
        options.inter_op_num_threads = 1
        from .cuda_execution import cuda_session

        session = cuda_session(self.path, options)
        if len(session.get_inputs()) != 1:
            raise ValueError("vision_model_format")
        self.session, self.digest = session, digest

    def detect(self, payload, corridor, *, preview=True, evidence=False, semantic=None):
        """Produce source-bound classification without rasterizing display geometry."""
        import numpy as np
        from PIL import Image

        if semantic is not None:
            from .perception_tuning import validate_resolved_semantic

            semantic = validate_resolved_semantic(semantic)
        if not payload or len(payload) > 512 * 1024:
            raise ValueError("vision_image_bounds")
        with Image.open(io.BytesIO(payload)) as image:
            if image.format not in {"JPEG", "PNG"} or not (
                32 <= image.width <= 1280 and 32 <= image.height <= 720
            ):
                raise ValueError("vision_image_bounds")
            source_size = image.size
            if evidence:
                from .navigation_maps import source_gray

                gray = source_gray(image)
            rgb = (
                np.asarray(
                    image.convert("RGB").resize((512, 512), Image.Resampling.BILINEAR),
                    dtype=np.float32,
                )
                / 255
            )
        # Darkness or flat/unusable capture must not become clearance.
        if rgb.mean() < 0.04 or rgb.std() < 0.01:
            result = {
                "state": "unknown",
                "reason": "vision_image_quality",
                "floor_fraction": 0.0,
            }
            if preview:
                result["_preview"] = render_preview(
                    rgb,
                    np.zeros((1, 150, 4, 4)),
                    corridor,
                    semantic=semantic,
                    draw_geometry=False,
                )
            return result
        self._load()
        session = self.session
        if session is None:
            raise ValueError("vision_model_unconfigured")
        pixels = (
            (rgb - np.array([0.485, 0.456, 0.406], dtype=np.float32))
            / np.array([0.229, 0.224, 0.225], dtype=np.float32)
        ).transpose(2, 0, 1)[None]
        logits = np.asarray(
            session.run(None, {session.get_inputs()[0].name: pixels})[0]
        )
        maps = None
        if semantic is not None or evidence:
            from .navigation_maps import semantic_maps

            maps = semantic_maps(logits, source_size, semantic=semantic)
        result = classify_logits(logits, corridor, semantic=semantic, source_maps=maps)
        if evidence:
            assert maps is not None
            result["_navigation_maps"] = {**maps, "gray": gray}
        if preview:
            result["_preview"] = render_preview(
                rgb,
                logits,
                corridor,
                semantic=semantic,
                source_maps=maps,
                draw_geometry=False,
            )
        return result


def classify_logits(logits, corridor, *, semantic=None, source_maps=None):
    import numpy as np

    if (
        logits.ndim != 4
        or logits.shape[0] != 1
        or logits.shape[1] != 150
        or min(logits.shape[2:]) < 4
        or not np.isfinite(logits).all()
    ):
        raise ValueError("vision_model_output")
    if semantic is not None:
        return _classify_configured(logits, corridor, semantic, source_maps)
    x0, y0, x1, y1 = corridor
    h, w = logits.shape[2:]
    roi = logits[
        0,
        :,
        int(y0 * h) : max(int(y1 * h), int(y0 * h) + 1),
        int(x0 * w) : max(int(x1 * w), int(x0 * w) + 1),
    ]
    normalized = roi - roi.max(axis=0)
    probabilities = np.exp(normalized)
    probabilities /= probabilities.sum(axis=0)
    labels = roi.argmax(axis=0)
    return _classify_legacy(labels, probabilities.max(axis=0))


def _classify_legacy(labels, confidence):
    import numpy as np

    confident = confidence >= 0.8
    # ADE20k floor is label 3. Every other label is an obstruction candidate.
    floor = (labels == FLOOR_LABEL) & confident
    fraction = float(floor.mean())
    counts = np.bincount(labels.ravel(), minlength=150)
    dominant = int(counts.argmax())
    if float(confident.mean()) < 0.99:
        state, reason = "unknown", "vision_low_confidence"
    elif ((labels != FLOOR_LABEL) & confident).any():
        state, reason = "obstacle", "corridor_not_floor"
    elif fraction < 1.0:
        state, reason = "unknown", "vision_floor_uncertainty"
    else:
        state, reason = "clear", "floor_candidate"
    return {
        "state": state,
        "reason": reason,
        "floor_fraction": fraction,
        "corridor_pixels": int(labels.size),
        "uncertain_pixels": int((~confident).sum()),
        "non_floor_pixels": int(((labels != FLOOR_LABEL) & confident).sum()),
        "confidence_fraction": float(confident.mean()),
        "dominant_class_id": dominant,
        "dominant_class_fraction": float(counts[dominant] / labels.size),
        "dominant_class_confidence": float(confidence[labels == dominant].mean()),
        "floor_candidate_fraction": float((labels == FLOOR_LABEL).mean()),
        "unsupported_ground_fraction": float(
            np.isin(labels, UNSUPPORTED_GROUND_LABELS).mean()
        ),
    }


def classify_compact_maps(maps, corridor, *, semantic=None):
    """Score a compact native grid, preserving the legacy ROI and diagnostics."""
    from .spatial_evidence import corridor_mask, semantic_masks

    if semantic is not None:
        return _classify_configured(None, corridor, semantic, maps)
    labels, confidence = maps["labels"], maps["confidence"]
    semantic_masks(labels, confidence, 0.8)
    route = corridor_mask(corridor, labels.shape)
    result = _classify_legacy(labels[route], confidence[route])
    if maps.get("valid", True) is not True:
        result.update(state="unknown", reason="navigation_invalid_evidence")
    return result


def _classify_configured(logits, corridor, semantic, source_maps):
    """Use the same compact source masks and spatial rules as temporal replay."""
    import numpy as np

    from .navigation_maps import assess_evidence, public_spatial, semantic_maps
    from .perception_tuning import validate_resolved_semantic
    from .spatial_evidence import corridor_mask

    options = validate_resolved_semantic(semantic)
    maps = source_maps
    if maps is None:
        maps = semantic_maps(
            logits, (logits.shape[3], logits.shape[2]), semantic=options
        )
    spatial = assess_evidence(maps, corridor, options)
    labels, confidence = maps["labels"], maps["confidence"]
    route = corridor_mask(corridor, labels.shape)
    region_labels, scores = labels[route], confidence[route]
    confident = scores >= options["pixel_confidence"]
    traversable = np.isin(region_labels, options["traversable_labels"])
    counts = np.bincount(region_labels, minlength=150)
    dominant = int(counts.argmax())
    candidate = spatial["candidate"]
    state = {"clear": "clear", "obstacle": "obstacle", "uncertain": "unknown"}[
        candidate
    ]
    if spatial.get("valid") is not True:
        state = "unknown"
    unsupported = [
        label
        for label in UNSUPPORTED_GROUND_LABELS
        if label not in options["traversable_labels"]
    ]
    return {
        "state": state,
        "reason": spatial["reason"],
        "floor_fraction": spatial["floor_fraction"],
        "corridor_pixels": int(region_labels.size),
        "uncertain_pixels": int((~confident).sum()),
        "non_floor_pixels": int((~traversable & confident).sum()),
        "confidence_fraction": float(confident.mean()),
        "dominant_class_id": dominant,
        "dominant_class_fraction": float(counts[dominant] / region_labels.size),
        "dominant_class_confidence": float(scores[region_labels == dominant].mean()),
        "floor_candidate_fraction": float(traversable.mean()),
        "unsupported_ground_fraction": float(
            np.isin(region_labels, unsupported).mean()
        ),
        "semantic_spatial": public_spatial(spatial),
    }


def render_preview(
    rgb, logits, corridor, *, semantic=None, source_maps=None, draw_geometry=True
):
    """Diagnostic mask uses exactly the configured confidence and class mapping."""
    import numpy as np
    from PIL import Image, ImageDraw

    from .navigation_maps import semantic_maps

    maps = source_maps
    if maps is None:
        maps = semantic_maps(
            logits, (logits.shape[3], logits.shape[2]), semantic=semantic
        )
    labels = maps["labels"]
    colors = np.full((*labels.shape, 3), (255, 185, 35), dtype=np.uint8)
    colors[maps["floor"]] = (35, 210, 100)
    colors[maps["obstacle"]] = (240, 60, 65)
    # Ground-like classes remain a distinct diagnostic only when unsupported.
    colors[maps["obstacle"] & np.isin(labels, UNSUPPORTED_GROUND_LABELS)] = (
        65,
        130,
        245,
    )
    height, width = rgb.shape[:2]
    overlay = Image.fromarray(colors).resize((width, height), Image.Resampling.NEAREST)
    image = Image.blend(Image.fromarray((rgb * 255).astype(np.uint8)), overlay, 0.4)
    if draw_geometry:
        x0, y0, x1, y1 = corridor
        ImageDraw.Draw(image).rectangle(
            (
                int(x0 * (width - 1)),
                int(y0 * (height - 1)),
                int(x1 * (width - 1)),
                int(y1 * (height - 1)),
            ),
            outline="white",
            width=3,
        )
    stream = io.BytesIO()
    image.save(stream, format="JPEG", quality=85)
    return stream.getvalue()
