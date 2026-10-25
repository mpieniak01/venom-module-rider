"""Offline requests select approved profiles and data, never actuator settings."""

from typing import Annotated, Literal

from pydantic import (
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)

from .api.schemas import StrictModel
from .depth_detector import PerceptionSettings
from .ground_config import GroundGeometry
from .samples_contract import RecordID, RequestID
from .vision_contract import BehaviorSettings


class AnalysisSpec(StrictModel):
    """One explicit input window and immutable model/geometry configuration."""

    input_source: Literal["samples", "video"] = "samples"
    profile: RequestID = "da2-default"
    corridor: tuple[StrictFloat, StrictFloat, StrictFloat, StrictFloat] = (
        0.35,
        0.80,
        0.65,
        0.95,
    )
    perception: PerceptionSettings = PerceptionSettings(engine="depth_plane")
    geometry: GroundGeometry = GroundGeometry(mode="image")
    quality_mode: Literal["production", "model_probe"] = "production"
    behavior: BehaviorSettings = Field(default_factory=BehaviorSettings)
    capture_evidence: StrictBool = False
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

    @model_validator(mode="after")
    def matching_engine(self):
        from .perception_catalog import FAST, SEGFORMER, YOLO

        if self.input_source == "video" and self.capture_evidence:
            raise ValueError("analysis_video_evidence_unsupported")
        semantic = self.profile in {SEGFORMER, YOLO, FAST}
        if semantic and "perception" not in self.model_fields_set:
            self.perception = self.perception.model_copy(update={"engine": "segformer"})
        expected = "segformer" if semantic else "depth_plane"
        if self.perception.engine != expected:
            raise ValueError("analysis_engine_unsupported")
        if semantic:
            if self.window != 1:
                raise ValueError("analysis_window_unsupported")
            if self.geometry.mode != "image":
                raise ValueError("analysis_geometry_unsupported")
            model_id = self.perception.model_id
            artifact = self.perception.artifact_sha256
            if (
                bool(model_id) != bool(artifact)
                or model_id
                and model_id != self.profile
            ):
                raise ValueError("analysis_model_identity_mismatch")
        elif self.perception.model_id or self.perception.artifact_sha256:
            raise ValueError("analysis_model_identity_unsupported")
        return self


class AnalysisCreate(StrictModel):
    """Idempotent analysis intent; no model URL or filesystem path is accepted."""

    request_id: RequestID
    session_id: RecordID
    spec: AnalysisSpec = AnalysisSpec()
