"""Owned native/ONNX perception adapters with one loaded model at a time."""

from __future__ import annotations

import io
import threading
import time
from collections import deque

from .depth_detector import DepthDetector, decode_payload
from .errors import RiderError
from .floor_detector import (
    FloorDetector,
    classify_compact_maps,
    classify_logits,
    render_preview,
)
from .perception_catalog import DEPTH, SEGFORMER, YOLO


class TorchSegformer:
    def __init__(self, path):
        self.path = path
        self.model = None

    def status(self):
        return {
            "loaded": self.model is not None,
            "device": next(self.model.parameters()).device.type
            if self.model is not None
            else None,
        }

    def detect(self, payload, corridor, *, evidence=False, semantic=None):
        import numpy as np
        import torch
        from PIL import Image
        from transformers import SegformerForSemanticSegmentation

        if semantic is not None:
            from .perception_tuning import validate_resolved_semantic

            semantic = validate_resolved_semantic(semantic)
        image = decode_payload(payload)
        rgb = (
            np.asarray(
                image.resize((512, 512), Image.Resampling.BILINEAR), dtype=np.float32
            )
            / 255
        )
        from .cuda_execution import require_cuda

        device = require_cuda()
        if self.model is None:
            self.model = (
                SegformerForSemanticSegmentation.from_pretrained(
                    self.path, local_files_only=True, use_safetensors=True
                )
                .eval()
                .to(device)
            )
            if self.model.config.id2label.get(3) != "floor":
                self.model = None
                raise ValueError("model_labels_invalid")
        pixels = (
            (rgb - np.array([0.485, 0.456, 0.406], dtype=np.float32))
            / np.array([0.229, 0.224, 0.225], dtype=np.float32)
        ).transpose(2, 0, 1)[None]
        with torch.inference_mode():
            logits = (
                self.model(pixel_values=torch.from_numpy(pixels).to(device))
                .logits.cpu()
                .numpy()
            )
        if semantic is not None and 28 in semantic["traversable_labels"]:
            if self.model.config.id2label.get(28) != "rug":
                raise ValueError("model_labels_invalid")
        result = semantic_output(logits, corridor, image, rgb, semantic, evidence)
        return result


