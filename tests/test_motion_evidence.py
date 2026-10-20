"""Synthetic offline sensor evidence, never a robot or control fixture."""

import copy
import hashlib
import json
import math

import pytest

from venom_module_rider.motion_evidence import (
    MotionEvidenceSettings,
    analyze_evidence,
    analyze_manifest,
)
from venom_module_rider.samples_archive import canonical


def record(index=0, timestamp=1.0, **overrides):
    value = {
        "schema_version": 1,
        "record_id": hashlib.sha256(str(index).encode()).hexdigest(),
        "kind": "imu",
        "device_id": "fixture",
        "source_session": "host-session",
        "source_instance_id": "source-boot",
        "source_observed_at": float(timestamp) if timestamp is not None else None,
        "source_clock_domain": "device-wall-unsynchronised",
        "received_mono": 1000.0 + index * 0.1,
        "received_at": 2000.0 + index * 0.1,
        "values": {"accel_x": 0.0, "accel_y": 0.0, "accel_z": 9.81, "yaw": 0.0},
        "units": {
            "accel_x": "m/s2",
            "accel_y": "m/s2",
            "accel_z": "m/s2",
            "yaw": "deg",
        },
        "quality": {
            "ready": True,
            "stale": False,
            "offline": False,
            "simulated": False,
            "physical_validation": "unverified",
            "sensors": {
                "yaw": {"ready": True, "unit": "deg", "observed_at": timestamp}
            },
        },
    }
    value.update(overrides)
    return value


def manifest(records=None):
    value = {
        "schema_version": 1,
        "kind": "rider-sample-session",
        "session": {"policy": {}},
        "samples": [],
    }
    if records is not None:
        value["evidence"] = {"schema_version": 1, "records": records, "status": {}}
    value["dataset_revision"] = hashlib.sha256(canonical(value)).hexdigest()
    value["exported_at"] = 1234.0
    return value


def yaw_settings(unit="deg"):
    return {"source_yaw_unit": unit, "source_yaw_unit_verified": True}


def test_gravity_norm_is_raw_and_never_integrated():
    rows = [record(index, index * 0.1) for index in range(5)]
    report = analyze_evidence(rows)
    assert report["status"] == "available"
    norm = report["acceleration"]["raw_norm"]
    assert norm["by_unit"][0]["distribution"]["mean"] == pytest.approx(9.81)
    assert norm["meaning"] == "raw_vector_norm_including_gravity_bias_and_noise"
    assert report["acceleration"]["gravity_compensated"]["status"] == "unavailable"
    for key in ("physical_velocity", "physical_distance"):
        assert report[key]["status"] == "unavailable"
        assert report[key]["value"] is None
    assert report["quality"]["physical_validation"] == {"unverified": 5}
    assert report["clocks"]["cross_clock_latency_s"] is None


def test_report_reproducible_and_input_not_modified():
    rows = [record()]
    original = copy.deepcopy(rows)
    first = analyze_evidence(rows)
    assert first == analyze_evidence(rows, MotionEvidenceSettings())
    assert rows == original
    changed = copy.deepcopy(rows)
    changed[0]["values"]["accel_x"] = 1.0
    assert (
        first["input"]["digest_sha256"]
        != analyze_evidence(changed)["input"]["digest_sha256"]
    )
    json.dumps(first, allow_nan=False)


def test_duplicate_record_ids_and_conflicts_remain_visible():
    first = record()
    conflict = copy.deepcopy(first)
    conflict["values"]["accel_x"] = 1.0
    report = analyze_evidence([first, first, conflict])
    assert report["input"]["records_unique"] == 1
    assert report["input"]["duplicate_record_ids"] == 2
    assert report["input"]["conflicting_record_ids"] == 1
    assert report["status"] == "partial"


