"""Bounded, read-only indexed replay of finalized RAW video; never inference."""

import hashlib
import json
import math
import threading
from collections import OrderedDict

from .errors import RiderError

MAX_INDEX_BYTES = 32 * 1024 * 1024
MAX_FRAMES = 100_000


class SessionVideoReader:
    """Cache only indexes, not JPEG copies; changed source files invalidate replay."""

    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.cache: OrderedDict[str, dict] = OrderedDict()

    def _signature(self, session_id):
        try:
            return tuple(
                (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns)
                for name in ("video.json", "frames.jsonl", "capture.mjpeg")
                for p in [self.store.video.path(session_id, name)]
            )
        except OSError as exc:
            raise RiderError("samples_video_unavailable", 404) from exc

    def _index(self, session_id):
        self.store.session(session_id)
        with self.lock:
            signature = self._signature(session_id)
            cached = self.cache.get(session_id)
            if cached and cached["signature"] == signature:
                self.cache.move_to_end(session_id)
                return cached
            summary = self.store.video.summary(session_id)
            if summary is None or summary.get("corrupt"):
                raise RiderError("samples_video_invalid", 409)
            if summary.get("ended_at") is None:
                raise RiderError("samples_video_recording", 409)
            if signature[1][1] > MAX_INDEX_BYTES or summary["frames"] > MAX_FRAMES:
                raise RiderError("samples_video_index_limit", 413)
            # First read verifies every committed source frame, including its hash.
            self.store.video.download(session_id)
            try:
                with self.store.video.path(session_id, "frames.jsonl").open("rb") as f:
                    entries = [json.loads(line) for line in f]
                if (
                    len(entries) != summary["frames"]
                    or signature[1][1] != summary["index_bytes"]
                    or any(not isinstance(e.get("context"), dict) for e in entries)
                    or (
                        entries
                        and not math.isclose(
                            entries[-1]["received_monotonic_s"]
                            - entries[0]["received_monotonic_s"],
                            summary["duration_s"],
                        )
                    )
                    or signature != self._signature(session_id)
                ):
                    raise ValueError("changed video")
            except (OSError, ValueError) as exc:
                raise RiderError("samples_video_invalid", 409) from exc
            first = entries[0]["received_monotonic_s"] if entries else 0
            result = {
                "signature": signature,
                "entries": entries,
                "summary": summary,
                "first": first,
                "index_bytes": signature[1][1],
            }
            self.cache.pop(session_id, None)
            while self.cache and (
                len(self.cache) >= 4
                or sum(v["index_bytes"] for v in self.cache.values()) + signature[1][1]
                > MAX_INDEX_BYTES
            ):
                self.cache.popitem(last=False)
            self.cache[session_id] = result
            return result

    @staticmethod
    def _public(entry, first):
        context = entry.get("context") or {}
        return {
            "index": entry["index"],
            "source_time_s": entry["received_monotonic_s"] - first,
            "received_at": entry.get("received_at"),
            "frame_id": context.get("frame_id") or entry.get("capture_key"),
            "sequence": context.get("sequence"),
            "raw_sha256": entry["sha256"],
            "gap_before_s": entry.get("gap_before_s"),
            "sequence_gap": entry.get("sequence_gap"),
        }

    def page(self, session_id, *, offset=0, limit=500):
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 500
        ):
            raise RiderError("samples_video_page_invalid", 400)
        value = self._index(session_id)
        entries = value["entries"]
        end = min(len(entries), offset + limit)
        return {
            "session_id": session_id,
            "total": len(entries),
            "duration_s": value["summary"]["duration_s"],
            "frames": [self._public(e, value["first"]) for e in entries[offset:end]],
            "next_offset": end if end < len(entries) else None,
            "clock_scope": "host receipt timing; not sensor exposure",
        }

    def frame(self, session_id, index):
        if type(index) is not int or index < 0:
            raise RiderError("samples_video_frame_invalid", 400)
        value = self._index(session_id)
        if index >= len(value["entries"]):
            raise RiderError("samples_video_frame_not_found", 404)
        entry = value["entries"][index]
        try:
            with self.store.video.path(session_id, "capture.mjpeg").open("rb") as f:
                f.seek(entry["offset"])
                payload = f.read(entry["bytes"])
            if (
                len(payload) != entry["bytes"]
                or hashlib.sha256(payload).hexdigest() != entry["sha256"]
                or self._signature(session_id) != value["signature"]
            ):
                raise ValueError("changed video")
        except (OSError, ValueError) as exc:
            raise RiderError("samples_video_invalid", 409) from exc
        return payload, entry["sha256"]
