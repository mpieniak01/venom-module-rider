"""Bounded host RAM for the exact source bytes of a displayed frame.

A ticket identifies one receipt, never the next image from the camera. No Pi
reads, disk writes or inference occur in this buffer.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any

from .errors import RiderError

MAX_FRAME_BYTES = 512 * 1024
MAX_CONTEXT_BYTES = 32 * 1024


@dataclass(frozen=True)
class CapturedFrame:
    key: str
    device_id: str
    payload: bytes
    mime: str
    sha256: str
    received_at: float
    received_mono: float
    context_json: bytes

    def context(self) -> dict[str, Any]:
        return json.loads(self.context_json)


class FrameBuffer:
    def __init__(
        self,
        *,
        clock=time.monotonic,
        wall=time.time,
        max_bytes=8 * 1024 * 1024,
        max_frames=32,
        ttl_s=30.0,
    ):
        if (
            type(max_bytes) is not int
            or type(max_frames) is not int
            or max_bytes < 1
            or max_frames < 1
            or not math.isfinite(ttl_s)
            or ttl_s <= 0
        ):
            raise ValueError("invalid frame buffer limits")
        self.clock, self.wall = clock, wall
        self.max_bytes, self.max_frames, self.ttl_s = max_bytes, max_frames, ttl_s
        self.frames: OrderedDict[str, CapturedFrame] = OrderedDict()
        self.identities: dict[tuple, str] = {}
        self.bytes = 0
        self.evicted = self.expired = self.rejected = 0

    @staticmethod
    def identity(device_id, context):
        return (
            device_id,
            context.get("run_id"),
            context.get("frame_id"),
            context.get("sequence"),
        )

    def _drop(self, *, expired=False):
        key, frame = self.frames.popitem(last=False)
        self.bytes -= len(frame.payload)
        identity = self.identity(frame.device_id, frame.context())
        if self.identities.get(identity) == key:
            del self.identities[identity]
        if expired:
            self.expired += 1
        else:
            self.evicted += 1

    def purge(self):
        while self.frames:
            first = next(iter(self.frames.values()))
            if self.clock() - first.received_mono <= self.ttl_s:
                break
            self._drop(expired=True)

    def offer(self, device_id, payload, mime, context=None) -> str | None:
        # A diagnostic metadata problem must never abort camera or inference.
        if mime not in {"image/jpeg", "image/png"} or not isinstance(payload, bytes):
            self.rejected += 1
            return None
        if not 0 < len(payload) <= min(MAX_FRAME_BYTES, self.max_bytes):
            self.rejected += 1
            return None
        if context is not None and not isinstance(context, dict):
            self.rejected += 1
            return None
        try:
            context_json = json.dumps(
                context or {}, allow_nan=False, separators=(",", ":"), sort_keys=True
            ).encode()
            if len(context_json) > MAX_CONTEXT_BYTES:
                raise ValueError("context too large")
            identity = self.identity(device_id, context or {})
            hash(identity)  # Reject malformed identities without retaining data.
        except (TypeError, ValueError):
            self.rejected += 1
            return None
        self.purge()
        while self.frames and (
            len(self.frames) >= self.max_frames
            or self.bytes + len(payload) > self.max_bytes
        ):
            self._drop()
        key = uuid.uuid4().hex
        frame = CapturedFrame(
            key,
            device_id,
            payload,
            mime,
            hashlib.sha256(payload).hexdigest(),
            self.wall(),
            self.clock(),
            context_json,
        )
        self.frames[key] = frame
        self.identities[identity] = key
        self.bytes += len(payload)
        return key

    def get(self, key: str) -> CapturedFrame:
        self.purge()
        if key not in self.frames:
            raise RiderError("samples_frame_expired", 409)
        return self.frames[key]

    def annotate_prediction(self, key, prediction):
        """A queued writer retains its earlier immutable metadata snapshot."""
        try:
            frame = self.get(key)
            context = {**frame.context(), "prediction": prediction}
            encoded = json.dumps(
                context, allow_nan=False, separators=(",", ":")
            ).encode()
            if len(encoded) > MAX_CONTEXT_BYTES:
                raise ValueError("prediction size")
            self.frames[key] = replace(frame, context_json=encoded)
            return True
        except (RiderError, ValueError, TypeError):
            self.rejected += 1
            return False

    def lookup(self, device_id, context) -> str | None:
        self.purge()
        return self.identities.get(self.identity(device_id, context))

    def status(self):
        self.purge()
        return {
            "bytes": self.bytes,
            "frames": len(self.frames),
            "max_bytes": self.max_bytes,
            "max_frames": self.max_frames,
            "ttl_s": self.ttl_s,
            "expired": self.expired,
            "evicted": self.evicted,
            "rejected": self.rejected,
        }
