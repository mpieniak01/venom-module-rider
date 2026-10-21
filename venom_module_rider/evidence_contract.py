"""Shared, bounded host evidence contract for collection and offline reports."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .motion_contract import MotionCommand, MotionProfile

EVIDENCE_SCHEMA_VERSION = 1
MAX_EVIDENCE_RECORD_BYTES = 8192
MAX_EVIDENCE_RAM_BYTES = 64 * 1024
MAX_EVIDENCE_RAM_RECORDS = 64
EVIDENCE_RAM_TTL_S = 30.0
MAX_EVIDENCE_SESSION_BYTES = 16 * 1024**2
MAX_EVIDENCE_SESSION_RECORDS = 8192
EVIDENCE_ASSOCIATION = "host-reception-window-unmatched-exposure"


class EvidenceBase(BaseModel):
    """Original clocks and observations, without physical-motion assertions."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    record_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    device_id: str = Field(min_length=1, max_length=128)
    source_session: str = Field(min_length=1, max_length=128)
    source_instance_id: str | None = Field(default=None, max_length=128)
    source_observed_at: float | None = None
    received_mono: float
    received_at: float
    values: dict[str, float | None] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)
    executor: dict[str, Any] | None = None


class EvidenceRecord(EvidenceBase):
    schema_version: Literal[1] = 1
    kind: Literal["imu", "executor"]
    source_clock_domain: Literal["device-wall-unsynchronised"] = (
        "device-wall-unsynchronised"
    )


class HostEvidenceRecord(EvidenceBase):
    schema_version: Literal[2]
    kind: Literal["command", "profile"]
    source_clock_domain: Literal["host-monotonic"]
    host: MotionCommand | MotionProfile


def validate_evidence(value: object) -> dict[str, Any]:
    """Validate both online admission and untrusted session imports identically."""
    if not isinstance(value, dict) or type(value.get("schema_version", 1)) is not int:
        raise ValueError("evidence_record_invalid")
    # Validate arbitrary nested quality before Pydantic's JSON conversion can
    # normalise nonfinite values to null and lose the source error.
    try:
        original = json.dumps(value, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("evidence_record_invalid") from exc
    if len(original.encode()) > MAX_EVIDENCE_RECORD_BYTES:
        raise ValueError("evidence_record_size")
    model = HostEvidenceRecord if value.get("schema_version") == 2 else EvidenceRecord
    result = model.model_validate(value).model_dump(mode="json")
    if result["schema_version"] == 2:
        if (
            result["kind"] != result["host"]["type"]
            or result["source_observed_at"] is not None
            or result["values"]
            or result["units"]
            or result["executor"] is not None
        ):
            raise ValueError("evidence_host_invalid")
    encoded = json.dumps(result, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode()) > MAX_EVIDENCE_RECORD_BYTES:
        raise ValueError("evidence_record_size")
    return result
