"""Bounded floor-perception decisions; no hardware or LLM access."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_serializer,
    model_validator,
)

from .avoidance import AvoidanceSettings, MotionPhase
from .depth_detector import PerceptionSettings


class BehaviorSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Annotated[StrictInt, Field(ge=1, le=1)] = 1
    profile_id: Literal["legacy", "cautious", "balanced", "fluid", "room_fluid"] = (
        "legacy"
    )
    profile_revision: Annotated[StrictInt, Field(ge=1, le=2)] = 1
    overrides: dict = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def serialize_selection(self, handler):
        value = handler(self)
        if not self.overrides:
            value.pop("overrides", None)
        return value

    @model_validator(mode="after")
    def validate_definition(self):
        from .navigation_profiles import validate_behavior

        validate_behavior(self.model_dump())
        return self


class JourneyContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Annotated[StrictInt, Field(ge=1, le=1)]
    id: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S+$")]
    maneuver_epoch: Annotated[StrictInt, Field(ge=0, le=2**53 - 1)]


class VisionFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    protocol_version: Literal[1, 2, 3]
    run_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]
    frame_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]
    sequence: Annotated[StrictInt, Field(ge=0, le=2**53 - 1)]
    config_revision: Annotated[StrictInt, Field(ge=0)]
    age_ms: Annotated[float, Field(ge=0, le=500)]
    deadline_ms: Annotated[StrictInt, Field(ge=50, le=500)]
    mode: Literal["observe", "drive"] = "observe"
    corridor: tuple[float, float, float, float] = (0.35, 0.80, 0.65, 0.95)
    verified: StrictBool = False
    calibration_id: Annotated[str, Field(max_length=64)] = ""
    model_sha256: Annotated[str, Field(pattern=r"^$|^[a-f0-9]{64}$")] = ""

    avoidance: AvoidanceSettings = Field(default_factory=AvoidanceSettings)
    motion_phase: MotionPhase = "ready"
    journey: JourneyContext | None = None

    perception: PerceptionSettings = Field(default_factory=PerceptionSettings)
    behavior: BehaviorSettings = Field(default_factory=BehaviorSettings)

    @model_validator(mode="after")
    def avoidance_protocol(self):
        if self.avoidance.enabled and (
            self.protocol_version != self.avoidance.protocol_version
            or self.perception.engine != "depth_plane"
        ):
            raise ValueError("avoidance_requires_depth_protocol_v2_or_v3")
        if self.journey is not None and not (
            self.mode == "drive"
            and self.protocol_version == 3
            and self.avoidance.enabled
            and self.avoidance.sweep_verified
        ):
            raise ValueError("journey_requires_accepted_steering_v3")
        return self

    @model_validator(mode="after")
    def valid_model_input(self):
        """Reject crops incompatible with the active engine, corridor or ground mode."""
        region = self.perception.input_region
        if region is not None:
            if self.perception.engine != "depth_plane":
                raise ValueError("navigation_input_region_unsupported")
            if not region.covers(self.corridor):
                raise ValueError("navigation_input_region_excludes_reference")
            if (
                self.protocol_version == 3
                and self.avoidance.geometry.mode == "ground"
                and not region.full
            ):
                raise ValueError("navigation_input_region_requires_image_geometry")
        return self

    def valid_corridor(self):
        x0, y0, x1, y1 = self.corridor
        return 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
