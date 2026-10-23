"""Owned native/ONNX perception adapters with one loaded model at a time."""

from __future__ import annotations

import io
import threading
import time
from collections import deque

from .depth_detector import DepthDetector, decode_payload
from .errors import RiderError
from .floor_detector import FloorDetector, classify_logits, render_preview
from .perception_catalog import DEPTH, SEGFORMER, YOLO


class TorchSegformer:
    def __init__(self, path):
        self.path = path
        self.model = None

    def status(self):
        return {"loaded": self.model is not None, "device": "cpu"}

    def detect(self, payload, corridor):
        import numpy as np
        import torch
        from PIL import Image
        from transformers import SegformerForSemanticSegmentation

        image = decode_payload(payload)
        rgb = (
            np.asarray(
                image.resize((512, 512), Image.Resampling.BILINEAR), dtype=np.float32
            )
            / 255
        )
        if self.model is None:
            self.model = SegformerForSemanticSegmentation.from_pretrained(
                self.path, local_files_only=True, use_safetensors=True
            ).eval()
            if self.model.config.id2label.get(3) != "floor":
                self.model = None
                raise ValueError("model_labels_invalid")
        pixels = (
            (rgb - np.array([0.485, 0.456, 0.406], dtype=np.float32))
            / np.array([0.229, 0.224, 0.225], dtype=np.float32)
        ).transpose(2, 0, 1)[None]
        with torch.inference_mode():
            logits = (
                self.model(pixel_values=torch.from_numpy(pixels)).logits.cpu().numpy()
            )
        result = classify_logits(logits, corridor)
        if rgb.mean() < 0.04 or rgb.std() < 0.01:
            result.update(state="unknown", reason="vision_image_quality")
        result["_preview"] = render_preview(rgb, logits, corridor)
        return result


def source_overlay(payload, preview):
    """The existing segmenter works on a square; undo that warp for display."""
    from PIL import Image

    image = decode_payload(payload)
    with Image.open(io.BytesIO(preview)) as mask:
        if mask.size == image.size:
            return preview
        corrected = mask.resize(image.size, Image.Resampling.BILINEAR)
        output = io.BytesIO()
        corrected.save(output, format="JPEG", quality=85)
        return output.getvalue()


def build_detector(catalog, model_id, artifact):
    path = catalog.path(model_id)
    if model_id == DEPTH:
        result = DepthDetector()
        result.path = path
        return result
    if artifact["backend"] == "segformer_onnx":
        detector = FloorDetector()
        detector.path = path / next(iter(artifact["files"]))
        detector.expected = next(iter(artifact["files"].values()))
        return detector
    if model_id == SEGFORMER:
        return TorchSegformer(path)
    if model_id == YOLO:
        return YoloSemanticOnnx(path)
    raise RiderError("model_needs_review", 409)


