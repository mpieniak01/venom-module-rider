"""Immutable source references and lazy MJPEG frame ranges; never extract RAW JPEGs."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from pathlib import Path

from .errors import RiderError
from .samples_archive import SampleArchive, canonical, sha256_stream

MAX_VIDEO_FRAMES = 10000
MAX_INDEX_LINE = 65536
MAX_SOURCE_BYTES = 128 * 1024**2
VIDEO_FILES = {"capture.mjpeg", "frames.jsonl", "video.json"}


def source_path(root, session_id, name):
    if (
        not isinstance(session_id, str)
        or re.fullmatch(r"[a-f0-9]{32}", session_id) is None
        or name not in VIDEO_FILES
    ):
        raise RiderError("analysis_video_source_invalid", 400)
    root = Path(root)
    if not root.is_absolute() or root.is_symlink() or (root / "videos").is_symlink():
        raise RiderError("analysis_video_source_invalid", 409)
    return SampleArchive.safe_file(root / "videos" / session_id, name)


def freeze_video(store, session_id):
    """Freeze the exact closed video/index/metadata without duplicating frame data."""
    session = store.session(session_id)
    if session["state"] == "collecting":
        raise RiderError("analysis_video_recording", 409)
    video = session.get("video")
    if not isinstance(video, dict) or not video.get("frames"):
        raise RiderError("analysis_empty_video", 409)
    files = {}
    for name, path in store.video.artifacts(session_id):
        with path.open("rb") as stream:
            digest = sha256_stream(stream)
        files[name] = {"sha256": digest, "bytes": path.stat().st_size}
    descriptor = {
        "version": 1,
        "kind": "session-video",
        "session_id": session_id,
        "frames": video["frames"],
        "files": files,
        "frame_limit": MAX_VIDEO_FRAMES,
        "byte_limit": MAX_SOURCE_BYTES,
    }
    descriptor["sha256"] = hashlib.sha256(canonical(descriptor)).hexdigest()
    inputs = VideoInputs(store.root, descriptor)
    return session, descriptor, inputs


class VideoInputs(Sequence):
    """Keep only line offsets in memory; retrieve one committed frame descriptor."""

    def __init__(self, root, descriptor):
        self.root, self.source = Path(root), descriptor
        self.offsets = []
        self.first_mono = None
        if (
            isinstance(descriptor, dict)
            and type(descriptor.get("frames")) is int
            and descriptor["frames"] > MAX_VIDEO_FRAMES
        ):
            raise RiderError("analysis_video_frame_limit", 413)
        if (
            not isinstance(descriptor, dict)
            or descriptor.get("kind") != "session-video"
            or descriptor.get("version") != 1
            or type(descriptor.get("frames")) is not int
            or not 1 <= descriptor["frames"] <= MAX_VIDEO_FRAMES
            or not isinstance(descriptor.get("files"), dict)
            or set(descriptor["files"]) != VIDEO_FILES
        ):
            raise RiderError("analysis_video_source_invalid", 409)
        expected_digest = hashlib.sha256(
            canonical(
                {key: value for key, value in descriptor.items() if key != "sha256"}
            )
        ).hexdigest()
        if descriptor.get("sha256") != expected_digest:
            raise RiderError("analysis_video_source_changed", 409)
        self.session_id = descriptor["session_id"]
        self.verify()
        self._index()

    def verify(self):
        total = 0
        for name, info in self.source["files"].items():
            if (
                not isinstance(info, dict)
                or type(info.get("bytes")) is not int
                or not 0 <= info["bytes"] <= MAX_SOURCE_BYTES
                or not isinstance(info.get("sha256"), str)
                or re.fullmatch(r"[a-f0-9]{64}", info["sha256"]) is None
            ):
                raise RiderError("analysis_video_source_invalid", 409)
            path = source_path(self.root, self.session_id, name)
            total += info["bytes"]
            with path.open("rb") as stream:
                if (
                    path.stat().st_size != info["bytes"]
                    or sha256_stream(stream) != info["sha256"]
                ):
                    raise RiderError("analysis_video_source_changed", 409)
        if total > MAX_SOURCE_BYTES:
            raise RiderError("analysis_video_source_limit", 413)

    def _index(self):
        offset = 0
        previous = None
        with source_path(self.root, self.session_id, "frames.jsonl").open(
            "rb"
        ) as stream:
            while True:
                line_offset = stream.tell()
                line = stream.readline(MAX_INDEX_LINE + 1)
                if not line:
                    break
                entry = self._entry(line)
                received = entry["received_monotonic_s"]
                if (
                    entry["index"] != len(self.offsets)
                    or entry["offset"] != offset
                    or previous is not None
                    and received <= previous
                    or len(self.offsets) >= MAX_VIDEO_FRAMES
                ):
                    raise RiderError("analysis_video_index_invalid", 409)
                self.first_mono = (
                    received if self.first_mono is None else self.first_mono
                )
                self.offsets.append(line_offset)
                offset += entry["bytes"]
                previous = received
        if (
            len(self.offsets) != self.source["frames"]
            or offset != self.source["files"]["capture.mjpeg"]["bytes"]
        ):
            raise RiderError("analysis_video_index_invalid", 409)

    @staticmethod
    def _entry(line):
        try:
            if len(line) > MAX_INDEX_LINE or not line.endswith(b"\n"):
                raise ValueError("line")
            entry = json.loads(line)
            if (
                not isinstance(entry, dict)
                or type(entry.get("index")) is not int
                or type(entry.get("offset")) is not int
                or entry["offset"] < 0
                or type(entry.get("bytes")) is not int
                or not 0 < entry["bytes"] <= 512 * 1024
                or not isinstance(entry.get("sha256"), str)
                or re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]) is None
                or not isinstance(entry.get("received_monotonic_s"), (int, float))
                or not math.isfinite(entry["received_monotonic_s"])
                or not isinstance(entry.get("context", {}), dict)
            ):
                raise ValueError("entry")
            return entry
        except (ValueError, TypeError) as exc:
            raise RiderError("analysis_video_index_invalid", 409) from exc

    def __len__(self):
        return len(self.offsets)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[value] for value in range(*index.indices(len(self)))]
        if type(index) is not int or not 0 <= index < len(self):
            raise IndexError(index)
        with source_path(self.root, self.session_id, "frames.jsonl").open(
            "rb"
        ) as stream:
            stream.seek(self.offsets[index])
            entry = self._entry(stream.readline(MAX_INDEX_LINE + 1))
        if entry["index"] != index:
            raise RiderError("analysis_video_source_changed", 409)
        context = entry.get("context", {})
        identity = hashlib.sha256(
            canonical([self.session_id, self.source["sha256"], index])
        ).hexdigest()[:32]
        return {
            "sample_id": identity,
            "input_kind": "video",
            "source_index": index,
            "source_time_s": entry["received_monotonic_s"] - self.first_mono,
            "source_received_monotonic_s": entry["received_monotonic_s"],
            "source_received_at": entry.get("received_at"),
            "source_sequence": context.get("sequence"),
            "source_gap_before_s": entry.get("gap_before_s"),
            "source_sequence_gap": entry.get("sequence_gap"),
            "source_device_id": context.get("source_device_id", "recorded-video"),
            "run_id": self.source["sha256"],
            "sequence": context.get("sequence"),
            "frame_id": context.get("frame_id") or identity,
            "labels": [],
            "source_context": context,
            "raw": {
                "kind": "video-frame",
                "format": "image/jpeg",
                "sha256": entry["sha256"],
                "bytes": entry["bytes"],
                "session_id": self.session_id,
                "offset": entry["offset"],
            },
        }


def inputs_for(root, context):
    source = context.get("input_source")
    if isinstance(source, dict) and source.get("kind") == "session-video":
        return VideoInputs(root, source)
    return context["inputs"]


def input_at(root, context, index):
    values = inputs_for(root, context)
    if type(index) is not int or not 0 <= index < len(values):
        raise RiderError("analysis_frame_not_found", 404)
    return values[index]


def payload_for(root, sample):
    raw = sample["raw"]
    if raw.get("kind") == "video-frame":
        path = source_path(root, raw["session_id"], "capture.mjpeg")
        with path.open("rb") as stream:
            stream.seek(raw["offset"])
            payload = stream.read(raw["bytes"])
    else:
        payload = SampleArchive.safe_file(
            Path(root) / "blobs", raw["sha256"] + ".bin"
        ).read_bytes()
    if (
        len(payload) != raw["bytes"]
        or hashlib.sha256(payload).hexdigest() != raw["sha256"]
    ):
        raise RiderError("samples_source_corrupt", 409)
    return payload
