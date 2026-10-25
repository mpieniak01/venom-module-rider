"""Versioned source-space DA2 input; omission preserves the full legacy view."""

import math
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class InputRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    version: Annotated[StrictInt, Field(ge=1, le=1)] = 1
    bounds: list[float] = Field(default_factory=lambda: [0.0, 0.4, 1.0, 1.0])

    @model_validator(mode="after")
    def normalized(self):
        if len(self.bounds) != 4:
            raise ValueError("navigation_input_region_invalid")
        a, b, c, d = self.bounds
        if not 0 <= a < c <= 1 or not 0 <= b < d <= 1:
            raise ValueError("navigation_input_region_invalid")
        return self

    @property
    def full(self):
        return self.bounds == [0.0, 0.0, 1.0, 1.0]

    def covers(self, corridor):
        x0, y0, x1, y1 = corridor
        top = min(y0, max(0.0, round(y1 - 0.30, 12)))
        a, b, c, d = self.bounds
        return a <= x0 and b <= top and c >= x1 and d >= y1

    def pixels(self, size):
        w, h = size
        a, b, c, d = self.bounds
        box = math.floor(a * w), math.floor(b * h), math.ceil(c * w), math.ceil(d * h)
        if box[2] - box[0] < 32 or box[3] - box[1] < 32:
            raise ValueError("vision_input_region_too_small")
        return box


def input_box(image, region):
    return region.pixels(image.size) if region is not None else (0, 0, *image.size)


def restore_depth(depth, size, box):
    """Restore coordinates, never synthesize depth for an unobserved pixel."""
    import numpy as np

    w, h = size
    a, b, c, d = box
    if (
        depth.shape != (d - b, c - a)
        or not np.isfinite(depth).all()
        or (depth <= 0).any()
    ):
        raise ValueError("vision_model_output")
    if box == (0, 0, w, h):
        return depth
    result = np.full((h, w), np.nan, dtype=depth.dtype)
    result[b:d, a:c] = depth
    return result