def test_actual_source_cadence_not_host_clock_latency_or_historical_probe():
    rows = [record(index, 100.0 + index * 0.338) for index in range(30)]
    report = analyze_evidence(rows)
    clocks = report["clocks"]
    assert clocks["source_gaps_s"]["p50"] == pytest.approx(0.338)
    assert clocks["source_gaps_s"]["p95"] == pytest.approx(0.338)
    assert clocks["host_receipt_gaps_s"]["p50"] == pytest.approx(0.1)
    assert clocks["sampling"]["intervals_at_least_event_duration"] == 29
    assert clocks["sampling"]["short_event_may_be_entirely_missed"] is True
    assert clocks["sampling"]["classification"] == "unavailable"
    assert clocks["samples"]["unique_source_observations"] == 30


def test_repeated_out_of_order_reset_and_missing_source_times():
    rows = [
        record(index, ts) for index, ts in enumerate([1.0, 1.0, 0.5, 1.0, 3.0, None])
    ]
    clocks = analyze_evidence(rows)["clocks"]
    counts = clocks["samples"]
    assert counts["repeated_source_timestamps"] == 2
    assert counts["out_of_order_source_timestamps"] == 1
    assert counts["missing_source_timestamp"] == 1
    assert counts["source_gaps_over_contiguous_limit"] == 1
    assert clocks["source_gaps_s"]["max"] == 2.0
    assert clocks["source_gaps_s"]["count"] == 2


def test_no_gaps_or_rates_cross_instance_or_host_session_boundaries():
    rows = [
        record(0, 1.0),
        record(1, 1000.0, source_instance_id="new-boot"),
        record(2, 1001.0, source_instance_id="new-boot", source_session="new-host"),
    ]
    clocks = analyze_evidence(rows, yaw_settings())["clocks"]
    assert clocks["source_gaps_s"]["count"] == 0
    assert clocks["host_receipt_gaps_s"]["count"] == 0
    assert clocks["samples"]["identity_discontinuities"] == 2
    assert clocks["samples"]["source_instance_id_changes"] == 1
    assert clocks["samples"]["source_session_changes"] == 1
    assert clocks["yaw_angular_rate"]["status"] == "unavailable"


def test_unknown_instance_is_explicit_and_disables_derivative():
    rows = [
        record(index, 1 + index * 0.1, source_instance_id=None) for index in range(2)
    ]
    clocks = analyze_evidence(rows, yaw_settings())["clocks"]
    assert clocks["samples"]["missing_instance_id"] == 2
    assert clocks["yaw_angular_rate"]["excluded_pairs"] == {"instance_unknown": 1}


def test_angular_rate_requires_explicit_verified_unit_mapping():
    rows = [record(0, 1.0), record(1, 1.1)]
    assert (
        analyze_evidence(rows)["clocks"]["yaw_angular_rate"]["reason"]
        == "calibration_required"
    )
    for row in rows:
        row["units"]["yaw"] = "sdk_raw"
    rate = analyze_evidence(rows, yaw_settings())["clocks"]["yaw_angular_rate"]
    assert rate["status"] == "unavailable"
    assert rate["excluded_pairs"] == {"source_yaw_unit_unqualified": 1}


@pytest.mark.parametrize(
    "unit, first, second",
    [("deg", 179.0, -179.0), ("rad", math.radians(179), math.radians(-179))],
)
def test_synthetic_turn_unwraps_source_yaw_and_uses_channel_time(unit, first, second):
    rows = [record(0, 10.0), record(1, 10.2)]
    for row, yaw, ts in zip(rows, (first, second), (1.0, 1.1), strict=True):
        row["values"]["yaw"] = yaw
        row["units"]["yaw"] = unit
        row["quality"]["sensors"]["yaw"].update(unit=unit, observed_at=ts)
    rate = analyze_evidence(rows, yaw_settings(unit))["clocks"]["yaw_angular_rate"]
    assert rate["status"] == "available"
    assert rate["distribution"]["mean"] == pytest.approx(20.0)
    assert rate["meaning"] == "source_yaw_rate_not_verified_physical_heading_rate"


