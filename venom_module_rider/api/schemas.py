"""Only manual commands and explicitly enumerated Pi operations."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ConnectionRefresh(StrictModel):
    pass


class Selection(StrictModel):
    device_id: str | None


class Command(StrictModel):
    action: Literal[
        "arm",
        "disarm",
        "stop",
        "clear_estop",
        "move",
        "height",
        "balance",
        "navigation_pause",
        "navigation_resume",
    ]
    panel_id: (
        Annotated[str, Field(strict=True, min_length=1, max_length=128)] | None
    ) = None
    session: str | None = None
    request_id: str | None = None
    source: Literal["manual"] = "manual"
    execution_context: Literal["panel"] = "panel"
    generation: str | None = None
    seq: StrictInt | None = None
    ts: float | None = None
    ttl_ms: StrictInt | None = None
    settings_revision: StrictInt | None = None
    operator_consent: StrictBool | None = None
    operator_plan: str | None = None
    owner: Literal["manual", "navigation"] | None = None
    arming_mode: Literal["operator", "timed"] | None = None
    lease_s: float | None = None
    direction: Literal["forward", "backward", "left", "right"] | None = None
    mode: Literal["pulse", "hold", "release"] | None = None
    device_settings: StrictBool | None = None
    intervene: StrictBool | None = None
    supersedes_operation_id: (
        Annotated[str, Field(strict=True, min_length=1, max_length=128)] | None
    ) = None
    height: StrictInt | None = None
    enabled: StrictBool | None = None

    @field_validator("panel_id", "supersedes_operation_id")
    @classmethod
    def nonblank_panel(cls, value):
        if value is not None and not value.strip():
            raise ValueError("invalid_panel_id")
        return value

    @model_validator(mode="after")
    def supersession_scope(self):
        if self.supersedes_operation_id is not None and not (
            self.action == "navigation_pause"
            or self.action == "move"
            and self.intervene is True
        ):
            raise ValueError("operator_supersession_scope")
        return self


class SettingsPatch(StrictModel):
    revision: StrictInt
    values: dict
    directions: dict | None = None
    profile: dict | None = None


class ResourceAction(StrictModel):
    action: Literal["on", "off", "blink", "auto", "stop", "release"]
    pids: list[StrictInt] | None = None
    units: list[str] | None = None
    if_generation: StrictInt | None = None


class GoalAction(StrictModel):
    enabled: StrictBool
    options: dict = {}
    request_id: str | None = None
    source: Literal["manual", "follow_me", "navigation"] | None = None
    execution_context: Literal["panel"] | None = None
    generation: str | None = None
    seq: StrictInt | None = None
    ts: float | None = None
    ttl_ms: StrictInt | None = None
    settings_revision: StrictInt | None = None


class NavigationTrialRequest(StrictModel):
    panel_id: (
        Annotated[str, Field(strict=True, min_length=1, max_length=128)] | None
    ) = None
    operator_control_version: Annotated[StrictInt, Field(ge=1, le=1)] | None = None

    @model_validator(mode="after")
    def operator_panel(self):
        if self.operator_control_version is not None and (
            self.panel_id is None or not self.panel_id.strip()
        ):
            raise ValueError("operator_panel_required")
        return self

    request_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    generation: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    ts: Annotated[StrictFloat, Field(ge=0)]
    ttl_ms: Annotated[StrictInt, Field(ge=1, le=500)]
    config_revision: Annotated[StrictInt, Field(ge=0)]
    settings_revision: Annotated[StrictInt, Field(ge=0)]
    operator_consent: StrictBool
    operator_plan: Annotated[str, Field(strict=True, min_length=1, max_length=512)]
    source: Literal["manual"]
    execution_context: Literal["panel"]

    @field_validator("operator_consent")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("explicit operator consent required")
        return value

    @field_validator("operator_plan")
    @classmethod
    def specific_plan(cls, value):
        if not value.strip():
            raise ValueError("specific operator plan required")
        return value
