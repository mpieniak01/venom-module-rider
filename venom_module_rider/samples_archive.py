"""Versioned session export and validated historical sources; no Pi transport."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Protocol

from .errors import RiderError
from .evidence_store import EvidenceStore, validate_extension

MAX_MANIFEST_BYTES = 40 * 1024 * 1024
MAX_EXPORTS = 2


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


class ByteReader(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


def sha256_stream(stream: ByteReader) -> str:
    """Hash ordinary and ZIP streams in bounded blocks, without readinto()."""
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


class SampleArchive:
    def __init__(self, store):
        self.store = store
        self.directory = store.root / "exports"
        if self.directory.is_symlink():
            raise RiderError("samples_root_invalid", 400)
        self.directory.mkdir(mode=0o700, exist_ok=True)

    def _export_slot(self):
        now = time.time()
        active = 0
        for path in self.directory.glob("rider-export-*.zip"):
            if path.is_symlink() or now - path.stat().st_mtime > 600:
                path.unlink(missing_ok=True)
            else:
                active += 1
        if active >= MAX_EXPORTS:
            raise RiderError("samples_export_busy", 429)

    def snapshot(self, session_id):
        # One WAL read transaction fixes samples and label revisions together.
        with self.store.db() as db:
            db.execute("BEGIN")
            session = self.store._session(db, session_id)
            samples = []
            for row in db.execute(
                "SELECT id,metadata FROM samples WHERE session_id=? ORDER BY ordinal",
                (session_id,),
            ).fetchall():
                sample = json.loads(row["metadata"])
                sample["labels"] = [
                    json.loads(label[0])
                    for label in db.execute(
                        "SELECT body FROM labels WHERE sample_id=? ORDER BY revision",
                        (row["id"],),
                    ).fetchall()
                ]
                sample["raw"] = {
                    **sample["raw"],
                    "file": f"raw/{sample['raw']['sha256']}.bin",
                }
                samples.append(sample)
            evidence = EvidenceStore.snapshot(db, session_id)
        dataset = {
            "schema_version": 1,
            "kind": "rider-sample-session",
            "session": session,
            "samples": samples,
        }
        if evidence is not None:
            dataset["evidence"] = evidence
        video = self.store.video.artifacts(session_id)
        if video:
            files = []
            for name, path in video:
                with path.open("rb") as stream:
                    digest = sha256_stream(stream)
                files.append(
                    {
                        "file": "video/" + name,
                        "bytes": path.stat().st_size,
                        "sha256": digest,
                    }
                )
            dataset["video"] = {"version": 1, "files": files}
        raw = canonical(dataset)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise RiderError("samples_export_size", 413)
        return dataset, hashlib.sha256(raw).hexdigest()

    def export(self, session_id):
        lock = self.directory / "export.lock"
        if lock.is_symlink():
            raise RiderError("samples_root_invalid", 400)
        with lock.open("a+b") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RiderError("samples_export_busy", 429) from exc
            try:
                return self._export(session_id)
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _export(self, session_id):
        self._export_slot()
        dataset, revision = self.snapshot(session_id)
        dataset["dataset_revision"] = revision
        dataset["exported_at"] = self.store.wall()
        manifest = canonical(dataset)
        size = sum(
            {
                row["raw"]["sha256"]: row["raw"]["bytes"] for row in dataset["samples"]
            }.values()
        )
        video_size = sum(
            row["bytes"] for row in dataset.get("video", {}).get("files", [])
        )
        self.store._space(size + len(manifest) + video_size)
        with tempfile.NamedTemporaryFile(
            dir=self.directory, prefix="rider-export-", suffix=".zip", delete=False
        ) as temporary:
            path = Path(temporary.name)
        try:
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("manifest.json", manifest)
                seen = set()
                for row in dataset["samples"]:
                    digest = row["raw"]["sha256"]
                    if digest in seen:
                        continue
                    payload, mime = self.store.original(row["sample_id"])
                    if (
                        mime != row["raw"]["format"]
                        or len(payload) != row["raw"]["bytes"]
                    ):
                        raise RiderError("samples_source_corrupt", 409)
                    archive.writestr(row["raw"]["file"], payload)
                    seen.add(digest)
                for row in dataset.get("video", {}).get("files", []):
                    source = self.store.video.path(
                        session_id, row["file"].split("/")[-1]
                    )
                    if source.stat().st_size != row["bytes"]:
                        raise RiderError("samples_video_invalid", 409)
                    with source.open("rb") as stream:
                        if sha256_stream(stream) != row["sha256"]:
                            raise RiderError("samples_video_invalid", 409)
                    archive.write(source, row["file"])
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
            return path, revision
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    def safe_file(root, name):
        if not isinstance(name, str):
            raise RiderError("samples_import_path", 400)
        relative = PurePosixPath(name)
        if (
            not isinstance(name, str)
            or not name
            or "\\" in name
            or relative.is_absolute()
            or any(part in {"..", "."} for part in relative.parts)
        ):
            raise RiderError("samples_import_path", 400)
        candidate = root / name
        if any(
            part.is_symlink()
            for part in [candidate, *candidate.parents]
            if part == root or root in part.parents
        ):
            raise RiderError("samples_import_path", 400)
        resolved = candidate.resolve()
        if root.resolve() not in resolved.parents or not resolved.is_file():
            raise RiderError("samples_import_path", 400)
        return resolved

    @staticmethod
    def validate_zip(source, max_bytes):
        if source.is_symlink() or not source.is_file():
            raise RiderError("samples_import_path", 400)
        try:
            with zipfile.ZipFile(source) as archive:
                entries = archive.infolist()
                if len(entries) > 1004 or len(
                    {entry.filename for entry in entries}
                ) != len(entries):
                    raise RiderError("samples_import_invalid", 400)
                if (
                    sum(entry.file_size for entry in entries)
                    > max_bytes + MAX_MANIFEST_BYTES
                ):
                    raise RiderError("samples_import_size", 413)
                for entry in entries:
                    name = PurePosixPath(entry.filename)
                    if (
                        name.is_absolute()
                        or ".." in name.parts
                        or "\\" in entry.filename
                        or stat.S_ISLNK(entry.external_attr >> 16)
                        or entry.flag_bits & 1
                    ):
                        raise RiderError("samples_import_path", 400)
                info = archive.getinfo("manifest.json")
                if info.file_size > MAX_MANIFEST_BYTES:
                    raise RiderError("samples_import_size", 413)
                manifest = json.loads(archive.read(info))
                SampleArchive._validate_manifest(manifest)
                expected = {"manifest.json"}
                for sample in manifest["samples"]:
                    raw = sample["raw"]
                    expected.add(raw["file"])
                    info = archive.getinfo(raw["file"])
                    if info.file_size != raw["bytes"]:
                        raise RiderError("samples_import_hash", 400)
                    payload = archive.read(info)
                    if hashlib.sha256(payload).hexdigest() != raw["sha256"]:
                        raise RiderError("samples_import_hash", 400)
                for video in manifest.get("video", {}).get("files", []):
                    expected.add(video["file"])
                    info = archive.getinfo(video["file"])
                    if info.file_size != video["bytes"]:
                        raise RiderError("samples_import_hash", 400)
                    with archive.open(info) as stream:
                        if sha256_stream(stream) != video["sha256"]:
                            raise RiderError("samples_import_hash", 400)
                if set(archive.namelist()) != expected:
                    raise RiderError("samples_import_invalid", 400)
                return manifest
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
            raise RiderError("samples_import_invalid", 400) from exc

    @staticmethod
    def _validate_manifest(manifest):
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != 1
            or manifest.get("kind") != "rider-sample-session"
            or not isinstance(manifest.get("samples"), list)
            or len(manifest["samples"]) > 1000
            or not isinstance(manifest.get("session"), dict)
            or not isinstance(manifest["session"].get("policy", {}), dict)
        ):
            raise RiderError("samples_import_invalid", 400)
        if "video" in manifest:
            video = manifest["video"]
            if (
                not isinstance(video, dict)
                or video.get("version") != 1
                or not isinstance(video.get("files"), list)
                or len(video["files"]) != 3
            ):
                raise RiderError("samples_video_invalid", 400)
            if {row.get("file") for row in video["files"] if isinstance(row, dict)} != {
                "video/video.json",
                "video/capture.mjpeg",
                "video/frames.jsonl",
            }:
                raise RiderError("samples_video_invalid", 400)
            for row in video["files"]:
                if (
                    not isinstance(row, dict)
                    or type(row.get("bytes")) is not int
                    or row["bytes"] < 0
                    or not isinstance(row.get("sha256"), str)
                    or re.fullmatch(r"[a-f0-9]{64}", row["sha256"]) is None
                ):
                    raise RiderError("samples_video_invalid", 400)
        if "evidence" in manifest:
            validate_extension(manifest["evidence"])
        seen = set()
        for sample in manifest["samples"]:
            if (
                not isinstance(sample, dict)
                or not isinstance(sample.get("raw"), dict)
                or not isinstance(sample.get("sample_id"), str)
                or re.fullmatch("[a-f0-9]{32}", sample["sample_id"]) is None
                or not isinstance(sample.get("labels", []), list)
                or not all(
                    isinstance(label, dict) for label in sample.get("labels", [])
                )
                or not isinstance(sample.get("source_context", {}), dict)
            ):
                raise RiderError("samples_import_invalid", 400)
            raw = sample["raw"]
            digest = raw["sha256"]
            if (
                not isinstance(digest, str)
                or re.fullmatch("[a-f0-9]{64}", digest) is None
                or raw["file"] != f"raw/{digest}.bin"
                or type(raw["bytes"]) is not int
                or not 0 < raw["bytes"] <= 512 * 1024
                or raw["format"] not in ("image/jpeg", "image/png")
                or sample["sample_id"] in seen
            ):
                raise RiderError("samples_import_invalid", 400)
            seen.add(sample["sample_id"])
        check = {
            key: value
            for key, value in manifest.items()
            if key not in {"dataset_revision", "exported_at"}
        }
        if hashlib.sha256(canonical(check)).hexdigest() != manifest.get(
            "dataset_revision"
        ):
            raise RiderError("samples_import_hash", 400)
