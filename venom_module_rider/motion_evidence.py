"""Bounded, deterministic offline descriptions of received sensor evidence.

This module has no device, network, control, inference or persistence dependency.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from .evidence_contract import (
    MAX_EVIDENCE_SESSION_BYTES,
    MAX_EVIDENCE_SESSION_RECORDS,
    validate_evidence,
)

REPORT_SCHEMA_VERSION = 1
ANALYZER_VERSION = "motion-evidence-v1.0.0"
MAX_REPORT_BYTES = 1024 * 1024
MAX_INPUT_DEPTH = 32
MAX_INPUT_INTEGER_BITS = 4096


@dataclass(frozen=True)
class MotionEvidenceSettings:
    """Analysis thresholds, never a device calibration or actuation policy.

    A verified yaw unit must refer to the original source yaw channel. sdk_raw
    is deliberately unsupported: a documented conversion must precede this API.
    """

    max_contiguous_gap_s: float = 1.0
    event_duration_s: float = 0.15
    source_yaw_unit: str | None = None
    source_yaw_unit_verified: bool = False


def _settings(value: MotionEvidenceSettings | dict | None) -> MotionEvidenceSettings:
    if value is None:
        result = MotionEvidenceSettings()
    elif isinstance(value, MotionEvidenceSettings):
        result = value
    elif isinstance(value, dict):
        try:
            result = MotionEvidenceSettings(**value)
        except TypeError as exc:
            raise ValueError("motion_settings_invalid") from exc
    else:
        raise ValueError("motion_settings_invalid")
    for number in (result.max_contiguous_gap_s, result.event_duration_s):
        if (
            type(number) not in (int, float)
            or not math.isfinite(number)
            or not 0 < number <= 3600
        ):
            raise ValueError("motion_settings_invalid")
    if (
        result.source_yaw_unit not in (None, "deg", "rad")
        or type(result.source_yaw_unit_verified) is not bool
        or (result.source_yaw_unit_verified and result.source_yaw_unit is None)
    ):
        raise ValueError("motion_settings_invalid")
    return result


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("motion_input_invalid") from exc


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[math.ceil(position)] * weight


def _distribution(values: list[float]) -> dict[str, Any]:
    return {
        "count": len(values),
        "min": min(values) if values else None,
        "p50": _percentile(values, 0.5),
        "p95": _percentile(values, 0.95),
        "max": max(values) if values else None,
        "mean": statistics.mean(values) if values else None,
    }


def _flag(record: dict, name: str) -> bool | None:
    quality = record["quality"]
    value = quality.get(name)
    if type(value) is bool:
        return value
    source = quality.get("source", {})
    value = source.get(name) if isinstance(source, dict) else None
    return value if type(value) is bool else None


def _channel_quality(record: dict, channel: str) -> dict:
    sensors = record["quality"].get("sensors", {})
    value = sensors.get(channel, {}) if isinstance(sensors, dict) else {}
    return value if isinstance(value, dict) else {}


def _fresh(record: dict, channel: str) -> bool:
    source = _channel_quality(record, channel)
    return (source.get("ready") is True or _flag(record, "ready") is True) and all(
        _flag(record, flag) is not True and source.get(flag) is not True
        for flag in ("stale", "offline", "simulated")
    )


def _yaw_source_time(record: dict) -> float | None:
    value = _channel_quality(record, "yaw").get("observed_at")
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    ):
        return value
    return None


def _identity(record: dict) -> tuple:
    return (
        record["device_id"],
        record["kind"],
        record["source_session"],
        record["source_instance_id"],
        record["source_clock_domain"],
    )


def _angle_reason(
    first: dict, second: dict, dt: float, settings: MotionEvidenceSettings
) -> str | None:
    """Return the first yaw-continuity qualification blocker."""
    if not settings.source_yaw_unit_verified:
        return "calibration_required"
    if dt > settings.max_contiguous_gap_s:
        return "source_gap"
    if first["source_instance_id"] is None or second["source_instance_id"] is None:
        return "instance_unknown"
    for record in (first, second):
        reason = _record_angle_reason(record, settings)
        if reason is not None:
            return reason
    first_time, second_time = _yaw_source_time(first), _yaw_source_time(second)
    if first_time is None or second_time is None:
        return "source_yaw_timestamp_missing"
    yaw_dt = second_time - first_time
    if not math.isfinite(yaw_dt) or yaw_dt <= 0:
        return "source_yaw_timestamp_nonincreasing"
    if yaw_dt > settings.max_contiguous_gap_s:
        return "source_yaw_gap"
    return None


def _yaw_rate(first: dict, second: dict, dt: float, unit: str) -> float | None:
    # Remainder unwrap assumes less than half a revolution between samples.
    period = 360.0 if unit == "deg" else 2 * math.pi
    a = first["values"]["yaw"]
    b = second["values"]["yaw"]
    difference = b - a
    if not math.isfinite(difference):
        return None
    delta = math.remainder(difference, period)
    # Exactly half a revolution has two equally valid unwrap directions.
    if math.isclose(abs(delta), period / 2, rel_tol=1e-12, abs_tol=1e-12):
        return None
    rate = delta / dt
    if unit == "rad":
        rate = math.degrees(rate)
    return rate if math.isfinite(rate) else None


def _preflight_records(records: list) -> None:
    """Bound exact compact ensure_ascii JSON bytes and traversal work."""
    budget = _JsonBudget()
    for record in records:
        budget.visit(record, 0)


def _validate_records(records: object) -> tuple[list[dict], dict]:
    if not isinstance(records, list):
        raise ValueError("motion_records_invalid")
    if len(records) > MAX_EVIDENCE_SESSION_RECORDS:
        raise ValueError("motion_records_limit")
    _preflight_records(records)
    # Invalid numeric records are rejected individually, with bounded input size.
    size = 0
    input_digest = hashlib.sha256()
    valid: list[dict] = []
    seen: dict[str, bytes] = {}
    counts: Counter = Counter()
    for record in records:
        try:
            encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise ValueError("motion_input_invalid") from exc
        input_digest.update(encoded + b"\n")
        size += len(encoded)
        if size > MAX_EVIDENCE_SESSION_BYTES:
            raise ValueError("motion_records_limit")
        try:
            row = validate_evidence(record)
        except (ValueError, TypeError):
            counts["records_rejected"] += 1
            continue
        counts["records_valid"] += 1
        encoded = _canonical(row)
        identity = row["record_id"]
        if identity in seen:
            counts["duplicate_record_ids"] += 1
            if seen[identity] != encoded:
                counts["conflicting_record_ids"] += 1
            continue
        seen[identity] = encoded
        valid.append(row)
    summary: dict[str, Any] = {
        name: counts[name]
        for name in (
            "records_valid",
            "records_rejected",
            "duplicate_record_ids",
            "conflicting_record_ids",
        )
    }
    summary.update(
        records_received=len(records),
        records_unique=len(valid),
        digest_sha256=input_digest.hexdigest(),
    )
    return valid, summary


def _quality_summary(records: list[dict], channel: str | None = None) -> dict:
    qualities = [
        _channel_quality(row, channel) if channel else row["quality"] for row in records
    ]
    result = {
        flag: {
            "true": sum(quality.get(flag) is True for quality in qualities),
            "false": sum(quality.get(flag) is False for quality in qualities),
            "unknown": sum(
                type(quality.get(flag)) is not bool for quality in qualities
            ),
        }
        for flag in ("ready", "stale", "offline", "simulated")
    }
    result["physical_validation"] = dict(
        Counter(
            str(quality.get("physical_validation", "unknown")) for quality in qualities
        )
    )
    return result


def _channel_report(records: list[dict]) -> tuple[list[dict], dict]:
    """Aggregate observed channels and raw vector norms without imputing data."""
    channels: dict[tuple[str, str], list[float]] = defaultdict(list)
    missing: Counter = Counter()
    channel_rows: dict[tuple[str, str], list[dict]] = defaultdict(list)
    norms: dict[str, list[float]] = defaultdict(list)
    incomplete = 0
    mixed_units = 0
    nonfinite = 0
    for record in records:
        if record["kind"] != "imu":
            continue
        for channel, value in record["values"].items():
            key = (channel, record["units"].get(channel, "unknown"))
            channels[key]  # Keep an explicit missing-only channel.
            channel_rows[key].append(record)
            if value is None:
                missing[key] += 1
            else:
                channels[key].append(value)
        a, b, c = _acceleration_norm(record, norms)
        incomplete += a
        mixed_units += b
        nonfinite += c
    channel_report = [
        {
            "channel": channel,
            "unit": unit,
            "missing_values": missing[(channel, unit)],
            "quality": _quality_summary(channel_rows[(channel, unit)], channel),
            "distribution": _distribution(values),
        }
        for (channel, unit), values in sorted(channels.items())
    ]
    raw_norm = {
        "status": "available" if norms else "unavailable",
        "meaning": "raw_vector_norm_including_gravity_bias_and_noise",
        "by_unit": [
            {"unit": unit, "distribution": _distribution(values)}
            for unit, values in sorted(norms.items())
        ],
        "incomplete_vectors": incomplete,
        "mixed_unit_vectors": mixed_units,
        "nonfinite_derived_vectors": nonfinite,
    }
    return channel_report, raw_norm


def _timing_report(records: list[dict], settings: MotionEvidenceSettings) -> dict:
    """Summarize source/receipt intervals and strictly qualified yaw derivatives."""
    source_gaps: list[float] = []
    receipt_gaps: list[float] = []
    angular_rates: list[float] = []
    counts: Counter = Counter()
    reasons: Counter = Counter()
    previous: dict[tuple, dict] = {}
    observed: set[tuple] = set()
    for record in records:
        _timing_observation(
            record,
            settings,
            (
                previous,
                observed,
                counts,
                reasons,
                source_gaps,
                receipt_gaps,
                angular_rates,
            ),
        )
    larger = sum(gap >= settings.event_duration_s for gap in source_gaps)
    return {
        "samples": {
            name: counts[name]
            for name in (
                "unique_source_observations",
                "missing_source_timestamp",
                "repeated_source_timestamps",
                "out_of_order_source_timestamps",
                "missing_instance_id",
                "identity_discontinuities",
                "source_session_changes",
                "source_instance_id_changes",
                "source_clock_domain_changes",
                "nonincreasing_host_receipts",
                "source_gaps_over_contiguous_limit",
                "nonfinite_source_intervals",
            )
        },
        "source_gaps_s": _distribution(source_gaps),
        "host_receipt_gaps_s": _distribution(receipt_gaps),
        "sampling": {
            "event_duration_s": settings.event_duration_s,
            "intervals_at_least_event_duration": larger,
            "fraction_intervals_at_least_event_duration": (
                larger / len(source_gaps) if source_gaps else None
            ),
            "event_coverage": "unverified",
            "short_event_may_be_entirely_missed": True if larger else None,
            "classification": "unavailable",
            "reason": "instantaneous_samples_do_not_establish_event_coverage",
        },
        "yaw_angular_rate": {
            "status": "available" if angular_rates else "unavailable",
            "reason": None
            if angular_rates
            else (
                "calibration_required"
                if not settings.source_yaw_unit_verified
                else "no_qualified_contiguous_source_yaw_pairs"
            ),
            "unit": "deg/s",
            "distribution": _distribution(angular_rates),
            "excluded_pairs": dict(sorted(reasons.items())),
            "unwrap_assumption": "less_than_half_revolution_between_source_samples",
            "meaning": "source_yaw_rate_not_verified_physical_heading_rate",
            "timestamp_basis": "quality.sensors.yaw.observed_at",
        },
    }


def analyze_evidence(
    records: object, settings: MotionEvidenceSettings | dict | None = None
) -> dict[str, Any]:
    """Describe at most 2048 v1 records; invalid individual rows stay visible.

    ValueError identifies malformed containers/settings or exceeded resource limits.
    Missing telemetry is an unavailable report, never fabricated zero motion.
    """
    config = _settings(settings)
    rows, summary = _validate_records(records)
    sensor_rows = [row for row in rows if row["schema_version"] == 1]
    channels, raw_norm = _channel_report(sensor_rows)
    timing = _timing_report(sensor_rows, config)
    imu_count = sum(row["kind"] == "imu" for row in rows)
    quality = _quality_summary(sensor_rows)
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "analyzer_version": ANALYZER_VERSION,
        "status": (
            "partial"
            if summary["records_rejected"] or summary["conflicting_record_ids"]
            else "available"
            if imu_count
            else "unavailable"
        ),
        "reason": None if imu_count else "imu_evidence_unavailable",
        "input": {
            **summary,
            "digest_scope": "input_records_as_sorted_json_lines_including_rejected",
            "validated_unique_digest_sha256": hashlib.sha256(
                _canonical(rows)
            ).hexdigest(),
            "dataset_revision": None,
        },
        "settings": asdict(config),
        "counts": {
            "imu_records": imu_count,
            "executor_records": sum(row["kind"] == "executor" for row in rows),
        },
        "host_record_count": sum(row["schema_version"] == 2 for row in rows),
        "clocks": {
            "source_domain": "device-wall-unsynchronised",
            "host_gap_clock": "received_mono",
            "ordering": "input_receipt_order_no_source_sorting",
            "cross_clock_latency_s": None,
            "synchronisation": "unknown",
            **timing,
        },
        "quality": quality,
        "channels": channels,
        "channel_distribution_basis": (
            "unique_record_ids_including_repeated_source_times"
        ),
        "acceleration": {
            "raw_norm": raw_norm,
            "gravity_compensated": {
                "status": "unavailable",
                "reason": "axes_frame_and_gravity_unqualified",
            },
            "forward_trend": {
                "status": "unavailable",
                "reason": "forward_axis_and_frame_unqualified",
            },
        },
        "physical_velocity": {
            "status": "unavailable",
            "unit": "m/s",
            "value": None,
            "reason": "no_qualified_velocity_measurement_or_inertial_solution",
        },
        "physical_distance": {
            "status": "unavailable",
            "unit": "m",
            "value": None,
            "reason": "no_qualified_position_measurement_or_inertial_solution",
        },
        "limitations": [
            "source_and_host_clocks_are_never_subtracted",
            "executor_acceptance_and_output_do_not_prove_physical_motion",
            "zero_sdk_or_acceleration_does_not_prove_stillness_or_allow_zupt",
            "signed_acceleration_does_not_identify_speedup_or_slowdown_in_reverse",
            "raw_acceleration_includes_gravity_bias_noise_and_unknown_frame",
            "source_cadence_does_not_prove_camera_exposure_association",
            "yaw_unwrap_cannot_resolve_turns_over_half_revolution_per_sample",
        ],
    }
    if len(_canonical(report)) > MAX_REPORT_BYTES:
        raise ValueError("motion_report_limit")
    return report


def analyze_manifest(
    manifest: object, settings: MotionEvidenceSettings | dict | None = None
) -> dict[str, Any]:
    """Analyze a validated session export, including legacy/evidence-only sessions.

    Reuse the archive validator for shape, revision and evidence integrity. An
    invalid archive is an error; a valid archive without evidence is unavailable.
    """
    from .samples_archive import SampleArchive

    if len(_canonical(manifest)) > 40 * 1024 * 1024:
        raise ValueError("motion_manifest_limit")
    SampleArchive._validate_manifest(manifest)
    assert isinstance(manifest, dict)  # Established by the shared validator.
    evidence = manifest.get("evidence")
    if evidence is None:
        report = analyze_evidence([], settings)
        report["reason"] = "legacy_archive_evidence_unavailable"
    else:
        if (
            not isinstance(evidence, dict)
            or type(evidence.get("schema_version")) is not int
            or evidence["schema_version"] not in {1, 2}
            or not isinstance(evidence.get("records"), list)
            or not isinstance(evidence.get("status", {}), dict)
        ):
            raise ValueError("motion_evidence_extension_invalid")
        report = analyze_evidence(evidence["records"], settings)
        report["collection_status"] = evidence.get("status", {})
    report["input"]["dataset_revision"] = manifest["dataset_revision"]
    report["input"]["manifest_digest_sha256"] = hashlib.sha256(
        _canonical(manifest)
    ).hexdigest()
    if len(_canonical(report)) > MAX_REPORT_BYTES:
        raise ValueError("motion_report_limit")
    return report


class _JsonBudget:
    """Bound byte/node work and detect only ancestor cycles during preflight."""

    def __init__(self):
        """Own one traversal budget without sharing state across requests."""
        self.remaining = MAX_EVIDENCE_SESSION_BYTES
        self.nodes_left = MAX_EVIDENCE_SESSION_BYTES
        self.ancestors: set[int] = set()

    def consume(self, size: int) -> None:
        """Charge exact JSON bytes."""
        self.remaining -= size
        if self.remaining < 0:
            raise ValueError("motion_records_limit")

    def visit(self, value: Any, depth: int) -> None:
        """Visit one bounded JSON node in the original traversal order."""
        self.nodes_left -= 1
        if self.nodes_left < 0 or depth > MAX_INPUT_DEPTH:
            raise ValueError("motion_records_limit")
        kind = type(value)
        if value is None:
            self.consume(4)
        elif kind is bool:
            self.consume(4 if value else 5)
        elif kind is int:
            _visit_json_integer(self, value)
        elif kind is float:
            _visit_json_float(self, value)
        elif kind is str:
            _visit_json_string(self, value)
        elif kind in (dict, list):
            _visit_json_container(self, value, kind, depth)
        else:
            raise ValueError("motion_input_invalid")


def _visit_json_string(self, value):
    """Charge exact escaped-string bytes without allocating the JSON representation."""
    self.consume(2)
    if len(value) > self.remaining:
        raise ValueError("motion_records_limit")
    for character in value:
        code = ord(character)
        if character in '"\\\x08\x0c\n\r\t':
            self.consume(2)
        elif code < 32 or code >= 127:
            self.consume(6 if code <= 65535 else 12)
        else:
            self.consume(1)


def _visit_json_container(self, value, kind, depth):
    """Preserve ancestor-cycle detection and punctuation/child budget order."""
    identity = id(value)
    if identity in self.ancestors:
        raise ValueError("motion_input_invalid")
    count = len(value)
    children = count * (2 if kind is dict else 1)
    minimum_size = 2 if not count else count * (5 if kind is dict else 2) + 1
    if children > self.nodes_left or minimum_size > self.remaining:
        raise ValueError("motion_records_limit")
    self.consume(2 + max(0, count - 1))
    self.ancestors.add(identity)
    try:
        if kind is dict:
            _visit_json_mapping(self, value, depth)
        else:
            for child in value:
                self.visit(child, depth + 1)
    finally:
        self.ancestors.remove(identity)


def _visit_json_mapping(self, value, depth):
    """Visit each string key, colon and child before proceeding to the next pair."""
    for key, child in value.items():
        if type(key) is not str:
            raise ValueError("motion_input_invalid")
        self.visit(key, depth + 1)
        self.consume(1)
        self.visit(child, depth + 1)


def _visit_json_integer(self, value):
    """Check integer bit budget before decimal conversion."""
    if value.bit_length() > MAX_INPUT_INTEGER_BITS:
        raise ValueError("motion_records_limit")
    self.consume(len(str(value)))


def _visit_json_float(self, value):
    """Charge finite or JSON nonfinite float spelling exactly."""
    if math.isfinite(value):
        self.consume(len(repr(value)))
    elif math.isnan(value):
        self.consume(3)
    elif value > 0:
        self.consume(8)
    else:
        self.consume(9)


def _record_angle_reason(record, settings):
    """Check yaw quality and unit for one source record in original order."""
    if not _fresh(record, "yaw"):
        return "source_quality_unqualified"
    if record["values"].get("yaw") is None:
        return "source_yaw_missing"
    if record["units"].get("yaw") != settings.source_yaw_unit:
        return "source_yaw_unit_unqualified"
    yaw_quality = _channel_quality(record, "yaw")
    if (
        "instance_id" in yaw_quality
        and yaw_quality["instance_id"] != record["source_instance_id"]
    ):
        return "source_yaw_instance_mismatch"
    if (
        yaw_quality.get("ready") is False
        or yaw_quality.get("stale") is True
        or yaw_quality.get("valid") is False
        or yaw_quality.get("wrapped_display") is True
    ):
        return "source_yaw_quality_unqualified"
    channel_unit = yaw_quality.get("unit")
    if channel_unit is not None and channel_unit != settings.source_yaw_unit:
        return "source_yaw_unit_unqualified"


def _acceleration_norm(record, norms):
    """Account complete same-unit vectors without fabricating missing values."""
    axes = ["accel_x", "accel_y", "accel_z"]
    if any(record["values"].get(axis) is None for axis in axes):
        return 1, 0, 0
    units = {record["units"].get(axis, "unknown") for axis in axes}
    if len(units) != 1:
        return 0, 1, 0
    norm = math.hypot(*(record["values"][axis] for axis in axes))
    if math.isfinite(norm):
        norms[units.pop()].append(norm)
    else:
        return 0, 0, 1
    return 0, 0, 0


def _timing_observation(record, settings, accumulators):
    """Account one IMU observation while retaining previous-source and
    uniqueness order.
    """
    previous, observed, counts, _, _, _, _ = accumulators
    if record["kind"] != "imu":
        return
    stream = (record["device_id"], record["kind"])
    prior = previous.get(stream)
    previous[stream] = record
    timestamp = record["source_observed_at"]
    if record["source_instance_id"] is None:
        counts["missing_instance_id"] += 1
    if timestamp is None:
        counts["missing_source_timestamp"] += 1
    else:
        sample = (*_identity(record), timestamp)
        if sample in observed:
            counts["repeated_source_timestamps"] += 1
        else:
            counts["unique_source_observations"] += 1
            observed.add(sample)
    if prior is None:
        return
    _timing_pair(prior, record, timestamp, settings, accumulators)


def _timing_pair(prior, record, timestamp, settings, accumulators):
    """Account intervals only after the stream predecessor is established."""
    _, _, counts, reasons, source_gaps, receipt_gaps, angular_rates = accumulators
    if _identity(prior) != _identity(record):
        counts["identity_discontinuities"] += 1
        for key in ("source_session", "source_instance_id", "source_clock_domain"):
            if prior[key] != record[key]:
                counts[key + "_changes"] += 1
        reasons["identity_discontinuity"] += 1
        return
    receipt_dt = record["received_mono"] - prior["received_mono"]
    if math.isfinite(receipt_dt) and receipt_dt > 0:
        receipt_gaps.append(receipt_dt)
    else:
        counts["nonincreasing_host_receipts"] += 1
    dt = _timing_source_interval(
        prior, timestamp, settings, counts, reasons, source_gaps
    )
    if dt is None:
        return
    if not math.isfinite(receipt_dt) or receipt_dt <= 0:
        reasons["host_receipt_nonincreasing"] += 1
        return
    _timing_yaw(prior, record, dt, settings, reasons, angular_rates)


def _timing_yaw(prior, record, dt, settings, reasons, angular_rates):
    """Report only qualified unambiguous source-yaw rates."""
    reason = _angle_reason(prior, record, dt, settings)
    if reason is not None:
        reasons[reason] += 1
        return
    first_time, second_time = _yaw_source_time(prior), _yaw_source_time(record)
    assert first_time is not None and second_time is not None
    rate = _yaw_rate(
        prior, record, second_time - first_time, settings.source_yaw_unit or "deg"
    )
    if rate is None:
        reasons["yaw_unwrap_ambiguous_or_nonfinite"] += 1
    else:
        angular_rates.append(rate)


def _timing_source_interval(prior, timestamp, settings, counts, reasons, source_gaps):
    """Preserve missing, nonfinite, nonincreasing and oversized interval counters."""
    prior_ts = prior["source_observed_at"]
    if timestamp is None or prior_ts is None:
        reasons["source_timestamp_missing"] += 1
        return
    dt = timestamp - prior_ts
    if not math.isfinite(dt):
        counts["nonfinite_source_intervals"] += 1
        reasons["source_interval_invalid"] += 1
        return
    if dt <= 0:
        if dt < 0:
            counts["out_of_order_source_timestamps"] += 1
        reasons["source_timestamp_nonincreasing"] += 1
        return
    source_gaps.append(dt)
    if dt > settings.max_contiguous_gap_s:
        counts["source_gaps_over_contiguous_limit"] += 1
    return dt
