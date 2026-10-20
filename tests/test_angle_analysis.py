"""Rate estimates are channel finite differences, never raw gyro measurements."""

import math
from copy import deepcopy

import pytest
from test_attitude_evidence import convert, payload

from venom_module_rider.angle_analysis import PROFILE, enrich, semantics


def point(t, value, unit="deg", segment=1):
    return {
        "id": str(t),
        "t": t,
        "source_time": t + 100,
        "instance_id": "boot",
        "valid": True,
        "segment": segment,
        "values": {"roll": value, "pitch": value, "yaw": value},
        "units": dict.fromkeys(("roll", "pitch", "yaw"), unit),
        "angle_semantics": {
            axis: {"unit": unit, "basis": "source-declared"}
            for axis in ("roll", "pitch", "yaw")
        },
        "quality": {
            "stale": False,
            "offline": False,
            "sensors": {
                axis: {
                    "unit": unit,
                    "ready": True,
                    "stale": False,
                    "offline": False,
                    "observed_at": t + 100,
                    "instance_id": "boot",
                }
                for axis in ("roll", "pitch", "yaw")
            },
        },
    }


def test_known_firmware_profile_does_not_relabel_source_or_unknown_firmware():
    row = {
        "units": {"roll": "sdk_raw"},
        "quality": {
            "source": "motion-executor",
            "telemetry": {
                "firmware": "R-1.1.6",
                "contract_id": "rider.telemetry",
                "contract_version": 1,
                "uart_owner": "motion-executor",
            },
        },
    }
    assert semantics(row)["roll"] == {"unit": "deg", "basis": PROFILE}
    assert row["units"]["roll"] == "sdk_raw"
    for field, value in [
        ("firmware", "R-1.2.3"),
        ("contract_version", True),
        ("uart_owner", "unknown"),
    ]:
        changed = deepcopy(row)
        changed["quality"]["telemetry"][field] = value
        assert semantics(changed) == {}


def test_metadata_is_copied_from_canonical_observation_without_secrets():
    source = payload()
    root = source["data"]["devices"]["xgo"]
    root["observation"] = {
        "fw": "R-1.1.6",
        "contract_id": "rider.telemetry",
        "contract_version": 1,
        "uart_owner": "motion-executor",
        "source": "motion-executor",
        "sensor_quality": root["sensor_quality"],
        "secret": "never-copy",
    }
    record = convert(source)
    assert record["quality"]["telemetry"]["firmware"] == "R-1.1.6"
    assert "never-copy" not in str(record)
    assert record["quality"]["source"] == "motion-executor"


@pytest.mark.parametrize(
    "unit,first,second",
    [("deg", 179, -179), ("rad", math.radians(179), math.radians(-179))],
)
def test_rates_unwrap_angle_and_use_original_channel_clock(unit, first, second):
    points = [point(0, first, unit), point(0.2, second, unit)]
    report = enrich(points, [])
    assert points[1]["angle_rates_deg_s"]["yaw"] == pytest.approx(10)
    assert report["observed_hz"] == pytest.approx(5)
    assert report["physical_latency_s"] is None


@pytest.mark.parametrize(
    "change",
    [
        "reset",
        "gap",
        "stale",
        "instance",
        "unit",
        "unknown",
        "ambiguous",
        "source_reset",
    ],
)
def test_bad_intervals_never_become_zero_or_fake_rate(change):
    points = [point(0, 0), point(0.2, 1)]
    second = points[1]
    if change == "reset":
        second["segment"] = 2
    if change == "gap":
        second["t"] = 2
    if change == "stale":
        second["quality"]["stale"] = True
    if change == "instance":
        second["instance_id"] = "other"
    if change == "unit":
        second["quality"]["sensors"]["yaw"]["unit"] = "rad"
    if change == "unknown":
        second["angle_semantics"] = {}
    if change == "ambiguous":
        second["values"] = dict.fromkeys(("roll", "pitch", "yaw"), 180)
    if change == "source_reset":
        for sensor in second["quality"]["sensors"].values():
            sensor["observed_at"] = 99
    enrich(points, [])
    assert "yaw" not in second["angle_rates_deg_s"]


def test_short_pulse_diagnostics_and_discontinuities_are_explicit():
    points = [point(0, 0), point(0.34, 1), point(0.68, 2, segment=2)]
    event = {
        "phase": "requested",
        "t": 0,
        "operation_id": "pulse",
        "intent": {"mode": "pulse", "duration_s": 0.15},
    }
    report = enrich(points, [event])
    assert report["pulses"][0]["insufficient"]
    assert report["pulses"][0]["observations"] == 1


def test_oversized_clock_and_missing_channels_are_not_valid_observations():
    points = [point(0, 0), point(0.1, 1), point(0.2, 2)]
    points[1]["quality"]["sensors"]["yaw"]["observed_at"] = 10**1000
    points[2]["values"].pop("yaw")
    event = {
        "phase": "requested",
        "t": 0,
        "operation_id": "pulse",
        "intent": {"mode": "pulse", "duration_s": 0.3},
    }
    report = enrich(points, [event])
    assert "yaw" not in points[1]["angle_rates_deg_s"]
    assert "yaw" not in points[2]["angle_rates_deg_s"]
    assert report["pulses"][0]["observations"] == 1
    assert report["pulses"][0]["insufficient"]


def test_sparse_source_cadence_remains_visible_across_gap_segments():
    points = [point(0, 0), point(2, 1, segment=2), point(4, 2, segment=3)]
    report = enrich(points, [])
    assert report["observed_hz"] == pytest.approx(0.5)
    assert report["source_interval_max_s"] == 2
    assert all(not p["angle_rates_deg_s"] for p in points)
    points[1]["instance_id"] = "other-boot"
    assert enrich(points, [])["observed_hz"] is None
