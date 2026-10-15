"""Offline-only depth backends. No robot transport or motion permission."""

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

MODELS = {
    "da3-small": (
        "depth-anything/DA3-SMALL",
        "e08cab65ca0ec38e7826075418411ab90cab4da3",
    ),
    "da3-base": ("depth-anything/DA3-BASE", "f4a6c9b3c95e41c82048423d3493a81ec3fa810e"),
    "da3metric-large": (
        "depth-anything/DA3METRIC-LARGE",
        "4010e39f3634a45bc60553321fb49fb760bd594e",
    ),
}
CHECKPOINT_HASHES = {
    "da3-small": "364492e38a3a06d221ac75da7f6621ada3f2361cd24fde11ba79091e9f40efcf",
    "da3-base": "e01067dc1659613083d9145a9a2547ccdbe6ccbbf83c4fe7b3e8a4e2bdae78b5",
    "da3metric-large": (
        "bbea5b0b3ee389849cffa7ddae89de064a90abd2b055fc5aa99aac68db324776"
    ),
}
SDK_REVISION = "3d835ec1a5802d64a8b8b15f817a1ab54809bfe4"


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_depth(depth):
    array = np.asarray(depth, dtype=np.float32)
    if array.ndim != 2 or min(array.shape) < 32:
        raise ValueError("invalid_depth_shape")
    if not np.isfinite(array).all() or (array <= 0).any():
        raise ValueError("invalid_depth_values")
    return array


def resize_depth(depth, size):
    """Resize the whole uncropped view to RAW coordinates before scoring."""
    from PIL import Image

    depth = validate_depth(depth)
    result = Image.fromarray(depth).resize(size, resample=Image.Resampling.BILINEAR)
    return validate_depth(np.asarray(result))


def verify_sdk(sdk):
    revision = subprocess.check_output(
        ["git", "-C", str(sdk), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != SDK_REVISION:
        raise ValueError("sdk_revision_mismatch")
    changed = subprocess.check_output(
        [
            "git",
            "-C",
            str(sdk),
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            "src",
        ],
        text=True,
    ).strip()
    if changed:
        raise ValueError("sdk_source_modified")


class Candidate:
    """One model per benchmark process; official SDK network + input processor."""

    def __init__(self, name, path, device, resolution):
        import torch

        self.name, self.device, self.resolution = name, device, resolution
        self.path = Path(path)
        self.processor = None
        self.metadata = {
            "backend": name,
            "device": device,
            "process_res": resolution,
            "distance_measurement": False,
            "drive_supported": False,
            "files": {
                n: file_hash(self.path / n)
                for n in ("config.json", "model.safetensors")
            },
        }
        if name == "da2":
            self._load_da2()
        else:
            self._load_da3()
        self.model.eval().to(device)
        self.metadata["parameters"] = sum(p.numel() for p in self.model.parameters())
        self.metadata["torch"] = torch.__version__
        self.metadata["precision"] = (
            "bf16"
            if name != "da2" and device == "cuda" and torch.cuda.is_bf16_supported()
            else "fp32"
        )

    def _load_da2(self):
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        self.processor = AutoImageProcessor.from_pretrained(
            self.path, local_files_only=True
        )
        self.model = AutoModelForDepthEstimation.from_pretrained(
            self.path, local_files_only=True
        )
        # Retain production DA2 preprocessing, do not silently make it cheaper.
        self.metadata["process_res"] = "production_preprocessor"
        self.metadata["files"]["preprocessor_config.json"] = file_hash(
            self.path / "preprocessor_config.json"
        )

    def _load_da3(self):
        import depth_anything_3.registry as registry
        import torch
        from depth_anything_3.cfg import create_object, load_config
        from depth_anything_3.registry import MODEL_REGISTRY
        from depth_anything_3.utils.io.input_processor import InputProcessor
        from safetensors.torch import load_model

        sdk = Path(registry.__file__).resolve().parents[2]
        verify_sdk(sdk)
        if self.metadata["files"]["model.safetensors"] != CHECKPOINT_HASHES[self.name]:
            raise ValueError("checkpoint_hash_mismatch")
        config = json.loads((self.path / "config.json").read_text())
        if config["model_name"] != self.name:
            raise ValueError("model_variant_mismatch")
        self.model = create_object(load_config(MODEL_REGISTRY[self.name]))
        # Official Hub loading restores shared LayerNorm aliases deduplicated
        # by safetensors. Strict state_dict loading would misreport these as missing.
        wrapper = torch.nn.Module()
        wrapper.add_module("model", self.model)
        load_model(wrapper, str(self.path / "model.safetensors"), strict=True)
        self.processor = InputProcessor()
        self.metadata.update(
            {
                "sdk_revision": SDK_REVISION,
                "model_id": MODELS[self.name][0],
                "model_revision": MODELS[self.name][1],
                "sdk_entry": "native_network_and_input_processor",
                "resize_method": "upper_bound_resize",
            }
        )

    def infer(self, image):
        import torch

        assert self.processor is not None
        with torch.inference_mode():
            if self.name == "da2":
                inputs = self.processor(images=image, return_tensors="pt").to(
                    self.device
                )
                self.metadata["processed_shape"] = list(inputs["pixel_values"].shape)
                result = self.model(**inputs)
                depth = (
                    self.processor.post_process_depth_estimation(
                        result, target_sizes=[(image.height, image.width)]
                    )[0]["predicted_depth"]
                    .float()
                    .cpu()
                    .numpy()
                )
            else:
                return self.infer_window([image])
            return validate_depth(depth)

    def infer_window(self, images):
        """Causal multi-view window, newest last; no recurrent/streaming cache."""
        import torch

        if not 1 <= len(images) <= 5:
            raise ValueError("window_bounds")
        if self.name == "da2" and len(images) == 1:
            return self.infer(images[0])
        if len(images) > 1 and self.name not in {"da3-small", "da3-base"}:
            raise ValueError("backend_has_no_joint_view_attention")
        if len({image.size for image in images}) != 1:
            raise ValueError("window_image_size_changed")
        assert self.processor is not None
        with torch.inference_mode():
            batch, _, _ = self.processor(
                images,
                process_res=self.resolution,
                process_res_method="upper_bound_resize",
                num_workers=1,
                sequential=True,
                print_progress=False,
            )
            inputs = batch.unsqueeze(0).to(self.device)
            self.metadata["processed_shape"] = list(inputs.shape)
            enabled = self.device == "cuda" and torch.cuda.is_bf16_supported()
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=enabled):
                result = self.model(
                    inputs,
                    infer_gs=False,
                    use_ray_pose=False,
                    ref_view_strategy="first",
                )
            raw = result["depth"]
            if raw.ndim != 4 or raw.shape[:2] != (1, len(images)):
                raise ValueError("unexpected_sdk_output")
            return resize_depth(raw[0, -1].float().cpu().numpy(), images[-1].size)
