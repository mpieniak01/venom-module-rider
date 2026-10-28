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
        """Consume bounded bytes while preserving incomplete multipart state."""
        if len(chunk) > MAX_PART_BYTES + MAX_HEADER_BYTES:
            raise RiderError("stream_input_size", 502)
        if self.finished:
            if chunk.strip():
                raise RiderError("stream_invalid_boundary", 502)
            return []
        self.buffer.extend(chunk)
        frames = []
        while self.buffer:
            if self.headers is None and not self._read_part_headers():
                break
            frame = self._read_payload()
            if frame is None:
                break
            frames.append(frame)
        if len(self.buffer) > MAX_PART_BYTES + MAX_HEADER_BYTES + 128:
            raise RiderError("stream_input_size", 502)
        return frames

    def _start_part(self):
        """Consume exactly one initial boundary, retaining partial markers."""
        if self.buffer.startswith(b"\r\n"):
            del self.buffer[:2]
        if len(self.buffer) < len(self.marker) + 2:
            return False
        if not self.buffer.startswith(self.marker):
            raise RiderError("stream_invalid_boundary", 502)
        trailer = self.buffer[len(self.marker) : len(self.marker) + 2]
        if trailer == b"--":
            del self.buffer[: len(self.marker) + 2]
            self.finished = True
            return False
        if trailer != b"\r\n":
            raise RiderError("stream_invalid_boundary", 502)
        del self.buffer[: len(self.marker) + 2]
        self.started = True
        return True

    def _read_part_headers(self):
        """Read bounded headers only after a complete valid boundary."""
        if not self.started and not self._start_part():
            return False
        split = self.buffer.find(b"\r\n\r\n")
        if split < 0:
            if len(self.buffer) > MAX_HEADER_BYTES:
                raise RiderError("stream_header_size", 502)
            return False
        if split > MAX_HEADER_BYTES:
            raise RiderError("stream_header_size", 502)
        raw = bytes(self.buffer[:split])
        del self.buffer[: split + 4]
        headers = self._parse_headers(raw)
        length = headers.get("content-length", "")
        if not length.isascii() or not length.isdigit():
            raise RiderError("stream_invalid_length", 502)
        self.length = int(length) if len(length) < 8 else MAX_PART_BYTES + 1
        if not 4 <= self.length <= MAX_PART_BYTES:
            raise RiderError("stream_input_size", 502)
        if headers.get("content-type", "").lower() != "image/jpeg":
            raise RiderError("stream_invalid_format", 502)
        self.headers = headers
        return True

    @staticmethod
    def _parse_headers(raw):
        """Preserve ASCII, header-name and duplicate validation order."""
        headers = {}
        try:
            for line in raw.decode("ascii").split("\r\n"):
                key, value = line.split(":", 1)
                key = key.strip().lower()
                if not re.fullmatch(r"[a-z0-9-]{1,64}", key) or key in headers:
                    raise ValueError("invalid or duplicate header")
                headers[key] = value.strip()
        except ValueError as exc:
            raise RiderError("stream_invalid_headers", 502) from exc
        return headers

    def _read_payload(self):
        """Publish only a complete bounded JPEG and reset the part state."""
        if len(self.buffer) < self.length + 2:
            return None
        payload = bytes(self.buffer[: self.length])
        if self.buffer[self.length : self.length + 2] != b"\r\n":
            raise RiderError("stream_invalid_length", 502)
        del self.buffer[: self.length + 2]
        if not payload.startswith(b"\xff\xd8") or not payload.endswith(b"\xff\xd9"):
            raise RiderError("stream_invalid_jpeg", 502)
        frame = self.headers, payload
        self.headers = None
        self.started = False
        return frame

    def finish(self):
        if self.headers is not None or (
            self.buffer.strip() if self.finished else self.buffer
        ):
            raise RiderError("stream_truncated", 502)
