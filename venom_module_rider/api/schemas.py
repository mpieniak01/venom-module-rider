"""Only manual commands and explicitly enumerated Pi operations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Selection(StrictModel):
    device_id: str | None


class Command(StrictModel):
    action: Literal["arm", "disarm", "stop", "clear_estop", "move", "height", "balance"]
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
    owner: Literal["manual"] | None = None
    arming_mode: Literal["operator"] | None = None
    direction: Literal["forward", "backward", "left", "right"] | None = None
    mode: Literal["pulse", "hold", "release"] | None = None
    device_settings: StrictBool | None = None
    height: StrictInt | None = None
    enabled: StrictBool | None = None


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
