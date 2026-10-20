"""Host timing configuration and bounded diagnostics; never an actuator policy."""

from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import tempfile
import time
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path

from .errors import RiderError

# Milliseconds. Bounds limit host demand, not firmware sampling or motion leases.
FIELDS = {
    "control_ms": (500, 500, 1000),
    "calibration_ms": (1000, 500, 2000),
    "overview_ms": (2000, 1000, 5000),
    "idle_ms": (5000, 2000, 10000),
    "telemetry_ms": (2000, 500, 5000),
    "preview_ms": (500, 500, 2000),
}
DEFAULTS = {key: row[0] for key, row in FIELDS.items()}


def validate(values):
    if not isinstance(values, dict) or set(values) != set(FIELDS):
        raise RiderError("loop_timing_invalid", 422)
    for key, value in values.items():
        _, minimum, maximum = FIELDS[key]
        if (
            type(value) is not int
            or not minimum <= value <= maximum
            or (value - minimum) % 100
        ):
            raise RiderError("loop_timing_invalid", 422)
    return dict(values)


def persist(path, state):
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, delete=False
        ) as out:
            temporary = Path(out.name)
            json.dump(state, out, allow_nan=False)
            out.flush()
            os.fsync(out.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "p50": None, "p95": None, "max": None}

    def percentile(q):
        index = (len(ordered) - 1) * q
        lower = math.floor(index)
        return ordered[lower] + (ordered[math.ceil(index)] - ordered[lower]) * (
            index - lower
        )

    return {
        "count": len(ordered),
        "p50": percentile(0.5),
        "p95": percentile(0.95),
        "max": ordered[-1],
    }


class ReadMetrics:
    """Fixed resource names, 64 observations each, no payloads or device clocks."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.rows: dict[str, dict] = {}

    def row(self, name):
        if name not in self.rows:
            self.rows[name] = {
                "requests": 0,
                "errors": 0,
                "cache_hits": 0,
                "coalesced": 0,
                "duration_ms": deque(maxlen=64),
                "start_gaps_ms": deque(maxlen=64),
                "last_started": None,
            }
        return self.rows[name]

    def count(self, name, event):
        self.row(name)[event] += 1

    async def measure(
        self, name: str, operation: Callable[[], Awaitable[dict]]
    ) -> dict:
        row = self.row(name)
        started = self.clock()
        if row["last_started"] is not None:
            row["start_gaps_ms"].append(max(0, (started - row["last_started"]) * 1000))
        row["last_started"] = started
        row["requests"] += 1
        try:
            return await operation()
        except BaseException:
            row["errors"] += 1
            raise
        finally:
            row["duration_ms"].append(max(0, (self.clock() - started) * 1000))

    def snapshot(self):
        return {
            name: {
                **{
                    key: row[key]
                    for key in ("requests", "errors", "cache_hits", "coalesced")
                },
                "duration_ms": distribution(row["duration_ms"]),
                "start_gaps_ms": distribution(row["start_gaps_ms"]),
            }
            for name, row in self.rows.items()
        }


class LoopTiming:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.revision = 0
        self.values = dict(DEFAULTS)
        self.warning = None
        self.lock = asyncio.Lock()
        if path is not None and path.exists():
            try:
                if path.stat().st_size > 4096:
                    raise ValueError("oversized")
                saved = json.loads(path.read_text())
                if (
                    not isinstance(saved, dict)
                    or set(saved) != {"version", "revision", "values"}
                    or type(saved["version"]) is not int
                    or saved["version"] != 1
                    or type(saved["revision"]) is not int
                    or not 0 <= saved["revision"] < 2**53
                ):
                    raise ValueError("invalid")
                self.values = validate(saved["values"])
                self.revision = saved["revision"]
            except (OSError, ValueError, RiderError):
                self.warning = "loop_timing_restore_failed"

    def snapshot(self):
        return {
            "schema_version": 1,
            "scope": "venom-host",
            "revision": self.revision,
            "effective": dict(self.values),
            "warning": self.warning,
            "persistent": self.path is not None,
            "fields": {
                key: {
                    "default": default,
                    "minimum": minimum,
                    "maximum": maximum,
                    "step": 100,
                    "unit": "ms",
                }
                for key, (default, minimum, maximum) in FIELDS.items()
            },
        }

    async def update(self, revision, values):
        values = validate(values)
        async with self.lock:
            if type(revision) is not int or revision != self.revision:
                raise RiderError("loop_timing_conflict", 409)
            if self.revision >= 2**53 - 1:
                raise RiderError("loop_timing_conflict", 409)
            state = {"version": 1, "revision": self.revision + 1, "values": values}
            if self.path is not None:
                task = asyncio.create_task(asyncio.to_thread(persist, self.path, state))
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    # Finish the tiny atomic write under the same lock, then publish
                    # its effective revision before acknowledging cancellation.
                    await task
                    self.revision = state["revision"]
                    self.values = dict(values)
                    self.warning = None
                    raise
                except OSError as exc:
                    raise RiderError("loop_timing_persistence_failed", 503) from exc
            self.revision += 1
            self.values = copy.deepcopy(values)
            self.warning = None
            return self.snapshot()

    def polling(self, baseline):
        return {
            key: self.values[key]
            if key in self.values
            else self.values["control_ms"]
            if key == "vision_ms"
            else self.values["idle_ms"]
            for key in baseline
        }
