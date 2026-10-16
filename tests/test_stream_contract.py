"""Native camera contracts and exactly-once canonical image transforms."""

import hashlib
import io
import json
import time

import pytest
from pydantic import ValidationError

from venom_module_rider.errors import RiderError
from venom_module_rider.stream_contract import (
    NativeFrame,
    SourceDescription,
    SourceReference,
    canonical_jpeg,
)

Image = pytest.importorskip("PIL.Image")


def jpeg():
    image = Image.new("RGB", (96, 64))
    for box, color in [
        ((0, 0, 48, 32), (220, 20, 20)),
        ((48, 0, 96, 32), (20, 220, 20)),
        ((0, 32, 48, 64), (20, 20, 220)),
        ((48, 32, 96, 64), (220, 220, 20)),
    ]:
        image.paste(color, box)
    stream = io.BytesIO()
    image.save(stream, format="JPEG", quality=95)
    return stream.getvalue()


def source(payload=None, *, sequence=1, session="session", epoch=2, revision=3):
    payload = jpeg() if payload is None else payload
    return {
        "contract_version": 1,
        "camera_session": session,
        "sequence": sequence,
        "transport_epoch": epoch,
        "pipeline_revision": revision,
        "frame_id": f"{session}-{sequence}",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "frame_age_ms": 5.0,
        "captured_monotonic_ns": 100000000,
        "encoded_width": 96,
        "encoded_height": 64,
        "image_transform": {"rotation": 0, "flip_h": False, "flip_v": False},
    }


def native(sequence=1, *, now=None, **kwargs):
    payload = jpeg()
    return NativeFrame(
        SourceDescription.model_validate(source(payload, sequence=sequence, **kwargs)),
        payload,
        time.monotonic() if now is None else now,
    )


@pytest.mark.parametrize(
    "rotation,expected",
    [
        (0, ["red", "green", "blue", "yellow"]),
        (90, ["blue", "red", "yellow", "green"]),
        (180, ["yellow", "blue", "green", "red"]),
        (270, ["green", "yellow", "red", "blue"]),
    ],
)
@pytest.mark.parametrize("flip_h", [False, True])
@pytest.mark.parametrize("flip_v", [False, True])
def test_canonical_orientation_is_clockwise_then_flips_exactly_once(
    rotation, expected, flip_h, flip_v
):
    incoming = native()
    descriptor = incoming.source.model_dump(mode="json")
    descriptor["image_transform"] = {
        "rotation": rotation,
        "flip_h": flip_h,
        "flip_v": flip_v,
    }
    frame = NativeFrame(
        SourceDescription.model_validate(descriptor),
        incoming.payload,
        incoming.received_mono,
    )
    frame.validate_payload()
    rendered = canonical_jpeg(frame)
    assert frame.source.sha256 == hashlib.sha256(incoming.payload).hexdigest()
    if not rotation and not flip_h and not flip_v:
        assert rendered is incoming.payload
    image = Image.open(io.BytesIO(rendered))
    assert image.size == ((64, 96) if rotation in (90, 270) else (96, 64))
    if flip_h:
        expected = [expected[1], expected[0], expected[3], expected[2]]
    if flip_v:
        expected = expected[2:] + expected[:2]
    colors = {
        "red": (220, 20, 20),
        "green": (20, 220, 20),
        "blue": (20, 20, 220),
        "yellow": (220, 220, 20),
    }
    for position, color in zip(
        [
            (image.width // 4, image.height // 4),
            (3 * image.width // 4, image.height // 4),
            (image.width // 4, 3 * image.height // 4),
            (3 * image.width // 4, 3 * image.height // 4),
        ],
        expected,
        strict=True,
    ):
        assert (
            max(
                abs(a - b)
                for a, b in zip(image.getpixel(position), colors[color], strict=True)
            )
            < 15
        )


@pytest.mark.parametrize(
    "update",
    [
        {"sequence": True},
        {"contract_version": True},
        {"image_transform": {"rotation": False, "flip_h": False, "flip_v": False}},
        {"transport_epoch": -1},
        {"pipeline_revision": -1},
        {"frame_id": "other"},
        {"sha256": "bad"},
        {"frame_age_ms": float("nan")},
        {"encoded_width": 8193},
        {"captured_monotonic_ns": 0},
        {"pipeline_revision": 0},
        {"sequence": 0},
        {"extra": "not-negotiated"},
        {"image_transform": {"rotation": 45, "flip_h": False, "flip_v": False}},
        {"image_transform": {"rotation": 0, "flip_h": 1, "flip_v": False}},
    ],
)
def test_source_descriptor_rejects_ambiguous_or_unbounded_values(update):
    with pytest.raises(ValidationError):
        SourceDescription.model_validate({**source(), **update})


def test_reference_is_exact_identity_and_age_is_host_monotonic_only():
    frame = native(now=10.0)
    reference = frame.source.reference()
    assert (
        SourceReference.model_validate_json(reference.model_dump_json()).identity()
        == frame.source.identity()
    )
    assert frame.age_ms(10.06) == pytest.approx(65)
    assert frame.age_ms(9.0) == 5
    assert "captured_monotonic_ns" not in reference.model_dump()
    assert json.loads(frame.source.model_dump_json())["contract_version"] == 1
    with pytest.raises(RiderError, match="frame_stale"):
        frame.age_ms(float("inf"))


def test_canonical_validation_rejects_tampered_hash_or_mismatched_dimensions():
    frame = native()
    bad = NativeFrame(frame.source, b"not-the-source", frame.received_mono)
    with pytest.raises(RiderError, match="stream_hash_mismatch"):
        bad.validate_payload()
    with pytest.raises(RiderError, match="stream_invalid_jpeg"):
        canonical_jpeg(bad)
    wrong = NativeFrame(
        frame.source.model_copy(update={"encoded_width": 97}),
        frame.payload,
        frame.received_mono,
    )
    with pytest.raises(RiderError, match="stream_invalid_jpeg"):
        canonical_jpeg(wrong)


def test_wire_dimensions_are_negotiated_but_model_decode_limit_is_explicit():
    frame = native()
    descriptor = frame.source.model_copy(
        update={"encoded_width": 8192, "encoded_height": 8192}
    )
    with pytest.raises(RiderError, match="stream_input_size"):
        canonical_jpeg(NativeFrame(descriptor, frame.payload, frame.received_mono))
