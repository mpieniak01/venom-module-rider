"""Strict host event payloads. No transport, permissions or actuator policies."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class MotionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class DirectionValues(MotionModel):
    speed: float
    duration: float
    minimum: float
    maximum: float
    step: float


class MotionProfile(MotionModel):
    type: Literal["profile"] = "profile"
    request_revision: StrictInt | None
    motion_revision: StrictInt | None
    profile_id: str | None = Field(max_length=128)
    values: dict[str, float]
    directions: dict[str, DirectionValues]
    height: float | None
    balance: bool | None
    settings_cache_age_s: float | None
    qualification: Literal["request-revision-match", "unverified"]
    timing_revision: StrictInt
    timing: dict[str, StrictInt]


class MotionIntent(MotionModel):
    action: str = Field(min_length=1, max_length=64)
    request_id: str | None = Field(default=None, max_length=128)
    panel_id: str | None = Field(default=None, max_length=128)
    supersedes_operation_id: str | None = Field(default=None, max_length=128)
    generation: str | None = Field(default=None, max_length=128)
    seq: StrictInt | None = None
    direction: Literal["forward", "backward", "left", "right"] | None = None
    mode: Literal["pulse", "hold", "release"] | None = None
    turn_mode: Literal["in_place"] | None = None
    intensity: float | None = None
    duration_s: float | None = None
    settings_revision: StrictInt | None = None


class MotionCommand(MotionModel):
    type: Literal["command"] = "command"
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    phase: Literal["requested", "result"]
    intent: MotionIntent
    profile_ref: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    outcome: Literal["pending", "accepted", "rejected", "cancelled", "unknown"]
    reason: str | None = Field(default=None, max_length=128)
    ack_status: str | None = Field(default=None, max_length=64)
