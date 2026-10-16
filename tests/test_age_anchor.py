"""Source ages include network backlog without subtracting clocks across hosts."""

import time

import pytest
from test_stream_contract import native

from venom_module_rider.age_anchor import age_anchor


def status_for(frame):
    return {
        "camera_session": frame.source.camera_session,
        "transport_epoch": frame.source.transport_epoch,
        "revision": frame.source.pipeline_revision,
        "latest_frame": frame.source.model_dump(mode="json"),
    }


def test_anchor_adds_entire_http_rtt_and_detects_a_backlogged_part():
    latest = native(now=20.0)
    # Pi monotonic can be entirely unrelated to host monotonic.
    latest = latest.source.model_copy(
        update={"captured_monotonic_ns": 9_000_000_000_000, "frame_age_ms": 20.0}
    )
    status = {
        "camera_session": latest.camera_session,
        "transport_epoch": latest.transport_epoch,
        "revision": latest.pipeline_revision,
        "latest_frame": latest.model_dump(mode="json"),
    }
    anchor = age_anchor(status, 1000.0, 1000.04)
    delayed = latest.model_copy(
        update={
            "captured_monotonic_ns": latest.captured_monotonic_ns - 300_000_000,
            "frame_age_ms": 10.0,
        }
    )
    # Source-send age alone says 10 ms; status anchor bounds this at 360 ms.
    assert anchor.upper_age_ms(delayed, 1000.04) == pytest.approx(360)
    assert anchor.upper_age_ms(delayed, 1000.08) == pytest.approx(400)
    shifted = delayed.model_copy(
        update={
            "captured_monotonic_ns": delayed.captured_monotonic_ns + 8_000_000_000_000
        }
    )
    shifted_status = {
        **status,
        "latest_frame": {
            **status["latest_frame"],
            "captured_monotonic_ns": latest.captured_monotonic_ns + 8_000_000_000_000,
        },
    }
    assert age_anchor(shifted_status, 1000.0, 1000.04).upper_age_ms(
        shifted, 1000.04
    ) == pytest.approx(360)


def test_anchor_never_crosses_session_epoch_or_revision():
    frame = native()
    status = status_for(frame)
    now = time.monotonic()
    anchor = age_anchor(status, now, now + 0.01)
    for field, value in [
        ("camera_session", "other"),
        ("transport_epoch", 99),
        ("pipeline_revision", 99),
    ]:
        assert (
            anchor.upper_age_ms(frame.source.model_copy(update={field: value}), now)
            is None
        )
    assert (
        age_anchor({**status, "camera_session": "different"}, now, now + 0.01) is None
    )
    assert age_anchor({**status, "latest_frame": None}, now, now + 0.01) is None


@pytest.mark.parametrize(
    "start,end",
    [
        (None, 1.0),
        (True, 1.0),
        (1.0, False),
        (float("nan"), 1.0),
        (1.0, float("inf")),
        (2.0, 1.0),
    ],
)
def test_invalid_or_missing_transport_timing_cannot_create_an_anchor(start, end):
    assert age_anchor(status_for(native()), start, end) is None
