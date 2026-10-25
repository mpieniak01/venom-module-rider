"""Session-owned, bounded RAW MJPEG with durable frame timing and no hardware IO."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re

from .errors import RiderError
from .recording_metrics import RecordingMetrics

TARGET_FPS = 5


class SessionVideoStore:
    """All mutations run on the existing samples writer under its process lease."""

    def __init__(self, store):
        self.store = store
        self.metrics = RecordingMetrics()
        self.root = store.root / "videos"
        if self.root.is_symlink():
            raise RiderError("samples_root_invalid", 400)
        self.root.mkdir(mode=0o700, exist_ok=True)

    def folder(self, session_id):
        if not isinstance(session_id, str) or not re.fullmatch(
            r"[a-f0-9]{32}", session_id
        ):
            raise RiderError("samples_invalid_identifier", 400)
        path = self.root / session_id
        if self.root.is_symlink() or path.is_symlink():
            raise RiderError("samples_root_invalid", 400)
        return path

    def path(self, session_id, name):
        if name not in {"video.json", "capture.mjpeg", "frames.jsonl"}:
            raise RiderError("samples_video_invalid", 400)
        path = self.folder(session_id) / name
        if path.is_symlink():
            raise RiderError("samples_root_invalid", 400)
        return path

    def summary(self, session_id):
        try:
            path = self.path(session_id, "video.json")
            if not path.exists():
                return None
            if path.stat().st_size > 65536:
                raise ValueError("metadata size")
            row = json.loads(path.read_text())
            required = {
                "session_id",
                "state",
                "frames",
                "bytes",
                "index_bytes",
                "duration_s",
                "gaps",
                "duplicates",
                "error",
                "binding",
                "ended_at",
                "last_key",
                "first_received_mono",
                "last_received_mono",
                "last_sequence",
                "max_bytes",
                "target_fps",
            }
            if (
                not isinstance(row, dict)
                or not required <= set(row)
                or row["session_id"] != session_id
            ):
                raise ValueError("metadata shape")
            if row["state"] not in {
                "waiting_camera",
                "recording",
                "complete",
                "interrupted",
                "error",
                "limit",
            }:
                raise ValueError("metadata state")
            for key in (
                "frames",
                "bytes",
                "index_bytes",
                "gaps",
                "duplicates",
                "max_bytes",
                "target_fps",
            ):
                if type(row[key]) is not int or row[key] < 0:
                    raise ValueError("metadata count")
            if (
                not isinstance(row["duration_s"], (int, float))
                or not math.isfinite(row["duration_s"])
                or row["duration_s"] < 0
            ):
                raise ValueError("metadata duration")
            return row
        except (ValueError, TypeError, OSError, RiderError):
            # One corrupt session must not hide the rest of the archive.
            return {
                "state": "error",
                "error": "samples_video_invalid",
                "corrupt": True,
                "frames": 0,
                "bytes": 0,
                "duration_s": 0,
                "target_fps": TARGET_FPS,
                "gaps": 0,
                "ended_at": None,
                "session_id": session_id,
            }

    def _save(self, session_id, value):
        with self.metrics.track("metadata_fsync_ms"):
            return self._save_json(session_id, value)

    def _save_json(self, session_id, value):
        path = self.path(session_id, "video.json")
        temporary = self.folder(session_id) / "video.json.tmp"
        if temporary.is_symlink():
            raise RiderError("samples_root_invalid", 400)
        with temporary.open("w", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, allow_nan=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return value

    def start(self, session_id):
        old = self.summary(session_id)
        if old is not None:
            return old
        self.store._space(4096)
        folder = self.folder(session_id)
        folder.mkdir(mode=0o700)
        for name in ("capture.mjpeg", "frames.jsonl"):
            with self.path(session_id, name).open("xb") as stream:
                os.chmod(stream.name, 0o600)
        return self._save(
            session_id,
            {
                "version": 1,
                "session_id": session_id,
                "format": "mjpeg",
                "state": "waiting_camera",
                "target_fps": TARGET_FPS,
                "frames": 0,
                "bytes": 0,
                "index_bytes": 0,
                "duration_s": 0,
                "gaps": 0,
                "duplicates": 0,
                "error": None,
                "binding": None,
                "started_at": self.store.wall(),
                "ended_at": None,
                "last_key": None,
                "first_received_mono": None,
                "last_received_mono": None,
                "last_sequence": None,
                "max_bytes": self.store.limits.session_bytes,
                "pixel_scope": "host RAW API JPEG bytes, no recorder re-encoding",
                "clock_scope": "host receipt timing; not sensor exposure",
            },
        )

    def note(self, session_id, error, *, terminal=False):
        row = self.summary(session_id)
        if row is None or row["state"] in {"complete", "interrupted", "error", "limit"}:
            return row
        row.update(error=error, state="error" if terminal else "waiting_camera")
        return self._save(session_id, row)

    def append(self, session_id, frame):
        with self.metrics.track("append_total_ms"):
            return self._append(session_id, frame)

    def _append(self, session_id, frame):
        row = self.summary(session_id)
        if row is None or row["state"] in {"complete", "interrupted", "error", "limit"}:
            return row
        context = frame.context()
        if frame.mime != "image/jpeg" or context.get("stale") is True:
            return self.note(session_id, "samples_video_source_invalid")
        with self.metrics.track("quality_ms"):
            self.store.quality(frame)
        age = context.get("source_age_ms")
        if isinstance(age, (int, float)) and (not math.isfinite(age) or age > 1000):
            return self.note(session_id, "samples_video_source_stale")
        native = context.get("source_identity")
        native = native if isinstance(native, dict) else {}
        binding = {
            key: context.get(key) for key in ("source_session", "host_device_session")
        }
        binding.update(
            {
                key: native.get(key, context.get(key))
                for key in ("camera_session", "transport_epoch", "pipeline_revision")
            }
        )
        if row["binding"] is not None and binding != row["binding"]:
            return self.note(session_id, "samples_video_source_changed", terminal=True)
        if row["last_key"] == frame.key:
            row["duplicates"] += 1
            return self._save(session_id, row)
        previous = row["last_received_mono"]
        elapsed = None if previous is None else frame.received_mono - previous
        if elapsed is not None and elapsed <= 0:
            return self.note(session_id, "samples_video_source_time", terminal=True)
        if elapsed is not None and elapsed < 1 / TARGET_FPS - 0.005:
            return row
        sequence = context.get("sequence")
        last_sequence = row["last_sequence"]
        if (
            isinstance(sequence, (int, float))
            and isinstance(last_sequence, (int, float))
            and sequence <= last_sequence
        ):
            return self.note(session_id, "samples_video_source_time", terminal=True)
        sequence_gap = (
            max(0, sequence - last_sequence - 1)
            if isinstance(sequence, (int, float))
            and isinstance(last_sequence, (int, float))
            else None
        )
        entry = {
            "index": row["frames"],
            "offset": row["bytes"],
            "bytes": len(frame.payload),
            "sha256": hashlib.sha256(frame.payload).hexdigest(),
            "received_at": frame.received_at,
            "received_monotonic_s": frame.received_mono,
            "gap_before_s": elapsed,
            "sequence_gap": sequence_gap,
            "capture_key": frame.key,
            "context": context,
        }
        encoded = (
            json.dumps(entry, allow_nan=False, separators=(",", ":")) + "\n"
        ).encode()
        extra = len(frame.payload) + len(encoded)
        with self.store.db() as db:
            samples = db.execute(
                "SELECT COALESCE(SUM(bytes),0) FROM samples WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
            evidence = self.store.evidence.size(db, session_id)
        if self.size(session_id) + samples + evidence + extra > row["max_bytes"]:
            row.update(state="limit", error="samples_video_size_limit")
            return self._save(session_id, row)
        try:
            with self.metrics.track("quota_ms"):
                self.store._space(extra + 4096)
            with (
                self.metrics.track("video_fsync_ms"),
                self.path(session_id, "capture.mjpeg").open("ab") as stream,
            ):
                stream.write(frame.payload)
                stream.flush()
                os.fsync(stream.fileno())
            with (
                self.metrics.track("index_fsync_ms"),
                self.path(session_id, "frames.jsonl").open("ab") as stream,
            ):
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, RiderError) as exc:
            return self.note(
                session_id,
                exc.code
                if isinstance(exc, RiderError)
                else "samples_video_write_failed",
                terminal=True,
            )
        first = (
            frame.received_mono
            if row["first_received_mono"] is None
            else row["first_received_mono"]
        )
        row.update(
            state="recording",
            error=None,
            binding=binding,
            frames=row["frames"] + 1,
            bytes=row["bytes"] + len(frame.payload),
            index_bytes=row["index_bytes"] + len(encoded),
            last_key=frame.key,
            first_received_mono=first,
            last_received_mono=frame.received_mono,
            last_sequence=sequence,
            duration_s=max(0, frame.received_mono - first),
            gaps=row["gaps"] + int(elapsed is not None and elapsed > 0.6),
        )
        return self._save(session_id, row)

    def finish(self, session_id, *, interrupted=False):
        row = self.summary(session_id)
        if row is None or row.get("corrupt") or row["ended_at"] is not None:
            return row
        if row["state"] not in {"error", "limit"}:
            if not row["frames"]:
                row.update(state="error", error="samples_video_no_frames")
            else:
                row["state"] = (
                    "interrupted"
                    if interrupted or row["state"] == "waiting_camera" or row["error"]
                    else "complete"
                )
        row["ended_at"] = self.store.wall()
        return self._save(session_id, row)

    def recover(self):
        for folder in self.root.iterdir():
            if folder.is_symlink():
                raise RiderError("samples_root_invalid", 400)
            if folder.is_dir() and re.fullmatch(r"[a-f0-9]{32}", folder.name):
                row = self.summary(folder.name)
                if (
                    row is not None
                    and not row.get("corrupt")
                    and row["ended_at"] is None
                ):
                    try:
                        committed = self._scan(folder.name)
                        row.update(committed)
                        self._save(folder.name, row)
                    except RiderError:
                        self.note(
                            folder.name,
                            "samples_video_recovery_incomplete",
                            terminal=True,
                        )
                    self.finish(folder.name, interrupted=True)

    def _scan(self, session_id):
        """Validate committed frame boundaries without modifying source files."""
        count = offset = index_bytes = 0
        first = last = None
        gaps = 0
        try:
            with (
                self.path(session_id, "capture.mjpeg").open("rb") as video,
                self.path(session_id, "frames.jsonl").open("rb") as index,
            ):
                while line := index.readline(65537):
                    if len(line) > 65536 or not line.endswith(b"\n"):
                        raise ValueError("incomplete index")
                    entry = json.loads(line)
                    size = entry.get("bytes")
                    received = entry.get("received_monotonic_s")
                    if (
                        entry.get("index") != count
                        or entry.get("offset") != offset
                        or type(size) is not int
                        or not 0 < size <= 512 * 1024
                        or not isinstance(received, (int, float))
                        or not math.isfinite(received)
                    ):
                        raise ValueError("invalid index")
                    payload = video.read(size)
                    if len(payload) != size or hashlib.sha256(
                        payload
                    ).hexdigest() != entry.get("sha256"):
                        raise ValueError("corrupt video")
                    if last is not None and received <= last:
                        raise ValueError("invalid timing")
                    gaps += int(last is not None and received - last > 0.6)
                    first = received if first is None else first
                    last = received
                    offset += size
                    count += 1
                    index_bytes += len(line)
                if video.read(1):
                    raise ValueError("uncommitted tail")
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise RiderError("samples_video_invalid", 409) from exc
        return {
            "frames": count,
            "bytes": offset,
            "index_bytes": index_bytes,
            "duration_s": 0 if first is None or last is None else max(0, last - first),
            "gaps": gaps,
        }

    def size(self, session_id):
        return sum(
            path.stat().st_size
            for name in ("video.json", "capture.mjpeg", "frames.jsonl")
            if (path := self.path(session_id, name)).exists()
        )

    def download(self, session_id):
        self.store.session(session_id)
        row = self.summary(session_id)
        if row is not None and row.get("corrupt"):
            raise RiderError("samples_video_invalid", 409)
        if row is None or not row["frames"]:
            raise RiderError("samples_video_unavailable", 404)
        if row["ended_at"] is None:
            raise RiderError("samples_video_recording", 409)
        checked = self._scan(session_id)
        if checked["frames"] != row["frames"] or checked["bytes"] != row["bytes"]:
            raise RiderError("samples_video_invalid", 409)
        return self.path(session_id, "capture.mjpeg")

    def artifacts(self, session_id):
        row = self.summary(session_id)
        if row is not None and row.get("corrupt"):
            raise RiderError("samples_video_invalid", 409)
        if row is None:
            return []
        if row["ended_at"] is None:
            raise RiderError("samples_video_recording", 409)
        checked = self._scan(session_id)
        if checked["frames"] != row["frames"] or checked["bytes"] != row["bytes"]:
            raise RiderError("samples_video_invalid", 409)
        return [
            (name, self.path(session_id, name))
            for name in ("video.json", "capture.mjpeg", "frames.jsonl")
        ]
