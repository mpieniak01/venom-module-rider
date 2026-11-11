"""Versioned image envelopes: independent floor support and full maneuver masks.

Coordinates are image fractions, never a claim of metric physical clearance.
An operator calibration must cover the body sweep AND the terminal footprint.
"""

import hashlib
import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class CollisionGeometry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    version: Annotated[StrictInt, Field(ge=1, le=1)]
    far_width_ratio: float = Field(gt=0, le=1)
    left_shift: float = Field(ge=0, le=1)
    right_shift: float = Field(ge=0, le=1)
    end_fraction: float = Field(ge=0.05, le=0.5)
    reference_corridor: list[float] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def reference_valid(self):
        a, b, c, d = self.reference_corridor
        if not 0 <= a < c <= 1 or not 0 <= b < d <= 1:
            raise ValueError("navigation_collision_geometry_invalid")
        return self

    def validate_corridor(self, corridor):
        a, b, c, d = self.reference_corridor
        x0, y0, x1, y1 = corridor
        if not (a <= x0 < x1 <= c and b <= y0 < y1 <= d):
            raise ValueError("navigation_collision_reference_incomplete")
        if d - b + 1e-9 < min(0.30, y1) or c - a + 1e-9 < min(0.30, x1):
            raise ValueError("navigation_collision_reference_insufficient")

    def validate_input(self, region):
        if region is not None:
            a, b, c, d = region.bounds
            x0, y0, x1, y1 = self.reference_corridor
            if not (a <= x0 and b <= y0 and c >= x1 and d >= y1):
                raise ValueError("navigation_input_region_excludes_reference")


def proof(collision, corridor):
    value = {"collision": collision.model_dump(), "corridor": list(corridor)}
    # Normalize numeric representations exactly like the stdlib-only Pi peer.
    value["corridor"] = [float(v) for v in corridor]
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {"version": 1, "sha256": hashlib.sha256(encoded.encode()).hexdigest()}


def proof_matches(result, collision, corridor):
    actual = result.get("collision_geometry")
    if collision is None:
        return actual is None
    return (
        corridor is not None
        and isinstance(actual, dict)
        and type(actual.get("version")) is int
        and actual == proof(collision, corridor)
        and result.get("geometry") == "image_collision_corridor"
    )


def envelopes(collision, corridor, shape, *, near_fraction=0.35):
    """One set of masks for scoring, temporal policy and source-pixel outlines."""
    import numpy as np

    collision.validate_corridor(corridor)
    h, w = shape
    x0, y0, x1, y1 = corridor
    ys, xs = np.mgrid[:h, :w]
    center, width = (x0 + x1) / 2, x1 - x0
    progress = np.clip((y1 - ys / h) / (y1 - y0), 0, 1)
    travel = np.minimum(progress / (1 - collision.end_fraction), 1)
    half = width * (1 + (collision.far_width_ratio - 1) * travel) / 2
    active = (ys / h >= y0) & (ys / h < y1)
    result = {}
    for name, shift in (
        ("center", 0),
        ("left", -collision.left_shift),
        ("right", collision.right_shift),
    ):
        middle = center + shift * travel
        mask = active & (xs / w >= middle - half) & (xs / w < middle + half)
        terminal_half = width * collision.far_width_ratio / 2
        complete = (
            0 <= center + shift - terminal_half and center + shift + terminal_half <= 1
        )
        result[name] = {"mask": mask, "complete": complete}
    near = result["center"]["mask"].copy()
    rows = np.flatnonzero(near.any(axis=1))
    if rows.size:
        near[
            : max(
                int(rows[0]),
                int(rows[-1]) + 1 - max(1, round(len(rows) * near_fraction)),
            )
        ] = False
    result["guard"] = {"mask": near, "complete": True}
    return result


def assess_envelopes(depth, masks, coefficients, scale, settings, valid, observed):
    """Unobserved or clipped required pixels never become a clear corridor."""
    import numpy as np

    from .directional import score_mask

    result = {}
    for name, item in masks.items():
        mask = item["mask"]
        pixels = int(mask.sum())
        complete = bool(
            item["complete"]
            and not (mask & ~observed).any()
            and np.isfinite(depth[mask]).all()
            and (depth[mask] > 0).all()
        )
        row = {
            "geometry": "image_collision_corridor",
            "coverage_complete": complete,
            "zone_pixels": pixels,
        }
        if not complete or pixels < 64 or not valid:
            row.update(state="unknown", reason="collision_coverage_or_reference")
        else:
            row.update(score_mask(depth, mask, coefficients, scale, settings))
        result[name] = row
    return result


def mask_outline(mask):
    """Trace the actual raster support without rounding a separate ideal polygon."""
    import numpy as np

    h, w = mask.shape
    rows = np.flatnonzero(mask.any(axis=1))
    if not rows.size:
        return []
    # Retain near/far/cap transitions plus uniformly sampled rows, bounded for UI.
    indices = np.unique(np.linspace(0, len(rows) - 1, min(12, len(rows)), dtype=int))
    rows = rows[indices]
    left, right = [], []
    for y in rows:
        xs = np.flatnonzero(mask[y])
        left.append([int(xs[0]), int(y)])
        right.append([int(xs[-1]), int(y)])
    return [*left, *reversed(right), left[0]]


def display_geometry(masks):
    return {name: mask_outline(item["mask"]) for name, item in masks.items()}


def draw_overlay(draw, assessment, corridor, size):
    """Raster exports consume the same source-pixel descriptor as the web panel."""
    from .overlay_geometry import analysis_geometry

    geometry = assessment.get("overlay_geometry")
    if geometry is None:
        geometry = analysis_geometry(size, assessment, corridor)
        assessment["overlay_geometry"] = geometry
    for side, points in geometry.get("paths", {}).items():
        if len(points) > 1:
            draw.line(
                [tuple(point) for point in points],
                fill=(255, 255, 255) if side == "center" else (72, 211, 232),
                width=2,
            )
    near = geometry.get("near", [])
    if len(near) > 1:
        draw.line([tuple(point) for point in near], fill=(255, 185, 35), width=2)
    a, b, c, d = geometry["reference_bounds"]
    draw.rectangle((a, b, c - 1, d - 1), outline=(80, 170, 255), width=1)