class ModelRuntime:
    """All loads and inference run under an owned lock in the vision executor."""

    def __init__(self, catalog):
        self.catalog = catalog
        self.lock = threading.RLock()
        self.detector = None
        self.identity = None
        self.generation = 0
        self.state = "unloaded"
        self.timings = deque(maxlen=100)
        self.last = None
        self.expected = {}

    def reset(self):
        with self.lock:
            self.detector = None
            self.identity = None
            self.timings.clear()
            self.last = None
            self.state = "unloaded"
            self.generation += 1

    def validate(self, device_id, frame):
        expected = self.expected.get(device_id)
        if expected is not None and (
            frame.config_revision != expected["revision"]
            or frame.perception.model_id != expected["model_id"]
            or frame.perception.artifact_sha256 != expected["artifact_sha256"]
        ):
            raise RiderError("model_selection_conflict", 409)

    def detect(self, payload, frame):
        with self.lock:
            model_id = frame.perception.model_id
            artifact = self.catalog.ready(model_id)
            if artifact["sha256"] != frame.perception.artifact_sha256:
                raise RiderError("model_hash_mismatch", 409)
            if (model_id == DEPTH) != (frame.perception.engine == "depth_plane"):
                raise RiderError("model_engine_mismatch", 409)
            key = model_id, artifact["sha256"]
            if self.identity != key:
                self.detector = None
                self.state = "loading"
                self.detector = build_detector(self.catalog, model_id, artifact)
                self.identity = key
            detector = self.detector
            assert detector is not None
            start = time.perf_counter()
            try:
                if model_id == DEPTH:
                    if (
                        frame.protocol_version == 3
                        and frame.avoidance.geometry.mode == "ground"
                    ):
                        result = detector.detect_ground(
                            payload,
                            frame.corridor,
                            frame.perception,
                            frame.avoidance.geometry,
                        )
                    else:
                        result = detector.detect(
                            payload, frame.corridor, frame.perception
                        )
                else:
                    result = detector.detect(payload, frame.corridor)
                    if result.get("_preview"):
                        result["_preview"] = source_overlay(payload, result["_preview"])
                elapsed = (time.perf_counter() - start) * 1000
                self.timings.append(elapsed)
                self.last = time.monotonic()
                self.state = "active"
                return {
                    **result,
                    "model_id": model_id,
                    "artifact_sha256": artifact["sha256"],
                    "model_generation": self.generation,
                    "drive_supported": model_id == DEPTH,
                    "distance_measurement": False,
                }
            except Exception:
                self.state = "unavailable"
                raise

    def status(self):
        return {
            "state": self.state,
            "active_model_id": self.identity[0] if self.identity else None,
            "model_generation": self.generation,
        }

    def detector_status(self, perception):
        model_id = perception["model_id"]
        row = self.catalog.describe(model_id)
        matching = self.identity == (model_id, perception.get("artifact_sha256"))
        legacy = self.detector.status() if matching and self.detector else {}
        return {
            **legacy,
            "configured": row["selectable"],
            "loaded": matching and legacy.get("loaded") is True,
            "model": model_id,
            "model_id": model_id,
            "sha256": legacy.get("sha256", ""),
            "artifact_sha256": row["sha256"],
            "model_generation": self.generation,
            "drive_supported": model_id == DEPTH,
            "distance_measurement": False,
        }

    def diagnostics(self):
        import os
        from pathlib import Path

        timings = sorted(self.timings)
        ram = None
        try:
            ram = (
                int(Path("/proc/self/statm").read_text().split()[1])
                * os.sysconf("SC_PAGE_SIZE")
                / 1024**2
            )
        except (OSError, ValueError, IndexError):
            pass
        return {
            "runtime": self.status(),
            "inference_ms": timings[-1] if timings else None,
            "p50_ms": timings[len(timings) // 2] if timings else None,
            "p95_ms": timings[min(len(timings) - 1, int(len(timings) * 0.95))]
            if timings
            else None,
            "fps": None,
            "ram_mb": round(ram, 1) if ram is not None else None,
            "vram_mb": None,
        }


class YoloSemanticOnnx:
    """Pinned raw-logit export; RGB letterbox and inverse padding are explicit."""

    def __init__(self, path):
        self.path = path
        self.session = None

    def status(self):
        return {"loaded": self.session is not None, "device": "cpu"}

    def detect(self, payload, corridor):
        import numpy as np
        import onnxruntime as ort
        import torch
        from PIL import Image

        image = decode_payload(payload)
        scale = min(640 / image.width, 640 / image.height)
        width, height = round(image.width * scale), round(image.height * scale)
        left = round((640 - width) / 2 - 0.1)
        top = round((640 - height) / 2 - 0.1)
        rgb = (
            np.asarray(
                image.resize((width, height), Image.Resampling.BILINEAR),
                dtype=np.float32,
            )
            / 255
        )
        pixels = np.full((640, 640, 3), 114 / 255, dtype=np.float32)
        pixels[top : top + height, left : left + width] = rgb
        if self.session is None:
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            self.session = ort.InferenceSession(
                str(self.path / "model.onnx"),
                options,
                providers=["CPUExecutionProvider"],
            )
        logits = self.session.run(
            None, {self.session.get_inputs()[0].name: pixels.transpose(2, 0, 1)[None]}
        )[0]
        if (
            logits.ndim != 4
            or logits.shape[:2] != (1, 150)
            or not np.isfinite(logits).all()
        ):
            raise ValueError("vision_model_output")
        logits = torch.nn.functional.interpolate(
            torch.from_numpy(logits),
            size=(640, 640),
            mode="bilinear",
            align_corners=False,
        ).numpy()[:, :, top : top + height, left : left + width]
        result = classify_logits(logits, corridor)
        if rgb.mean() < 0.04 or rgb.std() < 0.01:
            result.update(state="unknown", reason="vision_image_quality")
        result["_preview"] = render_preview(rgb, logits, corridor)
        return result
