"""Bounded in-memory stage timings for the recording lane."""

import math
import threading
import time
from collections import deque
from contextlib import contextmanager


class RecordingMetrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.values = {}
        self.counts = {}

    def record(self, name, elapsed_ms):
        with self.lock:
            self.values.setdefault(name, deque(maxlen=64)).append(max(0, elapsed_ms))
            self.counts[name] = self.counts.get(name, 0) + 1

    @contextmanager
    def track(self, name):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, (time.perf_counter() - started) * 1000)

    def snapshot(self):
        with self.lock:
            result = {}
            for name, values in self.values.items():
                ordered = sorted(values)
                result[name] = {
                    "count": self.counts[name],
                    "samples": len(ordered),
                    "p50": round(ordered[max(0, math.ceil(len(ordered) * 0.5) - 1)], 2),
                    "p95": round(
                        ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)], 2
                    ),
                    "max": round(ordered[-1], 2),
                }
            return result
