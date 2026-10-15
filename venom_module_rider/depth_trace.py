"""Opt-in bounded recording of the exact depth-inference inputs, off by default."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import queue
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_RAW_BYTES = 512 * 1024
MAX_METADATA_BYTES = 32 * 1024
MAX_ARCHIVE_BYTES = 5 * 1024 * 1024
ALARM_SLOTS = 12
CLEAR_SLOTS = 4


@dataclass(frozen=True)
class _Capture:
    trace_id: str
    payload: bytes
    depth: Any
    metadata: bytes
    alarm: bool


class DepthTraceRecorder:
    """One lazy daemon, two queued captures, 12 alarm and four CLEAR slots.

    A returned id means queued, not persisted. Recording errors never escape
    submit or stop the inference. CLEAR cannot evict alarms or overwrite their
    slots; alarms evict queued CLEAR first, then the oldest queued alarm.
    """

    def __init__(self, directory: Path, *, clock: Callable[[], float] = time.monotonic):
        self.directory = directory
        self.clock = clock
        self.failures = 0
        self.last_error: str | None = None
        self._queue: queue.Queue[_Capture] = queue.Queue(maxsize=2)
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_clear = -math.inf
        self._slots = {True: 0, False: 0}

    def submit(self, payload, depth, assessment, corridor, settings) -> str | None:
        if self._closed.is_set() or not self._lock.acquire(blocking=False):
            return None
        try:
            alarm = assessment.get("state") == "obstacle"
            if not alarm and assessment.get("state") != "clear":
                return None
            now = self.clock()
            if not alarm and now - self._last_clear < 1.0:
                return None
            capture = self._capture(
                payload, depth, assessment, corridor, settings, alarm
            )
            if capture is None:
                return None
            try:
                self._queue.put_nowait(capture)
            except queue.Full:
                if not alarm:
                    return None
                pending = []
                for _ in range(2):
                    try:
                        pending.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
                    self._queue.task_done()
                if pending:
                    drop = next(
                        (i for i, item in enumerate(pending) if not item.alarm), 0
                    )
                    pending.pop(drop)
                for item in pending:
                    self._queue.put_nowait(item)
                self._queue.put_nowait(capture)
            if not alarm:
                self._last_clear = now
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="rider-depth-trace", daemon=True
                )
                self._thread.start()
            return capture.trace_id
        except Exception as exc:
            self._failed(exc)
            return None
        finally:
            self._lock.release()

    @staticmethod
    def _capture(payload, depth, assessment, corridor, settings, alarm):
        import numpy as np

        if (
            not isinstance(payload, (bytes, bytearray))
            or not 0 < len(payload) <= MAX_RAW_BYTES
        ):
            return None
        if not isinstance(depth, np.ndarray) or depth.ndim != 2:
            return None
        height, width = depth.shape
        if not 32 <= height <= 720 or not 32 <= width <= 1280:
            return None
        if len(corridor) != 4 or not all(math.isfinite(float(v)) for v in corridor):
            return None
        x0, y0, x1, y1 = corridor
        if not 0 <= x0 < x1 <= 1 or not 0 <= y0 < y1 <= 1:
            return None
        if depth.dtype not in (np.dtype("float32"), np.dtype("float64")):
            return None
        snapshot = np.array(depth, copy=True, order="C")
        if not np.isfinite(snapshot).all() or (snapshot <= 0).any():
            return None
        config = (
            settings.model_dump(mode="json")
            if hasattr(settings, "model_dump")
            else settings
        )
        trace_id = uuid.uuid4().hex
        metadata = json.dumps(
            {
                "schema_version": 1,
                "trace_id": trace_id,
                "recorded_at": time.time(),
                "corridor": list(corridor),
                "settings": config,
                "assessment": {
                    k: v for k, v in assessment.items() if not k.startswith("_")
                },
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(metadata) > MAX_METADATA_BYTES:
            return None
        return _Capture(trace_id, bytes(payload), snapshot, metadata, alarm)

    def _failed(self, exc: Exception) -> None:
        self.failures += 1
        self.last_error = type(exc).__name__

    def _run(self) -> None:
        while not self._closed.is_set() or not self._queue.empty():
            try:
                capture = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._persist(capture)
            except Exception as exc:
                self._failed(exc)
            finally:
                self._queue.task_done()

    def _persist(self, capture: _Capture) -> None:
        import numpy as np

        metadata = json.loads(capture.metadata)
        metadata["raw"] = {
            "file": "frame.bin",
            "sha256": hashlib.sha256(capture.payload).hexdigest(),
            "bytes": len(capture.payload),
        }
        metadata["depth"] = {
            "file": "depth.npz",
            "sha256": hashlib.sha256(capture.depth.tobytes()).hexdigest(),
            "shape": list(capture.depth.shape),
            "dtype": str(capture.depth.dtype),
        }
        depth_stream = io.BytesIO()
        np.savez_compressed(depth_stream, depth=capture.depth)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as stream:
            stream.writestr("frame.bin", capture.payload)
            stream.writestr("depth.npz", depth_stream.getvalue())
            stream.writestr(
                "manifest.json", json.dumps(metadata, allow_nan=False, sort_keys=True)
            )
        if archive.tell() > MAX_ARCHIVE_BYTES:
            raise ValueError("depth_trace_archive_bounds")
        alarm = capture.alarm
        count = ALARM_SLOTS if alarm else CLEAR_SLOTS
        index = self._slots[alarm] % count
        kind = "alarm" if alarm else "clear"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.directory, prefix=".depth-trace-", suffix=".tmp", delete=False
            ) as output:
                temporary = output.name
                output.write(archive.getbuffer())
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.directory / f"{kind}-{index:02d}.zip")
            temporary = None
            self._slots[alarm] += 1
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def close(self, timeout: float = 2.0) -> None:
        """Drain queued work on shutdown; a slow writer never blocks indefinitely."""
        with self._lock:
            self._closed.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)


_recorder: DepthTraceRecorder | None = None
_recorder_lock = threading.Lock()


def submit(payload, depth, assessment, corridor, settings) -> str | None:
    """Queue an exact capture when RIDER_DEPTH_TRACE_DIR is explicitly configured."""
    global _recorder

    directory = os.getenv("RIDER_DEPTH_TRACE_DIR", "")
    if not directory or not _recorder_lock.acquire(blocking=False):
        return None
    try:
        if _recorder is None:
            _recorder = DepthTraceRecorder(Path(directory).expanduser())
        return _recorder.submit(payload, depth, assessment, corridor, settings)
    except Exception:
        return None
    finally:
        _recorder_lock.release()
