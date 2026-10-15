"""Relative ground geometry for accepted, supervised forward trials."""

import hashlib
import io
import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class PerceptionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    engine: str = Field(default="segformer", pattern=r"^(segformer|depth_plane)$")
    residual_limit: float = Field(default=0.06, ge=0.01, le=0.15)
    outlier_limit: float = Field(default=0.005, ge=0.0, le=0.02)
    slope_min: float = Field(default=2.0, ge=1.0, le=4.0)


def assess_depth(depth, corridor, settings):
    """Fit with broad support; score the STOP zone. No physical distance."""
    import numpy as np

    if depth.ndim != 2 or min(depth.shape) < 32:
        raise ValueError("vision_model_output")
    h, w = depth.shape
    x0, y0, x1, y1 = corridor
    if not 0 <= x0 < x1 <= 1 or not 0 <= y0 < y1 <= 1:
        raise ValueError("vision_corridor_invalid")
    y, x = np.mgrid[0:h, 0:w]
    mask = (x >= x0 * w) & (x < x1 * w) & (y >= y0 * h) & (y < y1 * h)
    if mask.sum() < 64 or not np.isfinite(depth).all() or (depth <= 0).any():
        raise ValueError("vision_model_output")
    # A shorter STOP horizon must not also shrink the plane-fitting support:
    # a flat obstacle could otherwise become the fitted ground. Keep at least
    # the original 30% image-height support, while scoring only the chosen zone.
    reference_y0 = min(y0, max(0.0, round(y1 - 0.30, 12)))
    reference = (x >= x0 * w) & (x < x1 * w) & (y >= reference_y0 * h) & (y < y1 * h)
    inverse_reference = 1.0 / depth[reference]
    scale = float(np.median(inverse_reference))
    design = np.column_stack(
        (x[reference] / w, y[reference] / h, np.ones(reference.sum()))
    )
    coefficients = np.linalg.lstsq(design, inverse_reference, rcond=None)[0]
    scoring_design = np.column_stack((x[mask] / w, y[mask] / h, np.ones(mask.sum())))
    # Positive residuals are nearer than the fitted plane; negative ones are
    # farther away. Keep both: a recess/drop-off must not become a clear path.
    signed_errors = (1.0 / depth[mask] - scoring_design @ coefficients) / scale
    errors = np.abs(signed_errors)
    slope = float(coefficients[1] / scale)
    outliers = float((errors > settings.residual_limit).mean())
    reference_errors = np.abs(inverse_reference - design @ coefficients) / scale
    residual = float(np.quantile(errors, 0.99))
    if not settings.slope_min <= slope <= 6.0:
        state, reason = "unknown", "depth_ground_orientation"
    elif outliers > settings.outlier_limit:
        state, reason = "obstacle", "depth_ground_deviation"
    else:
        state, reason = "clear", "depth_plane_candidate"
    error_map = np.full(depth.shape, np.nan)
    error_map[mask] = errors
    return {
        "state": state,
        "reason": reason,
        "perception_engine": "depth_plane",
        "plane_residual_p99": residual,
        "plane_slope": slope,
        "plane_reference_corridor": (x0, reference_y0, x1, y1),
        "outlier_fraction": outliers,
        "reference_outlier_fraction": float(
            (reference_errors > settings.residual_limit).mean()
        ),
        "nearer_outlier_fraction": float(
            (signed_errors > settings.residual_limit).mean()
        ),
        "farther_outlier_fraction": float(
            (signed_errors < -settings.residual_limit).mean()
        ),
        "residual_limit": settings.residual_limit,
        "outlier_limit": settings.outlier_limit,
        "slope_min": settings.slope_min,
        "drive_supported": True,
        "distance_measurement": False,
        "_errors": error_map,
    }


