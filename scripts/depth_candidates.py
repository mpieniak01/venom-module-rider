"""Offline-only depth backends. No robot transport or motion permission."""

import hashlib
import json
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
            "bf16" if device == "cuda" and torch.cuda.is_bf16_supported() else "fp32"
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
        import torch
        from depth_anything_3.cfg import create_object, load_config
        from depth_anything_3.registry import MODEL_REGISTRY
        from depth_anything_3.utils.io.input_processor import InputProcessor
        from safetensors.torch import load_model

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

        with torch.inference_mode():
            if self.name == "da2":
                inputs = self.processor(images=image, return_tensors="pt").to(
                    self.device
                )
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
                batch, _, _ = self.processor(
                    [image],
                    process_res=self.resolution,
                    process_res_method="upper_bound_resize",
                    num_workers=1,
                    sequential=True,
                    print_progress=False,
                )
                images = batch.unsqueeze(0).to(self.device)
                # Match official forward's GPU autocast; CPU stays fp32.
                enabled = self.device == "cuda" and torch.cuda.is_bf16_supported()
                with torch.autocast("cuda", dtype=torch.bfloat16, enabled=enabled):
                    result = self.model(images, infer_gs=False, use_ray_pose=False)
                raw = result["depth"]
                if raw.ndim != 4 or raw.shape[:2] != (1, 1):
                    raise ValueError("unexpected_sdk_output")
                depth = resize_depth(raw[0, 0].float().cpu().numpy(), image.size)
            return validate_depth(depth)