def semantic_output(logits, corridor, image, rgb, semantic, evidence):
    """Reuse source maps for raw scoring, temporal evidence and the preview."""
    from .navigation_maps import semantic_maps, source_gray

    maps = None
    if semantic is not None or evidence:
        maps = semantic_maps(logits, image.size, semantic=semantic)
    result = classify_logits(logits, corridor, semantic=semantic, source_maps=maps)
    if evidence:
        assert maps is not None
        result["_navigation_maps"] = {**maps, "gray": source_gray(image)}
    if rgb.mean() < 0.04 or rgb.std() < 0.01:
        result.update(state="unknown", reason="vision_image_quality")
    result["_preview"] = render_preview(
        rgb, logits, corridor, semantic=semantic, source_maps=maps
    )
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
        self.failure = None
        self.state = "unloaded"
        self.timings: deque[float] = deque(maxlen=100)
        self.last = None
        self.finishes: deque[float] = deque(maxlen=100)
        self.expected = {}

    def reset(self):
        with self.lock:
            self.detector = None
            self.identity = None
            self.timings.clear()
            self.last = None
            self.finishes.clear()
            self.failure = None
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
                from .navigation_profiles import parameters

                perception = frame.perception.model_dump()
                behavior = frame.behavior.model_dump()
                options = (
                    {"evidence": parameters(behavior, perception)}
                    if frame.behavior.profile_id != "legacy"
                    else {}
                )
                if model_id != DEPTH and (
                    frame.behavior.profile_id != "legacy"
                    or model_id in perception.get("semantic_models", {})
                ):
                    from .perception_tuning import semantic_parameters

                    options["semantic"] = semantic_parameters(
                        perception, behavior, model_id
                    )
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
                            **options,
                        )
                    else:
                        result = detector.detect(
                            payload, frame.corridor, frame.perception, **options
                        )
                else:
                    result = detector.detect(payload, frame.corridor, **options)
                    if result.get("_preview"):
                        result["_preview"] = source_overlay(payload, result["_preview"])
                elapsed = (time.perf_counter() - start) * 1000
                self.timings.append(elapsed)
                self.last = time.monotonic()
                self.finishes.append(self.last)
                self.failure = None
                self.state = "active"
                return {
                    **result,
                    "model_id": model_id,
                    "artifact_sha256": artifact["sha256"],
                    "model_generation": self.generation,
                    "drive_supported": model_id == DEPTH,
                    "distance_measurement": False,
                }
            except Exception as exc:
                self.failure = (
                    str(exc)
                    if str(exc)
                    in {
                        "vision_cuda_unavailable",
                        "vision_cuda_provider_unavailable",
                        "vision_cuda_required",
                    }
                    else "model_inference_failed"
                )
                self.state = "unavailable"
                raise

    def warm(self, perception):
        """Load outside capture TTL; synthetic output is never published."""
        import numpy as np
        from PIL import Image

        from .vision_contract import VisionFrame

        payload = io.BytesIO()
        Image.fromarray(
            np.random.default_rng(7).integers(40, 220, (96, 160, 3), dtype=np.uint8)
        ).save(payload, format="JPEG")
        frame = VisionFrame(
            protocol_version=1,
            run_id="warmup",
            frame_id="warmup",
            sequence=1,
            config_revision=0,
            age_ms=0.0,
            deadline_ms=500,
            perception=perception,
        )
        self.detect(payload.getvalue(), frame)
        self.timings.clear()
        self.finishes.clear()
        self.last = None

    def status(self):
        return {
            "state": self.state,
            **({"error": self.failure} if self.failure else {}),
            "active_model_id": self.identity[0] if self.identity else None,
            "model_generation": self.generation,
            "execution_provider": (
                self.detector.status().get("device") if self.detector else None
            ),
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
        recent = [at for at in self.finishes if time.monotonic() - at <= 5]
        fps = (
            (len(recent) - 1) / (recent[-1] - recent[0])
            if len(recent) > 1 and recent[-1] > recent[0]
            else None
        )
        ram, vram = None, None
        import sys

        torch = sys.modules.get("torch")
        if torch is not None and torch.cuda.is_initialized():
            vram = torch.cuda.memory_allocated() / 1024**2
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
            "inference_ms": self.timings[-1] if self.timings else None,
            "p50_ms": timings[len(timings) // 2] if timings else None,
            "p95_ms": timings[min(len(timings) - 1, int(len(timings) * 0.95))]
            if timings
            else None,
            "fps": round(fps, 2) if fps is not None else None,
            "memory_scope": "api_process",
            "ram_mb": round(ram, 1) if ram is not None else None,
            "vram_mb": round(vram, 1) if vram is not None else None,
        }


class YoloSemanticOnnx:
    """Pinned raw-logit export; RGB letterbox and inverse padding are explicit."""

    def __init__(self, path):
        self.path = path
        self.threads = 2
        self.session = None

    def status(self):
        return {
            "loaded": self.session is not None,
            "device": "cuda"
            if self.session is not None
            and self.session.get_providers()[0] == "CUDAExecutionProvider"
            else None,
        }

    def detect(self, payload, corridor, *, evidence=False, semantic=None):
        import numpy as np

        from .cuda_execution import require_cuda

        device = require_cuda()
        import onnxruntime as ort
        from PIL import Image

        if semantic is not None:
            from .perception_tuning import validate_resolved_semantic

            semantic = validate_resolved_semantic(semantic)
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
            options.intra_op_num_threads = self.threads
            options.inter_op_num_threads = 1
            from .cuda_execution import cuda_session

            self.session = cuda_session(self.path / "model.onnx", options)
        logits = self.session.run(
            None, {self.session.get_inputs()[0].name: pixels.transpose(2, 0, 1)[None]}
        )[0]
        if (
            logits.ndim != 4
            or logits.shape[:2] != (1, 150)
            or not np.isfinite(logits).all()
        ):
            raise ValueError("vision_model_output")
        from .navigation_maps import (
            letterbox_semantic_maps,
            semantic_maps_from_compact,
            source_gray,
        )

        native = letterbox_semantic_maps(
            logits, (left, top, width, height), device=device
        )
        maps = semantic_maps_from_compact(
            native["labels"], native["confidence"], image.size, semantic=semantic
        )
        # Legacy raw scoring uses its original cropped model grid. Configured
        # scoring uses the same source grid as temporal evidence and the preview.
        result = classify_compact_maps(
            native if semantic is None else maps, corridor, semantic=semantic
        )
        if evidence:
            result["_navigation_maps"] = {**maps, "gray": source_gray(image)}
        if rgb.mean() < 0.04 or rgb.std() < 0.01:
            result.update(state="unknown", reason="vision_image_quality")
        result["_preview"] = render_preview(
            rgb, None, corridor, semantic=semantic, source_maps=maps
        )
        return result
