"""Relative ground geometry for accepted, supervised forward trials."""

import hashlib
import io
import json
import math
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .directional import assess_paths, assess_sectors, sector_regions
from .vision_overlay import draw_sectors


class PerceptionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    engine: str = Field(default="segformer", pattern=r"^(segformer|depth_plane)$")
    model_id: str = Field(
        default="",
        pattern=r"^$|^(segformer-b0-ade20k|fast-scnn-floor|yolo26n-sem-ade20k|depth-anything-v2-indoor-small)$",
    )
    artifact_sha256: str = Field(default="", pattern=r"^$|^[a-f0-9]{64}$")
    residual_limit: float = Field(default=0.06, ge=0.01, le=0.15)
    outlier_limit: float = Field(default=0.005, ge=0.0, le=0.02)
    slope_min: float = Field(default=2.0, ge=1.0, le=4.0)

    semantic_models: dict = Field(default_factory=dict)

    @field_validator("semantic_models")
    @classmethod
    def validate_model_tuning(cls, value):
        from .perception_tuning import validate_semantic_models

        return validate_semantic_models(value)


def assess_depth(depth, corridor, settings, *, evidence=False):
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
    reference_fit = "least_squares"
    initial_errors = np.abs(inverse_reference - design @ coefficients) / scale
    if float((initial_errors > settings.residual_limit).mean()) > 0.20:
        from .image_ground_reference import recover

        recovered = recover(design, inverse_reference, scale, settings)
        if recovered is not None:
            coefficients = recovered
            reference_fit = "supported_trimmed_plane"
    scoring_design = np.column_stack((x[mask] / w, y[mask] / h, np.ones(mask.sum())))
    # Positive residuals are nearer than the fitted plane; negative ones are
    # farther away. Keep both: a recess/drop-off must not become a clear path.
    signed_errors = (1.0 / depth[mask] - scoring_design @ coefficients) / scale
    errors = np.abs(signed_errors)
    slope = float(coefficients[1] / scale)
    outlier_pixels = int((errors > settings.residual_limit).sum())
    zone_pixels = int(mask.sum())
    allowed_pixels = math.floor(settings.outlier_limit * zone_pixels)
    outliers = outlier_pixels / zone_pixels
    reference_errors = np.abs(inverse_reference - design @ coefficients) / scale
    residual = float(np.quantile(errors, 0.99))
    reference_outliers = float((reference_errors > settings.residual_limit).mean())
    # A single OLS plane supported by less than 80% of its own reference is
    # not reliable enough to classify other corridors or authorize steering.
    # This is a reference-quality gate, not a relaxed obstacle tolerance.
    reference_valid = reference_outliers <= 0.20
    oriented = settings.slope_min <= slope <= 6.0
    if not reference_valid:
        state, reason = "unknown", "ground_reference_uncertain"
    elif not oriented:
        state, reason = "unknown", "depth_ground_orientation"
    elif outlier_pixels > allowed_pixels:
        state, reason = "obstacle", "depth_ground_deviation"
    else:
        state, reason = "clear", "depth_plane_candidate"
    error_map = np.full(depth.shape, np.nan)
    error_map[mask] = errors
    assessment = {
        "state": state,
        "reason": reason,
        "sectors": assess_sectors(
            depth,
            corridor,
            coefficients,
            scale,
            settings,
            oriented and reference_valid,
        ),
        "paths": assess_paths(
            depth,
            corridor,
            coefficients,
            scale,
            settings,
            oriented and reference_valid,
        ),
        "perception_engine": "depth_plane",
        "plane_residual_p99": residual,
        "plane_slope": slope,
        "plane_reference_corridor": (x0, reference_y0, x1, y1),
        "outlier_fraction": outliers,
        "outlier_pixels": outlier_pixels,
        "zone_pixels": zone_pixels,
        "allowed_outlier_pixels": allowed_pixels,
        "nearer_outlier_pixels": int((signed_errors > settings.residual_limit).sum()),
        "farther_outlier_pixels": int((signed_errors < -settings.residual_limit).sum()),
        "reference_outlier_fraction": reference_outliers,
        "ground_reference_valid": reference_valid,
        "reference_fit": reference_fit,
        "reference_inlier_fraction": 1.0 - reference_outliers,
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
    if evidence:
        from .navigation_maps import depth_maps

        signed_map = np.full(depth.shape, np.nan)
        signed_map[mask] = signed_errors
        assessment["_navigation_maps"] = depth_maps(
            error_map,
            signed_map,
            settings.residual_limit,
            valid=reference_valid and oriented,
        )
    return assessment


def draw_stop_border(draw, shape, corridor):
    """Keep the inward PIL stroke outside every pixel of the scored mask."""
    h, w = shape
    x0, y0, x1, y1 = corridor
    left, top = math.ceil(x0 * w), math.ceil(y0 * h)
    right, bottom = math.ceil(x1 * w) - 1, math.ceil(y1 * h) - 1
    draw.rectangle((left - 2, top - 2, right + 2, bottom + 2), outline="white", width=2)


def decode_payload(payload):
    """Decode bounded bytes once, shared by live and stored-frame analysis."""
    from PIL import Image

    if not payload or len(payload) > 512 * 1024:
        raise ValueError("vision_image_bounds")
    with Image.open(io.BytesIO(payload)) as opened:
        if opened.format not in {"JPEG", "PNG"} or not (
            32 <= opened.width <= 1280 and 32 <= opened.height <= 720
        ):
            raise ValueError("vision_image_bounds")
        image = opened.convert("RGB")
    return image


def render_depth(image, depth, assessment, corridor, settings, geometry=None):
    """Render the same geometry overlay in live and offline analysis."""
    import numpy as np
    from PIL import Image, ImageDraw

    errors = assessment.pop("_errors")
    colors = np.full((*depth.shape, 3), (255, 185, 35), dtype=np.uint8)
    colors[np.isfinite(errors) & (errors <= settings.residual_limit)] = (
        35,
        210,
        100,
    )
    colors[np.isfinite(errors) & (errors > settings.residual_limit)] = (240, 60, 65)
    if assessment["state"] == "unknown":
        colors[np.isfinite(errors)] = (255, 185, 35)
    preview = Image.blend(image, Image.fromarray(colors), 0.4)
    x0, y0, x1, y1 = corridor
    draw = ImageDraw.Draw(preview)
    overlays = assessment.pop("_path_overlay", None)
    projected = assessment.get("geometry") == "ground_capsule"
    draw_sectors(
        draw,
        image.size,
        assessment.get("sectors", {}),
        outlines=overlays,
        projected=projected,
    )
    if not projected and (geometry is None or geometry.mode == "image"):
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
                    (pos, top, min(pos + 5, right), top),
                    fill=(80, 170, 255),
                    width=2,
                )
            for side in (left, right):
                for pos in range(top, bottom, 10):
                    draw.line(
                        (side, pos, side, min(pos + 5, bottom)),
                        fill=(80, 170, 255),
                        width=2,
                    )
        draw_stop_border(draw, depth.shape, corridor)
    stream = io.BytesIO()
    preview.save(stream, format="JPEG", quality=85)
    return stream.getvalue()


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
            "device": self.device,
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

    def detect(self, payload, corridor, settings, *, evidence=False):
        return self._detect(payload, corridor, settings, evidence=evidence)

    def detect_ground(self, payload, corridor, settings, geometry, *, evidence=False):
        return self._detect(payload, corridor, settings, geometry, evidence=evidence)

    def _detect(self, payload, corridor, settings, geometry=None, *, evidence=False):
        import numpy as np
        import torch

        image = decode_payload(payload)
        rgb = np.asarray(image, dtype=np.float32) / 255
        if rgb.mean() < 0.04 or rgb.std() < 0.01:
            # Publish the actual rejected capture, never retain a previous green mask.
            from PIL import ImageDraw

            projected = geometry is not None and geometry.mode == "ground"
            sectors = (
                {}
                if projected
                else {
                    side: {
                        "state": "unknown",
                        "corridor": bounds,
                        "coverage_complete": 0 <= bounds[0] < bounds[2] <= 1,
                    }
                    for side, bounds in sector_regions(corridor).items()
                }
            )
            draw = ImageDraw.Draw(image)
            draw_sectors(draw, image.size, sectors, projected=projected)
            if not projected:
                draw_stop_border(draw, (image.height, image.width), corridor)
            stream = io.BytesIO()
            image.save(stream, format="JPEG", quality=85)
            return {
                "state": "unknown",
                "reason": "vision_image_quality",
                "sectors": sectors,
                **({"geometry": "ground_capsule"} if projected else {}),
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
        if geometry is None or geometry.mode == "image":
            assessment = assess_depth(depth, corridor, settings, evidence=evidence)
        else:
            from .adaptive_ground import assess

            assessment = assess(depth, corridor, settings, geometry, evidence=evidence)
        if evidence and "_navigation_maps" in assessment:
            from .navigation_maps import source_gray

            assessment["_navigation_maps"]["gray"] = source_gray(image)
        preview = render_depth(image, depth, assessment, corridor, settings, geometry)
        return {
            **assessment,
            "_preview": preview,
            "model_sha256": self.digest,
            "_trace_depth": depth,
        }
