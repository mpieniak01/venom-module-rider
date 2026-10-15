"""Bounded floor-perception decisions; no hardware or LLM access."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .depth_detector import PerceptionSettings


class VisionFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    protocol_version: Literal[1]
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

    perception: PerceptionSettings = Field(default_factory=PerceptionSettings)

    def valid_corridor(self):
        x0, y0, x1, y1 = self.corridor
        return 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
