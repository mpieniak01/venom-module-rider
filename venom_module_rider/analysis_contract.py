"""Offline requests select approved profiles and data, never actuator settings."""

from typing import Annotated, Literal

from pydantic import Field, StrictFloat, StrictInt, field_validator

from .api.schemas import StrictModel
from .depth_detector import PerceptionSettings
from .samples_contract import RecordID, RequestID


class AnalysisSpec(StrictModel):
    """One explicit input window and immutable model/geometry configuration."""

    profile: RequestID = "da2-default"
    corridor: tuple[StrictFloat, StrictFloat, StrictFloat, StrictFloat] = (
        0.35,
        0.80,
        0.65,
        0.95,
    )
    perception: PerceptionSettings = PerceptionSettings(engine="depth_plane")
    quality_mode: Literal["production", "model_probe"] = "production"
    window: Literal[1, 3, 5] = 1
    offset: Annotated[StrictInt, Field(ge=0, le=999)] = 0
    count: Annotated[StrictInt, Field(ge=1, le=200)] = 200
    threads: Annotated[StrictInt, Field(ge=1, le=2)] = 2

    @field_validator("window", mode="before")
    @classmethod
    def explicit_window(cls, value):
        if type(value) is not int:
            raise ValueError("analysis_window_invalid")
        return value

    @field_validator("corridor")
    @classmethod
    def normalized_zone(cls, value):
        x0, y0, x1, y1 = value
        if not 0 <= x0 < x1 <= 1 or not 0 <= y0 < y1 <= 1:
            raise ValueError("analysis_corridor_invalid")
        return value

    @field_validator("perception")
    @classmethod
    def depth_only(cls, value):
        if value.engine != "depth_plane":
            raise ValueError("analysis_engine_unsupported")
        return value


class AnalysisCreate(StrictModel):
    """Idempotent analysis intent; no model URL or filesystem path is accepted."""

    request_id: RequestID
    session_id: RecordID
    spec: AnalysisSpec = AnalysisSpec()
