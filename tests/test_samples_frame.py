"""Exact displayed-frame retention, bounds and eviction, without any Pi IO."""

import math

import pytest

from venom_module_rider.errors import RiderError
from venom_module_rider.samples_frame import FrameBuffer


def test_snapshot_is_exact_and_immutable_and_receipts_are_distinct():
    clock = [1.0]
    buffer = FrameBuffer(clock=lambda: clock[0], wall=lambda: 123.0)
    context = {"frame_id": "source", "sequence": 1, "capture_time": None}
    key = buffer.offer("robot", b"exact-original-bytes", "image/jpeg", context)
    context["sequence"] = 9
    frame = buffer.get(key)
    assert frame.payload == b"exact-original-bytes"
    assert frame.received_at == 123.0 and frame.context()["sequence"] == 1
    frame.context()["sequence"] = 10
    assert frame.context()["sequence"] == 1
    newer = buffer.offer("robot", frame.payload, "image/jpeg", frame.context())
    assert newer != key
    assert buffer.get(newer).sha256 == frame.sha256
    assert buffer.lookup("robot", frame.context()) == newer


def test_byte_and_count_limits_never_return_the_next_frame():
    buffer = FrameBuffer(max_bytes=8, max_frames=2)
    first = buffer.offer("robot", b"first", "image/jpeg", {"frame_id": "one"})
    second = buffer.offer("robot", b"next", "image/jpeg", {"frame_id": "two"})
    assert buffer.status()["bytes"] == 4 and buffer.status()["evicted"] == 1
    with pytest.raises(RiderError, match="samples_frame_expired"):
        buffer.get(first)
    assert buffer.get(second).payload == b"next"
    buffer.offer("robot", b"a", "image/png", {"frame_id": "three"})
    buffer.offer("robot", b"b", "image/png", {"frame_id": "four"})
    assert buffer.status()["frames"] == 2
    assert buffer.lookup("robot", {"frame_id": "two"}) is None


def test_expiry_uses_receipt_monotonic_time_and_purges_identity():
    now = [0.0]
    buffer = FrameBuffer(clock=lambda: now[0], ttl_s=1)
    key = buffer.offer("robot", b"one", "image/png", {"frame_id": "one"})
    now[0] = 1.01
    assert buffer.status()["expired"] == 1
    assert buffer.lookup("robot", {"frame_id": "one"}) is None
    with pytest.raises(RiderError, match="samples_frame_expired"):
        buffer.get(key)
    assert buffer.status()["bytes"] == 0


def test_evicting_old_receipt_does_not_remove_new_identity():
    buffer = FrameBuffer(max_frames=2)
    old = buffer.offer("robot", b"a", "image/jpeg")
    newest = buffer.offer("robot", b"a", "image/jpeg")
    buffer.offer("other", b"b", "image/jpeg")
    assert buffer.lookup("robot", {}) == newest
    with pytest.raises(RiderError):
        buffer.get(old)


@pytest.mark.parametrize(
    "payload,mime,context",
    [
        (b"", "image/jpeg", {}),
        ("not-bytes", "image/jpeg", {}),
        (b"x", "video/mp4", {}),
        (b"x" * (512 * 1024 + 1), "image/jpeg", {}),
        (b"x", "image/jpeg", {"bad": math.nan}),
        (b"x", "image/jpeg", {"frame_id": []}),
        (b"x", "image/jpeg", {"too_large": "x" * (32 * 1024)}),
        (b"x", "image/jpeg", ["not", "metadata"]),
    ],
)
def test_rejected_input_never_retains_memory_or_escapes_producer(
    payload, mime, context
):
    buffer = FrameBuffer()
    assert buffer.offer("robot", payload, mime, context) is None
    assert buffer.status()["frames"] == 0 and buffer.status()["rejected"] == 1


@pytest.mark.parametrize(
    "settings",
    [
        {"max_bytes": 0},
        {"max_frames": 0},
        {"ttl_s": 0},
        {"ttl_s": math.inf},
        {"ttl_s": math.nan},
        {"max_frames": True},
    ],
)
def test_configuration_has_finite_hard_limits(settings):
    with pytest.raises(ValueError):
        FrameBuffer(**settings)


def test_analysis_attaches_to_exact_raw_without_mutating_an_admitted_snapshot():
    buffer = FrameBuffer()
    key = buffer.offer("robot", b"raw-original", "image/jpeg", {"frame_id": "one"})
    queued = buffer.get(key)
    assert buffer.annotate_prediction(key, {"state": "clear"}) is True
    assert buffer.get(key).payload == queued.payload
    assert queued.context().get("prediction") is None
    assert buffer.get(key).context()["prediction"]["state"] == "clear"
    assert buffer.annotate_prediction(key, {"invalid": float("nan")}) is False
    assert buffer.annotate_prediction("expired", {}) is False