class DepthDetector:
    def __init__(self):
        self.path = Path(
            os.getenv(
                "RIDER_DEPTH_MODEL",
                str(Path(__file__).resolve().parent.parent / "data/depth-small"),
            )
        )
        self.model = None
        self.processor = None
        self.digest = ""
        self.device = "cpu"

    def status(self):
        manifest = self.path / "manifest.json"
        return {
            "configured": manifest.is_file(),
            "loaded": self.model is not None,
            "model": "depth-anything-v2-indoor-small",
            "sha256": self.digest,
            "purpose": "relative_ground_geometry",
            "distance_measurement": False,
            "drive_supported": True,
        }

    def _load(self):
        if self.model is not None:
            return
        import torch
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        manifest = json.loads((self.path / "manifest.json").read_text())
        required = ("config.json", "preprocessor_config.json", "model.safetensors")
        if set(manifest["files"]) != set(required):
            raise ValueError("vision_model_manifest")
        for name in required:
            digest = hashlib.sha256((self.path / name).read_bytes()).hexdigest()
            if digest != manifest["files"][name]:
                raise ValueError("vision_model_hash_mismatch")
        self.digest = hashlib.sha256(
            json.dumps(manifest["files"], sort_keys=True).encode()
        ).hexdigest()
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.processor = AutoImageProcessor.from_pretrained(
            self.path, local_files_only=True
        )
        self.model = (
            AutoModelForDepthEstimation.from_pretrained(
                self.path, local_files_only=True
            )
            .eval()
            .to(self.device)
        )

    def detect(self, payload, corridor, settings):
        import numpy as np
        import torch
        from PIL import Image, ImageDraw

        if not payload or len(payload) > 512 * 1024:
            raise ValueError("vision_image_bounds")
        with Image.open(io.BytesIO(payload)) as opened:
            if opened.format not in {"JPEG", "PNG"} or not (
                32 <= opened.width <= 1280 and 32 <= opened.height <= 720
            ):
                raise ValueError("vision_image_bounds")
            image = opened.convert("RGB")
        rgb = np.asarray(image, dtype=np.float32) / 255
        if rgb.mean() < 0.04 or rgb.std() < 0.01:
            # Publish the actual rejected capture, never retain a previous green mask.
            stream = io.BytesIO()
            image.save(stream, format="JPEG", quality=85)
            return {
                "state": "unknown",
                "reason": "vision_image_quality",
                "drive_supported": True,
                "distance_measurement": False,
                "_preview": stream.getvalue(),
            }
        self._load()
        processor, model = self.processor, self.model
        if processor is None or model is None:
            raise ValueError("vision_model_unconfigured")
        inputs = processor(images=image, return_tensors="pt").to(self.device)
        with torch.inference_mode():
            result = model(**inputs)
        depth = (
            processor.post_process_depth_estimation(
                result, target_sizes=[(image.height, image.width)]
            )[0]["predicted_depth"]
            .float()
            .cpu()
            .numpy()
        )
        assessment = assess_depth(depth, corridor, settings)
        errors = assessment.pop("_errors")
        colors = np.full((*depth.shape, 3), (255, 185, 35), dtype=np.uint8)
        colors[np.isfinite(errors) & (errors <= settings.residual_limit)] = (
            35,
            210,
            100,
        )
        colors[np.isfinite(errors) & (errors > settings.residual_limit)] = (240, 60, 65)
        if assessment["reason"] == "depth_ground_orientation":
            colors[np.isfinite(errors)] = (255, 185, 35)
        preview = Image.blend(image, Image.fromarray(colors), 0.4)
        x0, y0, x1, y1 = corridor
        draw = ImageDraw.Draw(preview)
        reference_y0 = assessment["plane_reference_corridor"][1]
        if reference_y0 < y0:
            # Dashed blue: fitting context, not an additional STOP zone.
            left, right = int(x0 * (image.width - 1)), int(x1 * (image.width - 1))
            top, bottom = (
                int(reference_y0 * (image.height - 1)),
                int(y0 * (image.height - 1)),
            )
            for pos in range(left, right, 10):
                draw.line(
                    (pos, top, min(pos + 5, right), top), fill=(80, 170, 255), width=2
                )
            for side in (left, right):
                for pos in range(top, bottom, 10):
                    draw.line(
                        (side, pos, side, min(pos + 5, bottom)),
                        fill=(80, 170, 255),
                        width=2,
                    )
        draw.rectangle(
            (
                int(x0 * (image.width - 1)),
                int(y0 * (image.height - 1)),
                int(x1 * (image.width - 1)),
                int(y1 * (image.height - 1)),
            ),
            outline="white",
            width=2,
        )
        stream = io.BytesIO()
        preview.save(stream, format="JPEG", quality=85)
        return {
            **assessment,
            "_preview": stream.getvalue(),
            "model_sha256": self.digest,
        }
