"""Shared, bounded host evidence contract for collection and offline reports."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EVIDENCE_SCHEMA_VERSION = 1
MAX_EVIDENCE_RECORD_BYTES = 8192
MAX_EVIDENCE_RAM_BYTES = 64 * 1024
MAX_EVIDENCE_RAM_RECORDS = 64
EVIDENCE_RAM_TTL_S = 30.0
MAX_EVIDENCE_SESSION_BYTES = 2 * 1024**2
MAX_EVIDENCE_SESSION_RECORDS = 2048
EVIDENCE_ASSOCIATION = "host-reception-window-unmatched-exposure"


class EvidenceRecord(BaseModel):
    """Original clocks and observations, without physical-motion assertions."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    schema_version: Literal[1] = 1
    record_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: Literal["imu", "executor"]
    device_id: str = Field(min_length=1, max_length=128)
    source_session: str = Field(min_length=1, max_length=128)
    source_instance_id: str | None = Field(default=None, max_length=128)
    source_observed_at: float | None = None
    source_clock_domain: Literal["device-wall-unsynchronised"] = (
        "device-wall-unsynchronised"
    )
    received_mono: float
    received_at: float
    values: dict[str, float | None] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)
    executor: dict[str, Any] | None = None


def validate_evidence(value: object) -> dict[str, Any]:
    """Validate both online admission and untrusted session imports identically."""
    result = EvidenceRecord.model_validate(value).model_dump(mode="json")
    encoded = json.dumps(result, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode()) > MAX_EVIDENCE_RECORD_BYTES:
        raise ValueError("evidence_record_size")
    return result
