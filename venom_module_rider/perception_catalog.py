"""Host-owned live model records; the Pi persists only a selected identity.

ProfileCatalog remains the entrypoint for offline and live model definitions.
GET describes local files and receipts; it never imports ML or downloads weights.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .errors import RiderError

_MODEL_WEIGHTS_FILENAME = "model.safetensors"


SEGFORMER = "segformer-b0-ade20k"
DEPTH = "depth-anything-v2-indoor-small"
FAST = "fast-scnn-floor"
YOLO = "yolo26n-sem-ade20k"

SOURCES = {
    SEGFORMER: {
        "repo": "nvidia/segformer-b0-finetuned-ade-512-512",
        "revision": "489d5cd81a0b59fab9b7ea758d3548ebe99677da",
        "sha256": "6ae39addd01de6b1b8bde2cf677d43a5cd733424b8d186de3f95d1c51fee23f9",
    },
    DEPTH: {
        "repo": "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf",
        "revision": "8078d68a9c75a972131914f6afd0c1723be0da7f",
        "sha256": "e990eb82fbf11b05b7813261196a2b841bdcf5a05f64396724a8987fa90504a3",
    },
    FAST: {
        "repo": "Tanishjain9/fast-scnn-floor-segmentation",
        "revision": "9c8e45dcab8754f7a0a893d7839f52d73db79986",
        "sha256": "ddcd6459cb0d07a3f924404a928e7a6629cb8d087c38b3958fb30002dbc78bdd",
    },
    YOLO: {
        "repo": "ultralytics/assets",
        "revision": "v8.4.0-asset-444468363",
        "sha256": "68ac71bef2868c987fff7cd7c49cb656922f6d8447ad21af3893305577435000",
    },
}
LABELS = {
    SEGFORMER: "SegFormer B0 · ADE20K",
    DEPTH: "Depth Anything V2 · Indoor Small",
    FAST: "Fast-SCNN · Floor",
    YOLO: "YOLO26n · Semantic ADE20K",
}


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact_fingerprint(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def selected_identity(policy):
    perception = policy.get("perception") or {}
    model_id = perception.get("model_id") or (
        DEPTH if perception.get("engine") == "depth_plane" else SEGFORMER
    )
    return {
        "model_id": model_id,
        "artifact_sha256": perception.get("artifact_sha256", ""),
        "revision": policy["revision"],
    }


class LiveCatalog:
    """A view of ProfileCatalog's host assets, never a replicated Pi registry."""

    def __init__(self, profiles):
        self.profiles = profiles
        self.root = profiles.live_assets
        self.cache = {}

    def path(self, model_id):
        self.require(model_id)
        if model_id == DEPTH:
            return self.profiles.resolve("da2-default").model
        if model_id == SEGFORMER and os.getenv("RIDER_VISION_MODEL"):
            return Path(os.environ["RIDER_VISION_MODEL"]).resolve().parent
        return self.root / model_id

    def require(self, model_id):
        if model_id not in SOURCES:
            raise RiderError("model_unknown", 404)
        return SOURCES[model_id]

    def receipt_path(self, model_id):
        # Keep qualification receipts away from immutable/shared legacy assets.
        self.require(model_id)
        return self.root / "receipts" / (model_id + ".json")

    def definition(self, model_id):
        source = self.require(model_id)
        return {
            "id": model_id,
            "label": LABELS[model_id],
            "engine": "depth_plane" if model_id == DEPTH else "segformer",
            "task": "relative_ground_geometry" if model_id == DEPTH else "semantic",
            "revision": source["revision"],
            "source": source["repo"],
            "drive_supported": model_id == DEPTH,
            "distance_measurement": False,
            "provider": "venom",
            "execution_provider": "cuda",
            "qualification_details": (
                ["external_data_missing", "preprocessing_unverified"]
                if model_id == FAST
                else []
            ),
            "qualification": "needs_review" if model_id == FAST else "supported",
        }

    def local_files(self, model_id):
        """Validate model manifests and resolve an explicit operator override."""
        root = self.path(model_id)
        if model_id == DEPTH:
            metadata = json.loads((root / "manifest.json").read_text())
            files = metadata["files"]
            required = {
                "config.json",
                "preprocessor_config.json",
                _MODEL_WEIGHTS_FILENAME,
            }
            if (
                set(files) != required
                or metadata.get("revision") != SOURCES[DEPTH]["revision"]
                or files[_MODEL_WEIGHTS_FILENAME] != SOURCES[DEPTH]["sha256"]
            ):
                raise ValueError("manifest")
            return files, "da2"
        onnx = root / "model.onnx"
        if model_id == SEGFORMER and os.getenv("RIDER_VISION_MODEL"):
            onnx = Path(os.environ["RIDER_VISION_MODEL"]).resolve()
            expected = os.getenv("RIDER_VISION_SHA256", "")
            if len(expected) != 64:
                raise ValueError("manifest")
            return {onnx.name: expected}, "segformer_onnx"
        metadata = json.loads((root / "manifest.json").read_text())
        files = metadata["files"]
        if (
            metadata.get("model_id") != model_id
            or metadata.get("revision") != SOURCES[model_id]["revision"]
            or not isinstance(files, dict)
            or not files
            or any(Path(name).name != name for name in files)
        ):
            raise ValueError("manifest")
        if model_id == SEGFORMER:
            if files.get(_MODEL_WEIGHTS_FILENAME) != SOURCES[model_id]["sha256"]:
                raise ValueError("manifest")
            return files, "segformer_torch"
        if model_id == YOLO:
            if (
                files.get("source.pt") != SOURCES[YOLO]["sha256"]
                or set(files) != {"source.pt", "model.onnx"}
                or metadata.get("exporter") != "8.4.184"
                or metadata.get("parity") is not True
            ):
                raise ValueError("manifest")
            return files, "yolo_semantic_onnx"
        return files, "semantic_onnx"

    def qualify_files(self, model_id):
        """Hash actual bytes. Stat-keyed cache avoids rereading weights per GET."""
        try:
            files, backend = self.local_files(model_id)
            root = self.path(model_id)
            signature = tuple(
                (
                    name,
                    expected,
                    (root / name).stat().st_mtime_ns,
                    (root / name).stat().st_size,
                )
                for name, expected in sorted(files.items())
            )
            key = (model_id, signature)
            cached = self.cache.get(key)
            if cached is None:
                if any(
                    digest_file(root / name) != value for name, value in files.items()
                ):
                    raise RiderError("model_hash_mismatch", 409)
                cached = (artifact_fingerprint(files), backend)
                self.cache = {k: v for k, v in self.cache.items() if k[0] != model_id}
                self.cache[key] = cached
            return {"sha256": cached[0], "backend": cached[1], "files": files}
        except RiderError:
            raise
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RiderError("model_not_installed", 409) from exc

    def describe(self, model_id):
        row = {
            **self.definition(model_id),
            "installed": False,
            "loaded": False,
            "selectable": False,
            "installation": "not_installed",
            "reason": "model_not_installed",
            "sha256": "",
            "job": None,
        }
        if row["qualification"] == "needs_review":
            row.update(installation="needs_review", reason="model_needs_review")
            return row
        try:
            artifact = self.qualify_files(model_id)
            row.update(installed=True, sha256=artifact["sha256"])
            receipt = json.loads(self.receipt_path(model_id).read_text())
            if (
                receipt.get("sha256") != artifact["sha256"]
                or receipt.get("smoke") is not True
            ):
                raise ValueError("receipt")
            row.update(installation="ready", selectable=True, reason=None)
        except RiderError as exc:
            row["reason"] = exc.code
            if exc.code == "model_hash_mismatch":
                row["installation"] = "failed"
        except (OSError, ValueError, KeyError, TypeError):
            row.update(
                installation="not_installed", reason="model_verification_required"
            )
        return row

    def ready(self, model_id):
        row = self.describe(model_id)
        if not row["selectable"]:
            raise RiderError(row["reason"], 409)
        return self.qualify_files(model_id)

    def save_receipt(self, model_id):
        artifact = self.qualify_files(model_id)
        path = self.receipt_path(model_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "model_id": model_id,
                "sha256": artifact["sha256"],
                "smoke": True,
                "revision": SOURCES[model_id]["revision"],
                "backend": artifact["backend"],
            }
        )
        # Never follow a predictable temporary-file symlink, and publish only a
        # complete receipt in the same filesystem.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return artifact
