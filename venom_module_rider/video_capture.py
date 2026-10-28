"""Light host-only test recording. GET-only; never starts camera, models or motion."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .errors import RiderError
from .local_api import local_api_origin, local_api_tls_context
from .samples_archive import SampleArchive, canonical
from .stream_contract import SourceDescription
from .stream_framing import MultipartJpegParser, boundary_from_type

PREFIX = "/api/v1/rider"
MIN_FREE = 64 * 1024**2


def new_directory(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("output_symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir(mode=0o700)
    return path


def save_json(path, value):
    with path.open("w", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def active_pipeline(pipeline):
    if (
        pipeline.get("state") not in {"active", "waiting_frame"}
        or not pipeline.get("camera_session")
        or (pipeline.get("active_profile") or pipeline.get("profile") or {}).get(
            "remote_transport"
        )
        != "mjpeg"
    ):
        raise ValueError("camera_not_active_mjpeg")
    return (
        pipeline["camera_session"],
        pipeline["transport_epoch"],
        pipeline["revision"],
    )


async def snapshot(client):
    pipeline = (await client.get(PREFIX + "/camera/pipeline")).raise_for_status().json()
    active_pipeline(pipeline)
    navigation = (
        (await client.get(PREFIX + "/navigation/state")).raise_for_status().json()
    )
    return {"pipeline": pipeline, "navigation": navigation}


class VideoWriter:
    """Append only complete received JPEGs; journal offsets allow exact extraction."""

    def __init__(self, root, binding, max_bytes):
        self.root, self.binding, self.max_bytes = root, binding, max_bytes
        self.video = (root / "capture.mjpeg").open("xb")
        self.journal = (root / "frames.jsonl").open("x", encoding="utf-8")
        for name in ("capture.mjpeg", "frames.jsonl"):
            os.chmod(root / name, 0o600)
        self.count = self.size = self.gaps = self.duplicates = 0
        self.last = None
        self.start = time.monotonic()

    def append(self, headers, payload):
        source = SourceDescription.model_validate_json(headers["x-rider-source-json"])
        if (
            source.camera_session,
            source.transport_epoch,
            source.pipeline_revision,
        ) != self.binding:
            raise ValueError("source_changed")
        if headers.get("x-rider-placeholder") == "true":
            raise ValueError("placeholder_frame")
        previous = self.last
        if previous and source.sequence <= previous.sequence:
            if source.reference() == previous.reference():
                self.duplicates += 1
                return None
            raise ValueError("source_order_changed")
        if previous and source.captured_monotonic_ns <= previous.captured_monotonic_ns:
            raise ValueError("source_time_changed")
        if self.count >= 1000:
            return "frame_limit"
        if self.size + len(payload) > self.max_bytes:
            return "size_limit"
        if shutil.disk_usage(self.root).free < len(payload) + MIN_FREE:
            return "disk_limit"
        gap = source.sequence - previous.sequence - 1 if previous else 0
        row = {
            "index": self.count,
            "offset": self.size,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "source": source.model_dump(mode="json"),
            "sequence_gap": gap,
            "received_elapsed_s": time.monotonic() - self.start,
            "reported_age_ms": headers.get("x-rider-frame-age-ms"),
            "age_source": headers.get("x-rider-frame-age-source"),
            "reported_stale": headers.get("x-rider-stale"),
        }
        # The host RAW route may already orient/re-encode the producer's JPEG.
        # Keep source.sha256 separate from the hash of the recorded API bytes.
        self.video.write(payload)
        self.video.flush()
        self.journal.write(json.dumps(row, allow_nan=False) + "\n")
        self.journal.flush()
        self.count += 1
        self.size += len(payload)
        self.gaps += gap
        self.last = source
        return None

    def close(self):
        self.video.close()
        self.journal.close()


async def record(client, output, seconds=60, max_mib=128, study_session=None):
    if not 1 <= seconds <= 300 or not 1 <= max_mib <= 1024:
        raise ValueError("recording_limits")
    if study_session is not None and (
        len(study_session) != 32
        or any(c not in "0123456789abcdef" for c in study_session)
    ):
        raise ValueError("study_session_invalid")
    before = await snapshot(client)
    binding = active_pipeline(before["pipeline"])
    root = new_directory(output)
    writer = VideoWriter(root, binding, max_mib * 1024**2)
    meta = {
        "schema_version": 1,
        "kind": "rider-test-video",
        "started_at": datetime.now(UTC).isoformat(),
        "state": "recording",
        "seconds_limit": seconds,
        "max_mib": max_mib,
        "before": before,
        "study_session_id": study_session,
        "continuous_telemetry_recorded": False,
        "pixel_scope": "Venom canonical RAW API bytes, no recorder re-encoding",
        "clock_scope": "source monotonic file publication; not sensor exposure",
        "transport_scope": "latest-only shared native hub; not lossless camera capture",
    }
    save_json(root / "recording.json", meta)
    state, error = "interrupted", None
    try:
        async with asyncio.timeout(seconds):
            async with client.stream("GET", PREFIX + "/camera/stream/raw") as response:
                response.raise_for_status()
                parser = MultipartJpegParser(
                    boundary_from_type(response.headers.get("content-type", ""))
                )
                async for chunk in response.aiter_bytes(16384):
                    for headers, payload in parser.feed(chunk):
                        reason = writer.append(headers, payload)
                        if reason:
                            state = reason
                            return root
                parser.finish()
                state = "stream_ended"
    except TimeoutError:
        state = "duration_limit"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        state, error = "error", type(exc).__name__ + ":" + str(exc)[:160]
        raise
    finally:
        writer.close()
        meta.update(
            state=state,
            error=error,
            frames=writer.count,
            bytes=writer.size,
            sequence_gaps=writer.gaps,
            duplicates=writer.duplicates,
            elapsed_s=time.monotonic() - writer.start,
        )
        save_json(root / "recording.json", meta)
    return root


def extract(source, output):
    """Validate all committed bytes; build a standard historical sample import."""
    source = Path(source)
    meta = json.loads((source / "recording.json").read_text())
    if meta.get("kind") != "rider-test-video" or meta.get("schema_version") != 1:
        raise ValueError("recording_format")
    if meta.get("state") == "recording":
        raise ValueError("recording_still_active")
    journal = source / "frames.jsonl"
    if journal.stat().st_size > 32 * 1024**2:
        raise ValueError("recording_journal_size")
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    if not rows or len(rows) > 1000 or len(rows) != meta["frames"]:
        raise ValueError("recording_frame_count")
    root = new_directory(output)
    (root / "raw").mkdir()
    (root / "frames").mkdir()
    samples, replay, timeline = [], [], []
    offset, last = 0, None
    with (source / "capture.mjpeg").open("rb") as video:
        for i, row in enumerate(rows):
            if (
                row["index"] != i
                or row["offset"] != offset
                or type(row["bytes"]) is not int
                or not 4 <= row["bytes"] <= 512 * 1024
            ):
                raise ValueError("recording_offsets")
            payload = video.read(row["bytes"])
            sha = hashlib.sha256(payload).hexdigest()
            if (
                sha != row["sha256"]
                or not payload.startswith(b"\xff\xd8")
                or not payload.endswith(b"\xff\xd9")
            ):
                raise ValueError("recording_hash")
            desc = SourceDescription.model_validate(row["source"])
            key = (desc.camera_session, desc.transport_epoch, desc.pipeline_revision)
            if last and (
                key != last[0]
                or desc.sequence <= last[1]
                or desc.captured_monotonic_ns <= last[2]
            ):
                raise ValueError("recording_identity")
            last = key, desc.sequence, desc.captured_monotonic_ns
            offset += len(payload)
            raw = root / "raw" / (sha + ".bin")
            if not raw.exists():
                raw.write_bytes(payload)
            frame = root / "frames" / f"{i:06d}.jpg"
            os.link(raw, frame)
            samples.append(
                {
                    "sample_id": uuid.uuid4().hex,
                    "raw": {
                        "file": f"raw/{sha}.bin",
                        "sha256": sha,
                        "bytes": len(payload),
                        "format": "image/jpeg",
                    },
                    "labels": [],
                    "frame_id": desc.frame_id,
                    "sequence": desc.sequence,
                    "source_session": desc.camera_session,
                    "source_context": {
                        "input_kind": "historical-import",
                        "source_identity": desc.reference().model_dump(mode="json"),
                        "source_timestamp": str(desc.captured_monotonic_ns),
                        "timestamp_source": "source-monotonic-file-publication",
                        "video_index": i,
                        "study_session_id": meta.get("study_session_id"),
                    },
                }
            )
            replay.append(
                {"file": f"frames/{i:06d}.jpg", "sha256": sha, "expected": "unlabelled"}
            )
            timeline.append(
                {"sha256": sha, "captured_at": desc.captured_monotonic_ns / 1e9}
            )
        if video.read(1):
            raise ValueError("recording_uncommitted_tail")
    manifest = {
        "schema_version": 1,
        "kind": "rider-sample-session",
        "session": {
            "session_id": uuid.uuid4().hex,
            "source_device_id": "rider-history",
            "policy": {
                "scene_group": "test-video",
                "order_source": "manifest",
                "dataset_split": "unassigned",
            },
        },
        "samples": samples,
    }
    manifest["dataset_revision"] = hashlib.sha256(canonical(manifest)).hexdigest()
    SampleArchive._validate_manifest(manifest)
    save_json(root / "manifest.json", manifest)
    save_json(root / "replay-manifest.json", replay)
    save_json(root / "transport.json", {"stream": timeline})
    save_json(root / "recording.json", meta)
    return root


async def run_record(args):
    origin = local_api_origin(args.url)
    headers = {}
    token = os.getenv("RIDER_ACCESS_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    async with httpx.AsyncClient(
        base_url=origin,
        headers=headers,
        follow_redirects=False,
        trust_env=False,
        verify=local_api_tls_context() if origin.startswith("https://") else True,
        timeout=httpx.Timeout(5, read=3),
    ) as client:
        if args.action == "check":
            return await snapshot(client)
        path = args.output or Path.home() / ".local/share/venom/rider/test-videos" / (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        )
        root = await record(
            client, path, args.seconds, args.max_mib, args.study_session
        )
        return {
            "output": str(root),
            **json.loads((root / "recording.json").read_text()),
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8800")
    subs = parser.add_subparsers(dest="action", required=True)
    subs.add_parser("check")
    recording = subs.add_parser("record")
    recording.add_argument("--output", type=Path)
    recording.add_argument("--seconds", type=int, default=60)
    recording.add_argument("--max-mib", type=int, default=128)
    recording.add_argument("--study-session")
    extracting = subs.add_parser("extract")
    extracting.add_argument("source", type=Path)
    extracting.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = (
            {"output": str(extract(args.source, args.output))}
            if args.action == "extract"
            else asyncio.run(run_record(args))
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        print(json.dumps({"state": "interrupted"}))
        return 130
    except (OSError, ValueError, httpx.HTTPError, RiderError) as exc:
        # Never print HTTP request headers, tokens or a remote response body.
        reason = getattr(exc, "code", None)
        if isinstance(exc, ValueError) and str(exc).isidentifier():
            reason = str(exc)
        print(json.dumps({"error": type(exc).__name__, "reason": reason}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