@pytest.mark.parametrize("flag", ["stale", "offline", "simulated"])
def test_bad_source_quality_still_has_raw_diagnostics_but_no_derivative(flag):
    rows = [record(0, 1.0), record(1, 1.1)]
    rows[1]["quality"][flag] = True
    report = analyze_evidence(rows, yaw_settings())
    assert report["channels"]
    assert report["quality"][flag]["true"] == 1
    assert report["clocks"]["yaw_angular_rate"]["status"] == "unavailable"


@pytest.mark.parametrize(
    "change, reason",
    [
        ("missing_time", "source_yaw_timestamp_missing"),
        ("repeated_time", "source_yaw_timestamp_nonincreasing"),
        ("stale_channel", "source_quality_unqualified"),
        ("wrapped_display", "source_yaw_quality_unqualified"),
        ("half_turn", "yaw_unwrap_ambiguous_or_nonfinite"),
    ],
)
def test_source_yaw_quality_and_unwrap_ambiguity(change, reason):
    rows = [record(0, 1.0), record(1, 1.1)]
    sensor = rows[1]["quality"]["sensors"]["yaw"]
    if change == "missing_time":
        del sensor["observed_at"]
    elif change == "repeated_time":
        sensor["observed_at"] = 1.0
    elif change == "stale_channel":
        sensor["stale"] = True
    elif change == "wrapped_display":
        sensor["wrapped_display"] = True
    elif change == "half_turn":
        rows[1]["values"]["yaw"] = 180.0
    rate = analyze_evidence(rows, yaw_settings())["clocks"]["yaw_angular_rate"]
    assert rate["status"] == "unavailable"
    assert rate["excluded_pairs"] == {reason: 1}


def test_large_source_gap_disables_derivative():
    clocks = analyze_evidence([record(0, 1.0), record(1, 3.0)], yaw_settings())[
        "clocks"
    ]
    assert clocks["yaw_angular_rate"]["excluded_pairs"] == {"source_gap": 1}


def test_missing_units_are_not_converted_and_mixed_vector_units_not_combined():
    rows = [record(0), record(1, 1.1)]
    rows[0]["units"] = {}
    rows[1]["units"]["accel_z"] = "sdk_raw"
    report = analyze_evidence(rows)
    norm = report["acceleration"]["raw_norm"]
    assert norm["by_unit"][0]["unit"] == "unknown"
    assert norm["mixed_unit_vectors"] == 1


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "1.0", True])
def test_invalid_numeric_rows_are_rejected_without_fabricated_measurements(bad):
    row = record()
    row["values"]["accel_x"] = bad
    report = analyze_evidence([row])
    assert report["status"] == "partial"
    assert report["input"]["records_rejected"] == 1
    assert report["counts"]["imu_records"] == 0
    assert report["physical_velocity"]["value"] is None
    json.dumps(report, allow_nan=False)


def test_finite_extremes_do_not_emit_nonfinite_derived_numbers():
    rows = [record(0, -1e308), record(1, 1e308)]
    for row in rows:
        row["values"] = {"accel_x": 1e308, "accel_y": 1e308, "accel_z": 1e308}
    report = analyze_evidence(rows)
    assert report["clocks"]["samples"]["nonfinite_source_intervals"] == 1
    json.dumps(report, allow_nan=False)


def test_zero_sdk_and_executor_accepted_do_not_prove_stillness_or_movement():
    imu = record(values={"accel_x": 0.0, "accel_y": 0.0, "accel_z": 0.0})
    executor = record(
        1,
        1.1,
        kind="executor",
        values={},
        units={},
        executor={"accepted": True, "output": {"linear": 1}},
    )
    report = analyze_evidence([imu, executor])
    assert report["counts"] == {"imu_records": 1, "executor_records": 1}
    assert report["physical_velocity"]["status"] == "unavailable"
    assert report["physical_distance"]["status"] == "unavailable"
    assert "stillness" not in report


def test_legacy_manifest_and_evidence_only_manifest_with_zero_images():
    legacy = analyze_manifest(manifest())
    assert legacy["status"] == "unavailable"
    assert legacy["reason"] == "legacy_archive_evidence_unavailable"
    source = manifest([record()])
    report = analyze_manifest(source)
    assert report["status"] == "available"
    assert report["input"]["dataset_revision"] == source["dataset_revision"]
    assert len(report["input"]["manifest_digest_sha256"]) == 64


