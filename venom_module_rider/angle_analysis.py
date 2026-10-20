"""Versioned angle semantics and bounded finite differences; no gyro invention."""

import math
import statistics

AXES = ("roll", "pitch", "yaw")
PROFILE = "rider-r1.1.6-angle-registers-v1"


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def semantics(row):
    quality = row["quality"]
    telemetry = quality.get("telemetry", {})
    telemetry = telemetry if isinstance(telemetry, dict) else {}
    mapped = (
        telemetry.get("firmware") == "R-1.1.6"
        and telemetry.get("contract_id") == "rider.telemetry"
        and type(telemetry.get("contract_version")) is int
        and telemetry["contract_version"] == 1
        and telemetry.get("uart_owner") == "motion-executor"
        and quality.get("source") == "motion-executor"
    )
    return {
        axis: {"unit": unit, "basis": basis}
        for axis in AXES
        if (source_unit := row["units"].get(axis)) in ("deg", "rad")
        or source_unit == "sdk_raw"
        and mapped
        for unit, basis in [
            (source_unit, "source-declared")
            if source_unit != "sdk_raw"
            else ("deg", PROFILE)
        ]
    }


def channel_time(point, axis):
    sensors = point["quality"].get("sensors", {})
    sensor = sensors.get(axis, {}) if isinstance(sensors, dict) else {}
    if not isinstance(sensor, dict):
        return None
    stamp = sensor.get("observed_at")
    if (
        not point["valid"]
        or not point["instance_id"]
        or point["quality"].get("stale") is not False
        or point["quality"].get("offline") is not False
        or sensor.get("unit") != point["units"].get(axis)
        or sensor.get("ready") is not True
        or sensor.get("stale") is True
        or sensor.get("offline") is True
        or sensor.get("instance_id", point["instance_id"]) != point["instance_id"]
        or not finite(stamp)
    ):
        return None
    return stamp


def enrich(points, events):
    previous: dict[str, tuple] = {}
    intervals = []
    for point in points:
        rates = {}
        for axis in AXES:
            semantic = point["angle_semantics"].get(axis)
            stamp = channel_time(point, axis)
            value = point["values"].get(axis)
            prior = previous.get(axis)
            current = (point, stamp, value, semantic)
            previous[axis] = current
            if (
                not semantic
                or stamp is None
                or not finite(value)
                or abs(value) > 1e9
                or prior is None
            ):
                continue
            p, before, old, old_semantic = prior
            if (
                before is None
                or not finite(old)
                or abs(old) > 1e9
                or semantic != old_semantic
                or p["instance_id"] != point["instance_id"]
                or p["segment"] != point["segment"]
            ):
                continue
            dt, host_dt = stamp - before, point["t"] - p["t"]
            if not 0 < dt <= 1 or not 0 < host_dt <= 1:
                continue
            period = 360 if semantic["unit"] == "deg" else 2 * math.pi
            delta = (value - old + period / 2) % period - period / 2
            if math.isclose(abs(delta), period / 2, abs_tol=1e-8):
                continue
            rate = delta / dt
            if semantic["unit"] == "rad":
                rate = math.degrees(rate)
            if finite(rate):
                rates[axis] = rate
                intervals.append(dt)
        point["angle_rates_deg_s"] = rates
    # Sampling describes observed source intervals, never an inferred firmware ODR.
    gaps = []
    for first, second in zip(points, points[1:], strict=False):
        if (
            first["segment"] != second["segment"]
            or first["instance_id"] != second["instance_id"]
        ):
            continue
        a, b = first["source_time"], second["source_time"]
        if finite(a) and finite(b) and 0 < b - a <= 1:
            gaps.append(b - a)
    ordered = sorted(gaps)
    median = statistics.median(ordered) if ordered else None
    pulses = [
        e
        for e in events
        if e["phase"] == "requested" and e["intent"].get("mode") == "pulse"
    ]
    assessed = []
    for event in pulses:
        duration = event["intent"].get("duration_s")
        if not finite(duration) or duration <= 0:
            continue
        inside = [
            point
            for point in points
            if point["valid"]
            and all(
                channel_time(point, axis) is not None
                and finite(point["values"].get(axis))
                for axis in AXES
            )
            and event["t"] <= point["t"] <= event["t"] + duration
        ]
        assessed.append(
            {
                "operation_id": event["operation_id"],
                "duration_s": duration,
                "observations": len(inside),
                "insufficient": len(inside) < 3
                or len({p["segment"] for p in inside}) != 1
                or any(not p["instance_id"] for p in inside),
            }
        )
    return {
        "source_interval_median_s": median,
        "observed_hz": 1 / median if median else None,
        "source_interval_max_s": max(ordered) if ordered else None,
        "rate_pairs": len(intervals),
        "pulses": assessed,
        "physical_latency_s": None,
    }
