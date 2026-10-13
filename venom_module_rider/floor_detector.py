"""Lazy SegFormer ONNX inference on the host, independent of chat runtime."""

import hashlib
import io
import os
from pathlib import Path


class FloorDetector:
    def __init__(self):
        self.path = Path(
            os.getenv("RIDER_VISION_MODEL", "/models/rider-floor/model.onnx")
        )
        self.expected = os.getenv("RIDER_VISION_SHA256", "")
        self.session = None
        self.digest = None

    def status(self):
        return {
            "configured": self.path.is_file() and len(self.expected) == 64,
            "loaded": self.session is not None,
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
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(
            str(self.path), options, providers=["CPUExecutionProvider"]
        )
        if len(session.get_inputs()) != 1:
            raise ValueError("vision_model_format")
        self.session, self.digest = session, digest

    def detect(self, payload, corridor, *, preview=True):
        import numpy as np
        from PIL import Image

        if not payload or len(payload) > 512 * 1024:
            raise ValueError("vision_image_bounds")
        with Image.open(io.BytesIO(payload)) as image:
            if image.format not in {"JPEG", "PNG"} or not (
                32 <= image.width <= 1280 and 32 <= image.height <= 720
            ):
                raise ValueError("vision_image_bounds")
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
                    rgb, np.zeros((1, 150, 4, 4)), corridor
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
        result = classify_logits(logits, corridor)
        if preview:
            result["_preview"] = render_preview(rgb, logits, corridor)
        return result


def classify_logits(logits, corridor):
    import numpy as np

    if (
        logits.ndim != 4
        or logits.shape[0] != 1
        or logits.shape[1] != 150
        or min(logits.shape[2:]) < 4
        or not np.isfinite(logits).all()
    ):
        raise ValueError("vision_model_output")
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
    confident = probabilities.max(axis=0) >= 0.8
    # ADE20k floor is label 3. Every other label is an obstruction candidate.
    floor = (labels == 3) & confident
    fraction = float(floor.mean())
    if float(confident.mean()) < 0.99:
        state, reason = "unknown", "vision_low_confidence"
    elif fraction < 1.0:
        state, reason = "obstacle", "corridor_not_floor"
    else:
        state, reason = "clear", "floor_candidate"
    return {"state": state, "reason": reason, "floor_fraction": fraction}


def render_preview(rgb, logits, corridor):
    """Diagnostic mask: confident floor green, other labels red, uncertainty amber."""
    import numpy as np
    from PIL import Image, ImageDraw

    labels = logits[0].argmax(axis=0)
    values = logits[0] - logits[0].max(axis=0)
    probabilities = np.exp(values)
    confidence = probabilities.max(axis=0) / probabilities.sum(axis=0)
    colors = np.full((*labels.shape, 3), (255, 185, 35), dtype=np.uint8)
    colors[(confidence >= 0.8) & (labels == 3)] = (35, 210, 100)
    colors[(confidence >= 0.8) & (labels != 3)] = (240, 60, 65)
    overlay = Image.fromarray(colors).resize((512, 512), Image.Resampling.NEAREST)
    image = Image.blend(Image.fromarray((rgb * 255).astype(np.uint8)), overlay, 0.4)
    x0, y0, x1, y1 = corridor
    ImageDraw.Draw(image).rectangle(
        (int(x0 * 511), int(y0 * 511), int(x1 * 511), int(y1 * 511)),
        outline="white",
        width=3,
    )
    stream = io.BytesIO()
    image.save(stream, format="JPEG", quality=85)
    return stream.getvalue()
