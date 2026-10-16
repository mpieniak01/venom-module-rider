"""Durable, Venom-owned dataset registry. No transport or actuator imports."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageStat

from .errors import RiderError
from .samples_frame import MAX_CONTEXT_BYTES, CapturedFrame


def encode(value):
    try:
        result = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        if len(result.encode()) > MAX_CONTEXT_BYTES:
            raise ValueError("metadata size")
        return result
    except (ValueError, TypeError) as exc:
        raise RiderError("samples_metadata_invalid", 400) from exc


def identifier(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise RiderError("samples_invalid_identifier", 400)
    return value


@dataclass(frozen=True)
class StorageLimits:
    total_bytes: int = 2 * 1024**3
    session_bytes: int = 128 * 1024**2
    min_free_bytes: int = 256 * 1024**2
    session_samples: int = 1000
    session_requests: int = 2000
    sessions: int = 500
    session_seconds: int = 1800


class SampleStore:
    def __init__(self, root: Path, *, wall=time.time, limits=None, recover=False):
        if not root.is_absolute():
            raise RiderError("samples_root_must_be_absolute", 400)
        self.root = root.resolve()
        checkout = Path(__file__).resolve().parents[1]
        if (
            self.root == checkout
            or checkout in self.root.parents
            or any(
                (parent / ".git").exists() for parent in [self.root, *self.root.parents]
            )
        ):
            raise RiderError("samples_root_inside_checkout", 400)
        self.wall, self.limits = wall, limits or StorageLimits()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.blobs = self.root / "blobs"
        if self.blobs.is_symlink():
            raise RiderError("samples_root_invalid", 400)
        self.blobs.mkdir(exist_ok=True, mode=0o700)
        self.path = self.root / "registry.sqlite3"
        if self.path.is_symlink():
            raise RiderError("samples_root_invalid", 400)
        with self.db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise RiderError("samples_schema_unsupported", 409)
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                  id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                  device TEXT NOT NULL, started REAL NOT NULL, ended REAL,
                  state TEXT NOT NULL, policy TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                  id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                  session_id TEXT NOT NULL, admitted REAL NOT NULL,
                  state TEXT NOT NULL, error TEXT, sample_id TEXT, saved_at REAL);
                CREATE TABLE IF NOT EXISTS samples (
                  id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                  request_id TEXT UNIQUE NOT NULL, sha TEXT NOT NULL,
                  mime TEXT NOT NULL, bytes INTEGER NOT NULL,
                  metadata TEXT NOT NULL, ordinal INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS labels (
                  sample_id TEXT NOT NULL, revision INTEGER NOT NULL,
                  body TEXT NOT NULL, PRIMARY KEY(sample_id, revision));
                CREATE INDEX IF NOT EXISTS samples_session
                ON samples(session_id, ordinal);
                CREATE INDEX IF NOT EXISTS requests_session
                ON requests(session_id, state);
                PRAGMA user_version=1;
            """)
        # A previous crash may have left an uncommitted blob. Count it against
        # quota; never silently remove a source to create more free space.
        self.total_bytes = sum(p.stat().st_size for p in self.blobs.glob("*.bin"))
        if recover:
            self.recover_interrupted()

    def refresh_usage(self):
        self.total_bytes = sum(p.stat().st_size for p in self.blobs.glob("*.bin"))

    def recover_interrupted(self):
        """The collection service calls this while holding its process lease."""
        self.refresh_usage()
        with self.db() as db:
            # Interrupted work never becomes saved on a restart.
            db.execute(
                "UPDATE requests SET state='failed', error='samples_interrupted' "
                "WHERE state IN ('queued','running')"
            )
            db.execute(
                "UPDATE sessions SET state='interrupted', ended=? "
                "WHERE state='collecting'",
                (self.wall(),),
            )

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=0.2)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def usage(self):
        metadata = sum(
            path.stat().st_size
            for path in (
                self.path,
                Path(str(self.path) + "-wal"),
                Path(str(self.path) + "-shm"),
            )
            if path.exists()
        )
        if (self.root / "exports").is_symlink():
            raise RiderError("samples_root_invalid", 400)
        exports = sum(
            path.stat().st_size
            for path in (self.root / "exports").glob("*.zip")
            if not path.is_symlink()
        )
        derived = self.root / "analysis"
        if derived.is_symlink():
            raise RiderError("samples_root_invalid", 400)
        artifact_bytes = 0
        seen = set()
        if derived.is_dir():
            for path in derived.rglob("*"):
                if path.is_symlink():
                    raise RiderError("samples_root_invalid", 400)
                try:
                    if path.is_file():
                        info = path.stat()
                        inode = (info.st_dev, info.st_ino)
                        if inode not in seen:
                            artifact_bytes += info.st_size
                            seen.add(inode)
                except FileNotFoundError:
                    # Atomic writers may unlink their private temporary name.
                    continue
        return self.total_bytes + metadata + exports + artifact_bytes

    def _space(self, extra=0):
        if self.usage() + extra > self.limits.total_bytes:
            raise RiderError("samples_quota_reached", 507)
        if shutil.disk_usage(self.root).free < self.limits.min_free_bytes + extra:
            raise RiderError("samples_disk_full", 507)

    def start(self, request_id, device_id, policy=None):
        identifier(request_id)
        identifier(device_id)
        policy_json = encode(policy or {"kind": "manual"})
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM sessions WHERE request_id=?", (request_id,)
            ).fetchone()
            if old:
                if old["device"] != device_id or old["policy"] != policy_json:
                    raise RiderError("samples_request_conflict", 409)
                return self._session(db, old["id"])
            self._space()
            if (
                db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
                >= self.limits.sessions
            ):
                raise RiderError("samples_quota_reached", 507)
            session_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
                (
                    session_id,
                    request_id,
                    device_id,
                    self.wall(),
                    None,
                    "collecting",
                    policy_json,
                ),
            )
            return self._session(db, session_id)

    def _session(self, db, session_id):
        row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise RiderError("samples_session_not_found", 404)
        counts = dict(
            db.execute(
                "SELECT state,COUNT(*) FROM requests WHERE session_id=? GROUP BY state",
                (session_id,),
            ).fetchall()
        )
        return {
            "schema_version": 1,
            "session_id": row["id"],
            "source_device_id": row["device"],
            "state": row["state"],
            "started_at": row["started"],
            "ended_at": row["ended"],
            "policy": json.loads(row["policy"]),
            "counts": counts,
        }

    def session(self, session_id):
        with self.db() as db:
            return self._session(db, identifier(session_id))

    def sessions(self):
        with self.db() as db:
            ids = db.execute(
                "SELECT id FROM sessions ORDER BY started DESC LIMIT ?",
                (self.limits.sessions,),
            ).fetchall()
            return [self._session(db, row[0]) for row in ids]

    def end(self, session_id, *, incomplete=False):
        with self.db() as db:
            session = self._session(db, identifier(session_id))
            incomplete = incomplete or bool(session["counts"].get("failed"))
            db.execute(
                "UPDATE sessions SET state=?, ended=? "
                "WHERE id=? AND state='collecting'",
                ("interrupted" if incomplete else "closed", self.wall(), session_id),
            )
            return self._session(db, session_id)

    def request(self, request_id):
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM requests WHERE id=?", (identifier(request_id),)
            ).fetchone()
            if row is None:
                raise RiderError("samples_request_not_found", 404)
            return dict(row)

    @staticmethod
    def quality(frame):
        try:
            with Image.open(io.BytesIO(frame.payload)) as image:
                if (
                    image.format not in {"JPEG", "PNG"}
                    or {"JPEG": "image/jpeg", "PNG": "image/png"}[image.format]
                    != frame.mime
                    or image.width * image.height > 1280 * 720
                    or min(image.size) < 1
                ):
                    raise RiderError("samples_image_invalid", 415)
                image.load()
                grey = image.convert("L").resize((64, 64))
                stats = ImageStat.Stat(grey)
                return {
                    "width": image.width,
                    "height": image.height,
                    "mean_luma": round(stats.mean[0], 2),
                    "contrast_stddev": round(stats.stddev[0], 2),
                    "flags": (["dim"] if stats.mean[0] < 40 else [])
                    + (["low_contrast"] if stats.stddev[0] < 10 else []),
                    "blur": "unmeasured",
                }
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise RiderError("samples_image_invalid", 415) from exc

    def _blob(self, frame):
        path = self.blobs / (frame.sha256 + ".bin")
        if path.is_symlink():
            raise RiderError("samples_source_corrupt", 409)
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != frame.sha256:
                raise RiderError("samples_source_corrupt", 409)
            return
        self._space(len(frame.payload))
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.blobs, suffix=".tmp", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(frame.payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self.total_bytes += len(frame.payload)
            fd = os.open(self.blobs, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def capture(
        self,
        session_id,
        request_id,
        frame_key,
        frame: CapturedFrame | None,
        *,
        admitted_at=None,
        allow_historical=False,
        receipt_age_ms=0.0,
    ):
        """Called by the bounded writer, never directly on an API event loop."""
        identifier(session_id)
        identifier(request_id)
        identifier(frame_key)
        fingerprint = hashlib.sha256(
            encode([session_id, frame_key]).encode()
        ).hexdigest()
        admitted = self.wall() if admitted_at is None else admitted_at
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise RiderError("samples_request_conflict", 409)
                return dict(old)
            session = self._session(db, session_id)
            count = db.execute(
                "SELECT COUNT(*) FROM requests WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            if count >= self.limits.session_requests:
                raise RiderError("samples_quota_reached", 507)
            db.execute(
                "INSERT INTO requests VALUES (?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    fingerprint,
                    session_id,
                    admitted,
                    "running",
                    None,
                    None,
                    None,
                ),
            )
        try:
            if frame is None or frame.key != frame_key:
                raise RiderError("samples_frame_expired", 409)
            if frame.device_id != session["source_device_id"]:
                raise RiderError("samples_source_mismatch", 409)
            if session["state"] != "collecting" and not (
                session["state"] == "closed" and admitted <= session["ended_at"]
            ):
                raise RiderError("samples_session_closed", 409)
            if admitted - session["started_at"] > self.limits.session_seconds:
                raise RiderError("samples_session_expired", 409)
            context = frame.context()
            source_age = context.get("source_age_ms")
            age_lower_bound = receipt_age_ms + (
                source_age
                if isinstance(source_age, (int, float)) and source_age > 0
                else 0
            )
            historical = (
                context.get("stale") is True
                or age_lower_bound > 1000
                or bool(context.get("recorded_at_header"))
            )
            if historical and not allow_historical:
                raise RiderError("samples_historical_consent_required", 409)
            with self.db() as db:
                count, size = db.execute(
                    "SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM samples "
                    "WHERE session_id=?",
                    (session_id,),
                ).fetchone()
            if (
                count >= self.limits.session_samples
                or size + len(frame.payload) > self.limits.session_bytes
            ):
                raise RiderError("samples_quota_reached", 507)
            self._space(MAX_CONTEXT_BYTES * 2)
            quality = self.quality(frame)
            sample_id = uuid.uuid4().hex
            manifest = {
                "schema_version": 1,
                "sample_id": sample_id,
                "capture_request_id": request_id,
                "session_id": session_id,
                "source_device_id": frame.device_id,
                "frame_key": frame.key,
                "source_session": context.get("source_session"),
                "run_id": context.get("run_id"),
                "frame_id": context.get("frame_id"),
                "sequence": context.get("sequence"),
                "ordinal": count,
                "raw": {
                    "sha256": frame.sha256,
                    "bytes": len(frame.payload),
                    "format": frame.mime,
                },
                "source_timestamp": context.get("source_timestamp"),
                "timestamp_source": context.get("timestamp_source"),
                "clock_domain": context.get("clock_domain"),
                "clock_sync_uncertainty_ms": context.get("clock_sync_uncertainty_ms"),
                "received_at": frame.received_at,
                "admitted_at": admitted,
                "receipt_age_ms": receipt_age_ms,
                "age_lower_bound_ms": age_lower_bound,
                "historical": historical,
                "transform": context.get("transform"),
                "calibration": context.get("calibration"),
                "telemetry": context.get("telemetry"),
                "commands": context.get("commands"),
                "association": context.get("association", "unmeasured"),
                "prediction": context.get("prediction"),
                "quality": quality,
                "source_context": context,
            }
            metadata = encode(manifest)
            self._blob(frame)
            saved = self.wall()
            # The SQLite transaction is the immutable manifest commit. A source
            # blob is fsynced first; saved is never visible before both exist.
            with self.db() as db:
                db.execute(
                    "INSERT INTO samples VALUES (?,?,?,?,?,?,?,?)",
                    (
                        sample_id,
                        session_id,
                        request_id,
                        frame.sha256,
                        frame.mime,
                        len(frame.payload),
                        metadata,
                        count,
                    ),
                )
                db.execute(
                    "UPDATE requests SET state='saved', sample_id=?, saved_at=? "
                    "WHERE id=?",
                    (sample_id, saved, request_id),
                )
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, RiderError)
                else (
                    "samples_disk_full"
                    if isinstance(exc, OSError)
                    else "samples_write_failed"
                )
            )
            with self.db() as db:
                db.execute(
                    "UPDATE requests SET state='failed', error=? WHERE id=?",
                    (code, request_id),
                )
        return self.request(request_id)

    def samples(self, session_id):
        with self.db() as db:
            self._session(db, identifier(session_id))
            rows = db.execute(
                "SELECT metadata FROM samples WHERE session_id=? ORDER BY ordinal",
                (session_id,),
            ).fetchall()
            return [json.loads(row[0]) for row in rows]

    def page(self, session_id, *, offset=0, limit=24):
        if (
            type(offset) is not int
            or type(limit) is not int
            or offset < 0
            or not 1 <= limit <= 50
        ):
            raise RiderError("samples_page_invalid", 400)
        with self.db() as db:
            session = self._session(db, identifier(session_id))
            rows = db.execute(
                "SELECT metadata FROM samples WHERE session_id=? "
                "ORDER BY ordinal LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            ).fetchall()
            count = db.execute(
                "SELECT COUNT(*) FROM samples WHERE session_id=?", (session_id,)
            ).fetchone()[0]
        items = [
            {
                key: value
                for key, value in json.loads(row[0]).items()
                if key
                in {
                    "sample_id",
                    "frame_id",
                    "sequence",
                    "ordinal",
                    "raw",
                    "quality",
                    "historical",
                    "received_at",
                }
            }
            for row in rows
        ]
        return {
            "session": session,
            "samples": items,
            "total": count,
            "offset": offset,
            "limit": limit,
        }

    def sample(self, sample_id):
        with self.db() as db:
            row = db.execute(
                "SELECT metadata FROM samples WHERE id=?", (identifier(sample_id),)
            ).fetchone()
            if row is None:
                raise RiderError("samples_sample_not_found", 404)
            result = json.loads(row[0])
            labels = db.execute(
                "SELECT body FROM labels WHERE sample_id=? ORDER BY revision",
                (sample_id,),
            ).fetchall()
            return {**result, "labels": [json.loads(row[0]) for row in labels]}

    def original(self, sample_id):
        metadata = self.sample(sample_id)
        path = self.blobs / (metadata["raw"]["sha256"] + ".bin")
        if path.is_symlink() or not path.is_file():
            raise RiderError("samples_source_corrupt", 409)
        payload = path.read_bytes()
        if (
            len(payload) != metadata["raw"]["bytes"]
            or hashlib.sha256(payload).hexdigest() != metadata["raw"]["sha256"]
        ):
            raise RiderError("samples_source_corrupt", 409)
        return payload, metadata["raw"]["format"]

    def label(
        self,
        sample_id,
        values,
        *,
        expected_revision,
        author_kind="operator",
        origin=None,
    ):
        self.sample(sample_id)
        self._space(8192)
        from pydantic import ValidationError

        from .samples_contract import LabelValues

        try:
            body = LabelValues.model_validate(values).model_dump()
        except ValidationError as exc:
            raise RiderError("samples_label_invalid", 400) from exc
        if author_kind not in {"operator", "auto"}:
            raise RiderError("samples_label_invalid", 400)
        if author_kind == "auto":
            body["review"] = "pending"
        body["author"] = {"kind": author_kind, "id": None}
        body["created_at"] = self.wall()
        if origin is not None:
            body["import_origin"] = origin
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute(
                "SELECT COALESCE(MAX(revision),0) FROM labels WHERE sample_id=?",
                (sample_id,),
            ).fetchone()[0]
            if revision >= 32:
                raise RiderError("samples_label_limit", 409)
            if revision != expected_revision:
                raise RiderError("samples_label_conflict", 409)
            body["revision"] = revision + 1
            db.execute(
                "INSERT INTO labels VALUES (?,?,?)",
                (sample_id, revision + 1, encode(body)),
            )
        return body

    def status(self):
        with self.db() as db:
            return {
                "schema_version": 1,
                "bytes": self.usage(),
                "source_bytes": self.total_bytes,
                "max_bytes": self.limits.total_bytes,
                "free_bytes": shutil.disk_usage(self.root).free,
                "min_free_bytes": self.limits.min_free_bytes,
                "sessions": db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                "samples": db.execute("SELECT COUNT(*) FROM samples").fetchone()[0],
                "failed": db.execute(
                    "SELECT COUNT(*) FROM requests WHERE state='failed'"
                ).fetchone()[0],
            }
