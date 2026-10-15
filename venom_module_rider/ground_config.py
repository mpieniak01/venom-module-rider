"""Pi-owned projection calibration; None is unknown, never an invented camera."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GroundGeometry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    mode: Literal["ground", "image"] = "ground"
    fx: float | None = Field(default=None, ge=0.1, le=5.0)
    fy: float | None = Field(default=None, ge=0.1, le=5.0)
    cx: float = Field(default=0.5, ge=0.0, le=1.0)
    cy: float = Field(default=0.5, ge=0.0, le=1.0)
    # Circumscribed camera-footpoint-to-body radius / MINIMUM camera height.
    # A conservative bound for all accepted heights, not an estimated distance.
    radius_ratio: float | None = Field(default=None, ge=0.05, le=5.0)
    lookahead_ratio: float = Field(default=2.0, ge=0.25, le=8.0)
    max_heading_deg: float = Field(default=45.0, ge=5.0, le=70.0)

    def calibrated(self):
        return all(value is not None for value in (self.fx, self.fy, self.radius_ratio))
