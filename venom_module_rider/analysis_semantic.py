"""Offline semantic adapter inside the existing CPU-only analysis worker."""

from __future__ import annotations

from pathlib import Path

from .perception_catalog import SEGFORMER, YOLO, artifact_fingerprint


class FrozenCatalog:
    """The child uses the qualified run snapshot, never the live selection."""

    def __init__(self, model_id, path):
        self.model_id = model_id
        self.model = Path(path)

    def path(self, model_id):
        if model_id != self.model_id:
            raise ValueError("analysis_model_identity_mismatch")
        return self.model


class SemanticCandidate:
    """Reuse native live preprocessing and source overlays in an owned child."""

    def __init__(self, profile, qualified, threads):
        from .perception_runtime import build_detector

        model_id = qualified["id"]
        if model_id not in {SEGFORMER, YOLO}:
            raise ValueError("analysis_model_needs_review")
        if qualified["model_sha256"] != artifact_fingerprint(qualified["files"]):
            raise ValueError("analysis_model_hash_mismatch")
        backends = {
            SEGFORMER: {"segformer_torch", "segformer_onnx"},
            YOLO: {"yolo_semantic_onnx"},
        }
        if qualified["backend"] not in backends[model_id]:
            raise ValueError("analysis_model_variant_mismatch")
        import torch

        torch.set_num_threads(threads)
        torch.set_num_interop_threads(1)
        self.identity = model_id, qualified["model_sha256"]
        self.detector = build_detector(
            FrozenCatalog(model_id, profile["model"]),
            model_id,
            {
                "sha256": qualified["model_sha256"],
                "backend": qualified["backend"],
                "files": qualified["files"],
            },
        )
        self.detector.threads = threads
        self.metadata = {
            **qualified,
            "threads": threads,
            "device": "cpu",
            "load_timing": "included_in_first_perception_call",
            "perception_timing": "decode_preprocess_load_infer_postprocess_overlay",
        }

    def detect(self, payload, corridor):
        from .perception_runtime import source_overlay

        result = dict(self.detector.detect(payload, corridor))
        if result.get("state") not in {"clear", "obstacle", "unknown"}:
            raise ValueError("analysis_model_output")
        preview = result.pop("_preview", None)
        if not isinstance(preview, bytes) or not preview:
            raise ValueError("analysis_model_preview_unavailable")
        return {
            **result,
            "model_id": self.identity[0],
            "artifact_sha256": self.identity[1],
            "drive_supported": False,
            "distance_measurement": False,
        }, source_overlay(payload, preview)
