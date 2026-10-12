"""The versioned, bounded inference contract. It contains no actuator fields."""

from __future__ import annotations

import io
import wave
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .errors import RiderError

PROTOCOL_VERSION = 1
TASKS = (
    "text.respond",
    "audio.transcribe",
    "audio.synthesize",
    "vision.describe",
    "conversation.respond",
)
IDENTIFIER = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
MAX_INPUT = 2 * 1024 * 1024
MAX_OUTPUT = 2 * 1024 * 1024
LIMITS = {
    "max_input_bytes": MAX_INPUT,
    "max_output_bytes": MAX_OUTPUT,
    "max_text_bytes": 4096,
    "max_header_bytes": 4096,
    "max_pending": 4,
    "max_running": 1,
    "ttl_min_ms": 100,
    "ttl_max_ms": 60000,
    "max_input_age_ms": 60000,
    "result_retention_s": 60,
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Parameters(StrictModel):
    language: Literal["pl", "en", "de"] = "pl"
    prompt: Annotated[str, Field(max_length=512)] = ""


class LeaseRequest(StrictModel):
    protocol_version: Literal[1]
    device_session: IDENTIFIER
    client_id: Literal["voice", "vision", "cli", "panel"] = "cli"
    client_instance: IDENTIFIER = "legacy"


class JobRequest(StrictModel):
    protocol_version: Literal[1]
    executor_session: IDENTIFIER
    session_id: IDENTIFIER
    device_session: IDENTIFIER
    job_id: IDENTIFIER
    input_id: IDENTIFIER
    stream_id: IDENTIFIER
    task: Literal[
        "text.respond",
        "audio.transcribe",
        "audio.synthesize",
        "vision.describe",
        "conversation.respond",
    ]
    captured_at: Annotated[float, Field(gt=0)]
    timestamp_source: Literal[
        "capture", "file_mtime", "capture_read_boundary", "operator_input", "unknown"
    ] = "unknown"
    input_age_ms: Annotated[float, Field(ge=0, le=60000)]
    ttl_ms: Annotated[int, Field(ge=100, le=60000)]
    sequence: Annotated[int, Field(ge=0, le=2**53 - 1)]
    params: Parameters = Field(default_factory=Parameters)


def validate_media(job: JobRequest, payload: bytes, mime: str) -> None:
    """Validate before retaining payload or occupying a model slot."""
    mime = mime.split(";", 1)[0].strip().lower()
    if not payload or len(payload) > MAX_INPUT:
        raise RiderError("compute_input_size", 413)
    if job.task in {"text.respond", "audio.synthesize", "conversation.respond"}:
        if mime != "text/plain" or len(payload) > LIMITS["max_text_bytes"]:
            raise RiderError("compute_input_format", 415)
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RiderError("compute_input_format", 415) from exc
        if not text.strip():
            raise RiderError("compute_empty_input", 400)
    elif job.task == "audio.transcribe":
        if mime not in {"audio/wav", "audio/x-wav"}:
            raise RiderError("compute_input_format", 415)
        try:
            with wave.open(io.BytesIO(payload)) as audio:
                if (
                    audio.getnchannels() != 1
                    or audio.getsampwidth() != 2
                    or audio.getcomptype() != "NONE"
                    or not 8000 <= audio.getframerate() <= 48000
                    or not 0 < audio.getnframes() <= audio.getframerate() * 15
                    or len(audio.readframes(audio.getnframes()))
                    != audio.getnframes() * 2
                ):
                    raise ValueError("invalid WAV")
        except (wave.Error, EOFError, ValueError) as exc:
            raise RiderError("compute_input_format", 415) from exc
    else:
        if mime not in {"image/jpeg", "image/png"}:
            raise RiderError("compute_input_format", 415)
        from PIL import Image

        try:
            with Image.open(io.BytesIO(payload)) as image:
                if (
                    image.format != ("JPEG" if mime == "image/jpeg" else "PNG")
                    or image.width > 1920
                    or image.height > 1080
                    or image.width < 1
                    or image.height < 1
                ):
                    raise ValueError("invalid image")
                image.verify()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise RiderError("compute_input_format", 415) from exc
    if job.task != "vision.describe" and job.params.prompt:
        raise RiderError("compute_invalid_parameters", 400)
