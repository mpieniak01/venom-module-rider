"""Bounded incremental multipart framing shared by the negotiated Pi media client."""

from __future__ import annotations

import re

from .errors import RiderError

MAX_PART_BYTES = 512 * 1024
MAX_HEADER_BYTES = 8192
MAX_BOUNDARY_BYTES = 70


def boundary_from_type(content_type: str) -> bytes:
    parts = [part.strip() for part in content_type.split(";")]
    if parts[0].lower() != "multipart/x-mixed-replace":
        raise RiderError("stream_invalid_format", 502)
    boundaries = [
        part.split("=", 1)[1].strip().strip('"')
        for part in parts[1:]
        if part.lower().startswith("boundary=")
    ]
    if len(boundaries) != 1 or not re.fullmatch(r"[a-zA-Z0-9_-]{1,70}", boundaries[0]):
        raise RiderError("stream_invalid_boundary", 502)
    return boundaries[0].encode("ascii")


class MultipartJpegParser:
    """Require explicit lengths; never scan unbounded bodies for JPEG markers."""

    def __init__(self, boundary: bytes):
        if not re.fullmatch(rb"[a-zA-Z0-9_-]{1,70}", boundary):
            raise RiderError("stream_invalid_boundary", 502)
        self.marker = b"--" + boundary
        self.buffer = bytearray()
        self.headers: dict[str, str] | None = None
        self.length = 0
        self.finished = False
        self.started = False

    def feed(self, chunk: bytes) -> list[tuple[dict[str, str], bytes]]:
        # PiClient supplies bounded 16 KiB chunks. A caller cannot use this
        # parser as an unbounded staging queue before validation.
        if len(chunk) > MAX_PART_BYTES + MAX_HEADER_BYTES:
            raise RiderError("stream_input_size", 502)
        if self.finished:
            if chunk.strip():
                raise RiderError("stream_invalid_boundary", 502)
            return []
        self.buffer.extend(chunk)
        frames = []
        while self.buffer:
            if self.headers is None:
                if not self.started:
                    if self.buffer.startswith(b"\r\n"):
                        del self.buffer[:2]
                    if len(self.buffer) < len(self.marker) + 2:
                        break
                    if not self.buffer.startswith(self.marker):
                        raise RiderError("stream_invalid_boundary", 502)
                    trailer = self.buffer[len(self.marker) : len(self.marker) + 2]
                    if trailer == b"--":
                        del self.buffer[: len(self.marker) + 2]
                        self.finished = True
                        break
                    if trailer != b"\r\n":
                        raise RiderError("stream_invalid_boundary", 502)
                    del self.buffer[: len(self.marker) + 2]
                    self.started = True
                split = self.buffer.find(b"\r\n\r\n")
                if split < 0:
                    if len(self.buffer) > MAX_HEADER_BYTES:
                        raise RiderError("stream_header_size", 502)
                    break
                if split > MAX_HEADER_BYTES:
                    raise RiderError("stream_header_size", 502)
                raw = bytes(self.buffer[:split])
                del self.buffer[: split + 4]
                headers: dict[str, str] = {}
                try:
                    for line in raw.decode("ascii").split("\r\n"):
                        key, value = line.split(":", 1)
                        key = key.strip().lower()
                        if not re.fullmatch(r"[a-z0-9-]{1,64}", key) or key in headers:
                            raise ValueError("invalid or duplicate header")
                        headers[key] = value.strip()
                except (ValueError, UnicodeError) as exc:
                    raise RiderError("stream_invalid_headers", 502) from exc
                length = headers.get("content-length", "")
                if not length.isascii() or not length.isdigit():
                    raise RiderError("stream_invalid_length", 502)
                self.length = int(length) if len(length) < 8 else MAX_PART_BYTES + 1
                if not 4 <= self.length <= MAX_PART_BYTES:
                    raise RiderError("stream_input_size", 502)
                if headers.get("content-type", "").lower() != "image/jpeg":
                    raise RiderError("stream_invalid_format", 502)
                self.headers = headers
            if len(self.buffer) < self.length + 2:
                break
            payload = bytes(self.buffer[: self.length])
            if self.buffer[self.length : self.length + 2] != b"\r\n":
                raise RiderError("stream_invalid_length", 502)
            del self.buffer[: self.length + 2]
            if not payload.startswith(b"\xff\xd8") or not payload.endswith(b"\xff\xd9"):
                raise RiderError("stream_invalid_jpeg", 502)
            frames.append((self.headers, payload))
            self.headers = None
            self.started = False
        if len(self.buffer) > MAX_PART_BYTES + MAX_HEADER_BYTES + 128:
            raise RiderError("stream_input_size", 502)
        return frames

    def finish(self):
        if self.headers is not None or (
            self.buffer.strip() if self.finished else self.buffer
        ):
            raise RiderError("stream_truncated", 502)
