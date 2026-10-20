"""Untrusted record boundaries shared by receiver and archive analysis."""

import pytest

from venom_module_rider.evidence_contract import validate_evidence


def record():
    return {
        "schema_version": 1,
        "record_id": "a" * 64,
        "kind": "imu",
        "device_id": "recorded",
        "source_session": "host",
        "source_observed_at": 10.0,
        "received_mono": 20.0,
        "received_at": 100.0,
        "values": {"accel_z": 9.81},
        "units": {"accel_z": "m/s^2"},
        "quality": {"physical_validation": "unverified"},
    }


def test_original_clock_and_quality_survive_canonical_validation():
    row = validate_evidence(record())
    assert row["source_clock_domain"] == "device-wall-unsynchronised"
    assert row["source_observed_at"] == 10 and row["received_mono"] == 20
    assert row["quality"]["physical_validation"] == "unverified"


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("kind", "command"),
        ("record_id", "bad"),
        ("source_observed_at", float("nan")),
        ("received_mono", float("inf")),
        ("values", {"accel_x": float("nan")}),
        ("quality", {"bad": float("nan")}),
        ("quality", {"too_large": "x" * 9000}),
    ],
)
def test_malformed_or_unbounded_record_cannot_enter_archives(field, value):
    row = record()
    row[field] = value
    with pytest.raises(ValueError):
        validate_evidence(row)
