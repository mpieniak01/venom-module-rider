"""Validated native camera identities; clocks are never compared across hosts."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from .errors import RiderError
from .vision_contract import VisionFrame

Identifier = Annotated[
    str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_-]+$")
]
Counter = Annotated[StrictInt, Field(ge=0, le=2**53 - 1)]


class StreamModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class PipelineProfile(StreamModel):
    analysis_location: Literal["local", "venom"]
    local_engine: Literal["canny", "ssd", "tflite"]
    remote_transport: Literal["snapshots", "mjpeg"]


class PipelineAction(StreamModel):
    contract_version: Literal[1]
    request_id: Annotated[
        str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    ]
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    action: Literal["configure", "start", "stop"]
    profile: PipelineProfile | None = None

    @field_validator("contract_version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int:
            raise ValueError("integer_version_required")
        return value

    @model_validator(mode="after")
    def configuration_profile(self):
        if (self.action == "configure") != (self.profile is not None) or (
            self.action != "configure" and "profile" in self.model_fields_set
        ):
            raise ValueError("profile_required_only_for_configuration")
        return self


class ImageTransform(StreamModel):
    rotation: Literal[0, 90, 180, 270]
    flip_h: StrictBool
    flip_v: StrictBool

    @field_validator("rotation", mode="before")
    @classmethod
    def strict_rotation(cls, value):
        if type(value) is not int:
            raise ValueError("integer_rotation_required")
        return value


class SourceReference(StreamModel):
    camera_session: Identifier
    sequence: Annotated[StrictInt, Field(ge=1, le=2**53 - 1)]
    transport_epoch: Counter
    pipeline_revision: Annotated[StrictInt, Field(ge=1, le=2**53 - 1)]
    frame_id: Identifier
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

    @model_validator(mode="after")
    def matching_identity(self):
        if self.frame_id != f"{self.camera_session}-{self.sequence}":
            raise ValueError("source_frame_identity")
        return self

    def identity(self):
        return (
            self.camera_session,
            self.sequence,
            self.transport_epoch,
            self.pipeline_revision,
            self.frame_id,
            self.sha256,
        )


class SourceDescription(SourceReference):
    contract_version: Literal[1]
    frame_age_ms: Annotated[float, Field(ge=0)]
    captured_monotonic_ns: Annotated[StrictInt, Field(ge=1)]
    encoded_width: Annotated[StrictInt, Field(ge=1, le=8192)]
    encoded_height: Annotated[StrictInt, Field(ge=1, le=8192)]
    image_transform: ImageTransform

    @field_validator("contract_version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int:
            raise ValueError("integer_version_required")
        return value

    def reference(self):
        return SourceReference.model_validate(
            {key: getattr(self, key) for key in SourceReference.model_fields}
        )


class ReferenceDecision(StreamModel):
    frame: VisionFrame
    source: SourceReference


@dataclass(frozen=True)
class NativeFrame:
    source: SourceDescription
    payload: bytes
    received_mono: float
    age_floor_ms: float = 0.0

    def age_ms(self, now):
        # frame_age_ms was sampled by Pi immediately before sending this part.
        # This is a conservative local residence measurement, not a network
        # latency claim. Pi validates the full capture-return age itself.
        age = (
            max(self.source.frame_age_ms, self.age_floor_ms)
            + max(0, now - self.received_mono) * 1000
        )
        if not math.isfinite(age):
            raise RiderError("frame_stale", 409)
        return age

    def validate_payload(self):
        if hashlib.sha256(self.payload).hexdigest() != self.source.sha256:
            raise RiderError("stream_hash_mismatch", 502)


def canonical_jpeg(frame: NativeFrame) -> bytes:
    """Apply the declared transform once, on Venom, retaining original source SHA."""
    import io

    from PIL import Image

    transform = frame.source.image_transform
    if (
        min(frame.source.encoded_width, frame.source.encoded_height) < 32
        or max(frame.source.encoded_width, frame.source.encoded_height) > 1280
        or frame.source.encoded_width * frame.source.encoded_height > 1280 * 720
    ):
        raise RiderError("stream_input_size", 502)
    try:
        with Image.open(io.BytesIO(frame.payload)) as opened:
            if opened.format != "JPEG" or opened.size != (
                frame.source.encoded_width,
                frame.source.encoded_height,
            ):
                raise ValueError("dimensions do not match source")
            opened.load()
            image = opened.convert("RGB")
        if transform.rotation:
            # Pi PREVIEW_ROT is clockwise; PIL's transpose names are CCW.
            image = image.transpose(
                {
                    90: Image.Transpose.ROTATE_270,
                    180: Image.Transpose.ROTATE_180,
                    270: Image.Transpose.ROTATE_90,
                }[transform.rotation]
            )
        if transform.flip_h:
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if transform.flip_v:
            image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        if not transform.rotation and not transform.flip_h and not transform.flip_v:
            return frame.payload
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=90)
        return output.getvalue()
    except (
        OSError,
        ValueError,
        Image.DecompressionBombError,
    ) as exc:
        raise RiderError("stream_invalid_jpeg", 502) from exc


def source_headers(frame, now, *, source="pi-camera", max_age_ms=300):
    metadata = frame.source.model_dump(mode="json")
    return {
        "cache-control": "no-store",
        "x-rider-source": source,
        "x-rider-source-json": json.dumps(metadata, separators=(",", ":")),
        "x-rider-stale": "true" if frame.age_ms(now) >= max_age_ms else "false",
        "x-rider-placeholder": "false",
        "x-rider-camera-session": frame.source.camera_session,
        "x-rider-transport-epoch": str(frame.source.transport_epoch),
        "x-rider-pipeline-revision": str(frame.source.pipeline_revision),
        "x-rider-frame-id": frame.source.frame_id,
        "x-rider-sequence": str(frame.source.sequence),
        "x-rider-frame-age-ms": str(round(frame.age_ms(now), 2)),
        "x-rider-frame-age-source": "pi-status-anchor-plus-host-elapsed",
    }