def test_bad_manifest_hash_is_error_distinct_from_unavailable():
    source = manifest()
    source["dataset_revision"] = "0" * 64
    with pytest.raises(Exception, match="samples_import_hash"):
        analyze_manifest(source)


@pytest.mark.parametrize(
    "settings",
    [
        {"max_contiguous_gap_s": 0},
        {"event_duration_s": float("nan")},
        {"event_duration_s": True},
        {"source_yaw_unit": "sdk_raw"},
        {"source_yaw_unit_verified": True},
        {"unknown": True},
    ],
)
def test_invalid_settings_are_errors(settings):
    with pytest.raises(ValueError, match="motion_settings_invalid"):
        analyze_evidence([], settings)


def test_record_container_and_count_limits():
    with pytest.raises(ValueError, match="motion_records_invalid"):
        analyze_evidence({})
    with pytest.raises(ValueError, match="motion_records_limit"):
        analyze_evidence([{}] * 2049)


def test_no_network_or_control_imports():
    import ast
    import inspect

    import venom_module_rider.motion_evidence as module

    tree = ast.parse(inspect.getsource(module))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    imported |= {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert (
        not {"socket", "httpx", "requests", "urllib.request", "service", "client"}
        & imported
    )


def test_channel_quality_is_preserved_separately_from_source_quality():
    row = record()
    row["quality"]["sensors"]["yaw"]["stale"] = True
    report = analyze_evidence([row])
    yaw = next(channel for channel in report["channels"] if channel["channel"] == "yaw")
    assert yaw["quality"]["stale"]["true"] == 1
    assert report["quality"]["stale"]["false"] == 1


def test_nonincreasing_host_receipt_excludes_yaw_derivative():
    rows = [record(0, 1.0), record(1, 1.1, received_mono=999.0)]
    clocks = analyze_evidence(rows, yaw_settings())["clocks"]
    assert clocks["samples"]["nonincreasing_host_receipts"] == 1
    assert clocks["source_gaps_s"]["count"] == 1
    assert clocks["yaw_angular_rate"]["excluded_pairs"] == {
        "host_receipt_nonincreasing": 1
    }


@pytest.mark.parametrize(
    "instances",
    [
        ("other-boot", "other-boot"),
        ("source-boot", "other-boot"),
        (None, "other-boot"),
        ("", "source-boot"),
    ],
)
def test_explicit_yaw_producer_must_agree_with_source_instance(instances):
    rows = [record(0, 1.0), record(1, 1.1)]
    for row, instance in zip(rows, instances, strict=True):
        if instance is not None:
            row["quality"]["sensors"]["yaw"]["instance_id"] = instance
    rate = analyze_evidence(rows, yaw_settings())["clocks"]["yaw_angular_rate"]
    assert rate["status"] == "unavailable"
    assert rate["excluded_pairs"] == {"source_yaw_instance_mismatch": 1}


def test_matching_explicit_yaw_producer_allows_qualified_rate():
    rows = [record(0, 1.0), record(1, 1.1)]
    for row in rows:
        row["quality"]["sensors"]["yaw"]["instance_id"] = "source-boot"
    rate = analyze_evidence(rows, yaw_settings())["clocks"]["yaw_angular_rate"]
    assert rate["status"] == "available"


@pytest.mark.parametrize("timestamps", [[1.0, 1.1], [1.0, None, 1.1], [None], []])
def test_short_retained_intervals_do_not_establish_event_coverage(timestamps):
    rows = [record(index, stamp) for index, stamp in enumerate(timestamps)]
    sampling = analyze_evidence(rows)["clocks"]["sampling"]
    assert sampling["intervals_at_least_event_duration"] == 0
    assert sampling["short_event_may_be_entirely_missed"] is None
    assert sampling["event_coverage"] == "unverified"


def test_identity_boundary_does_not_establish_short_event_coverage():
    rows = [record(0, 1.0), record(1, 1.1), record(2, 3.0, source_session="new")]
    sampling = analyze_evidence(rows)["clocks"]["sampling"]
    assert sampling["short_event_may_be_entirely_missed"] is None


@pytest.mark.parametrize(
    "kind", ["string", "unicode", "list", "mapping", "depth", "cycle", "integer"]
)
def test_input_preflight_precedes_json_serialization(kind, monkeypatch):
    import venom_module_rider.motion_evidence as module

    row = record()
    limit = module.MAX_EVIDENCE_SESSION_BYTES
    if kind == "string":
        value = "x" * (limit + 1)
    elif kind == "unicode":
        value = "é" * (limit // 6 + 1)
    elif kind == "list":
        value = [None] * (limit // 2 + 1)
    elif kind == "mapping":
        monkeypatch.setattr(module, "MAX_EVIDENCE_SESSION_BYTES", 4096)
        value = {str(index): None for index in range(1000)}
    elif kind == "depth":
        value = None
        for _ in range(40):
            value = [value]
    elif kind == "cycle":
        value = []
        value.append(value)
    else:
        value = 1 << 100_000
    row["quality"]["extra"] = value
    original = module.json.dumps

    def guarded(value, *args, **kwargs):
        if value is row:
            raise AssertionError("unbounded record reached JSON serialization")
        return original(value, *args, **kwargs)

    monkeypatch.setattr(module.json, "dumps", guarded)
    with pytest.raises(ValueError, match="motion_(records_limit|input_invalid)"):
        analyze_evidence([row])


def test_entire_session_budget_preflight_precedes_serialization(monkeypatch):
    import venom_module_rider.motion_evidence as module

    rows = [record(0), record(1, 1.1)]
    for row in rows:
        row["quality"]["extra"] = "x" * (module.MAX_EVIDENCE_SESSION_BYTES // 2)
    original = module.json.dumps

    def guarded(value, *args, **kwargs):
        if any(value is row for row in rows):
            raise AssertionError("over-budget session reached JSON serialization")
        return original(value, *args, **kwargs)

    monkeypatch.setattr(module.json, "dumps", guarded)
    with pytest.raises(ValueError, match="motion_records_limit"):
        analyze_evidence(rows)


def test_invalid_nan_input_digest_retains_existing_deterministic_spelling():
    row = record()
    row["values"]["accel_x"] = float("nan")
    expected = hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    ).hexdigest()
    first = analyze_evidence([row])
    second = analyze_evidence([row])
    assert first["input"]["digest_sha256"] == expected
    assert first["input"]["digest_sha256"] == second["input"]["digest_sha256"]
    assert first["input"]["records_rejected"] == 1


def test_repeated_container_references_are_not_cycles():
    row = record()
    shared = [None, True, 1, "value"]
    row["quality"]["first"] = shared
    row["quality"]["second"] = shared
    assert analyze_evidence([row])["input"]["records_valid"] == 1


@pytest.mark.parametrize(
    "value",
    [
        "".join(chr(code) for code in range(128)),
        "é漢😀\ud800",
        [
            None,
            True,
            False,
            0,
            -1,
            10**100,
            1e100,
            -0.0,
            float("nan"),
            float("inf"),
            -float("inf"),
        ],
        {"nested": [[], {}, {"": "value"}]},
    ],
)
def test_json_preflight_exact_encoded_byte_boundary(value, monkeypatch):
    import venom_module_rider.motion_evidence as module

    row = {"value": value}
    encoded_size = len(json.dumps(row, sort_keys=True, separators=(",", ":")).encode())
    monkeypatch.setattr(module, "MAX_EVIDENCE_SESSION_BYTES", encoded_size)
    module._preflight_records([row])
    monkeypatch.setattr(module, "MAX_EVIDENCE_SESSION_BYTES", encoded_size - 1)
    with pytest.raises(ValueError, match="motion_records_limit"):
        module._preflight_records([row])
