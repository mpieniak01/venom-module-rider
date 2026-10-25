"""Additive analysis runs and immutable artifacts in the shared sample registry."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid

from .analysis_contract import AnalysisSpec
from .errors import RiderError
from .samples_archive import SampleArchive, canonical
from .samples_store import identifier

TERMINAL = {"succeeded", "failed", "cancelled"}
MAX_RUNS = 50
MAX_SNAPSHOT = 8 * 1024**2
MAX_RUN_BYTES = 256 * 1024**2
MAX_ARTIFACT_BYTES = 8 * 1024**2
SUMMARY_COLUMNS = (
    "id,request_id,session_id,state,created,started,finished,"
    "cancel_requested,host_task_id,done,total,phase,error,result"
)


def run_id(value):
    """Only server-generated IDs may select an artifact directory."""
    if not isinstance(value, str) or re.fullmatch("[a-f0-9]{32}", value) is None:
        raise RiderError("analysis_invalid_id", 400)
    return value


class AnalysisStore:
    """One SQLite registry; sources and previous analyses are never overwritten."""

    def __init__(self, samples):
        self.samples = samples
        self.root = samples.root / "analysis"
        if self.root.is_symlink():
            raise RiderError("samples_root_invalid", 400)
        self.root.mkdir(mode=0o700, exist_ok=True)
        with samples.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS analysis_runs (
                  id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                  fingerprint TEXT NOT NULL, session_id TEXT NOT NULL,
                  state TEXT NOT NULL, created REAL NOT NULL,
                  started REAL, finished REAL,
                  cancel_requested INTEGER NOT NULL DEFAULT 0, host_task_id TEXT,
                  done INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL,
                  phase TEXT NOT NULL, error TEXT, context TEXT NOT NULL,
                  result TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS analysis_artifacts (
                  run_id TEXT NOT NULL, name TEXT NOT NULL, sha TEXT NOT NULL,
                  bytes INTEGER NOT NULL, mime TEXT NOT NULL,
                  PRIMARY KEY(run_id, name));
            """)

        with samples.db() as db:
            if "host_task_id" not in {
                row[1] for row in db.execute("PRAGMA table_info(analysis_runs)")
            }:
                db.execute("ALTER TABLE analysis_runs ADD COLUMN host_task_id TEXT")

    def _row(self, db, identity, *, context=True):
        columns = "*" if context else SUMMARY_COLUMNS
        row = db.execute(
            f"SELECT {columns} FROM analysis_runs WHERE id=?", (run_id(identity),)
        ).fetchone()
        if row is None:
            raise RiderError("analysis_not_found", 404)
        return dict(row)

    @staticmethod
    def _decode(row, *, context=True):
        result = {
            key: value
            for key, value in row.items()
            if key not in {"fingerprint", "context", "result"}
        }
        result["cancel_requested"] = bool(result["cancel_requested"])
        result["result"] = json.loads(row["result"])
        if context:
            result["context"] = json.loads(row["context"])
        return result

    def get(self, identity, *, context=True):
        """Read compact state unless an immutable input snapshot is requested."""
        with self.samples.db() as db:
            row = self._row(db, identity, context=context)
        return self._decode(row, context=context)

    def by_request(self, request_id):
        identifier(request_id)
        with self.samples.db() as db:
            row = db.execute(
                "SELECT id FROM analysis_runs WHERE request_id=?", (request_id,)
            ).fetchone()
        if row is None:
            raise RiderError("analysis_not_found", 404)
        return self.get(row["id"], context=False)

    def list(self, session_id=None):
        """Return bounded summaries, without inflating all input snapshots."""
        with self.samples.db() as db:
            rows = db.execute(
                f"SELECT {SUMMARY_COLUMNS} FROM analysis_runs "
                "WHERE (? IS NULL OR session_id=?) "
                "ORDER BY created DESC LIMIT ?",
                (session_id, session_id, MAX_RUNS),
            ).fetchall()
        return [self._decode(dict(row), context=False) for row in rows]

    def create(self, request_id, session_id, spec, profile, code):
        """Freeze a single dataset/label revision and exact selected input window."""
        identifier(request_id)
        identifier(session_id)
        spec = AnalysisSpec.model_validate(spec).model_dump(mode="json")
        if spec["input_source"] == "samples":
            # Keep fingerprints of stored pre-video sample requests unchanged.
            spec.pop("input_source")
        fingerprint = hashlib.sha256(canonical([session_id, spec])).hexdigest()
        with self.samples.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM analysis_runs WHERE request_id=?", (request_id,)
            ).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise RiderError("analysis_request_conflict", 409)
                return self._decode(dict(old))
        video = spec.get("input_source") == "video"
        if video:
            from .analysis_inputs import freeze_video

            session, source, inputs = freeze_video(self.samples, session_id)
            total = selected_count = len(inputs)
            dataset_revision = input_digest = source["sha256"]
            selected = None
        else:
            snapshot, dataset_revision = SampleArchive(self.samples).snapshot(
                session_id
            )
            session = snapshot["session"]
            total = len(snapshot["samples"])
            selected = snapshot["samples"][
                spec["offset"] : spec["offset"] + spec["count"]
            ]
            selected_count = len(selected)
            if not selected:
                raise RiderError("analysis_empty_window", 409)
            input_digest = hashlib.sha256(canonical(selected)).hexdigest()
        context = {
            "schema_version": 1,
            "kind": "rider-offline-analysis",
            "session_id": session_id,
            "dataset_revision": dataset_revision,
            "source_sample_count": total,
            "spec": spec,
            "profile": profile,
            "code": code,
            "input_digest": input_digest,
            "session": session,
            "drive_supported": False,
            "distance_measurement": False,
        }
        if video:
            context.update(input_source=source, input_kind="video")
        else:
            context["inputs"] = selected
        encoded = canonical(context)
        if len(encoded) > MAX_SNAPSHOT:
            raise RiderError("analysis_snapshot_size", 413)
        identity = uuid.uuid4().hex
        with self.samples.db() as db:
            db.execute("BEGIN IMMEDIATE")
            # Another HTTP request may have committed the same intent while the
            # snapshot was read. Resolve that result, never replace its inputs.
            old = db.execute(
                "SELECT * FROM analysis_runs WHERE request_id=?", (request_id,)
            ).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise RiderError("analysis_request_conflict", 409)
                return self._decode(dict(old))
            if (
                db.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0]
                >= MAX_RUNS
            ):
                raise RiderError("analysis_run_limit", 429)
            self.samples._space(len(encoded) + 8192)
            db.execute(
                "INSERT INTO analysis_runs "
                "(id,request_id,fingerprint,session_id,state,created,"
                "total,phase,context) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    request_id,
                    fingerprint,
                    session_id,
                    "queued",
                    self.samples.wall(),
                    selected_count,
                    "queued",
                    encoded.decode(),
                ),
            )
        return self.get(identity)

    def bind_task(self, identity, task_id):
        with self.samples.db() as db:
            db.execute(
                "UPDATE analysis_runs SET host_task_id=? WHERE id=?",
                (task_id, run_id(identity)),
            )

    def running(self, identity):
        """Only one transition may claim an un-cancelled queued run."""
        with self.samples.db() as db:
            self._row(db, identity, context=False)
            changed = db.execute(
                "UPDATE analysis_runs SET state='running',started=?,phase='loading' "
                "WHERE id=? AND state='queued' AND cancel_requested=0",
                (self.samples.wall(), identity),
            ).rowcount
        return bool(changed)

    def progress(self, identity, done, phase):
        """Persist monotonic progress without rewriting the immutable snapshot."""
        if type(done) is not int or phase not in {
            "loading",
            "waiting_live",
            "waiting_capacity",
            "decoding",
            "inference",
            "geometry",
            "saving",
        }:
            raise RiderError("analysis_progress_invalid", 400)
        with self.samples.db() as db:
            row = self._row(db, identity, context=False)
            writable = row["state"] == "running" or (
                row["state"] == "queued"
                and done == 0
                and phase in {"waiting_live", "waiting_capacity"}
            )
            if not writable or not row["done"] <= done <= row["total"]:
                raise RiderError("analysis_progress_invalid", 409)
            db.execute(
                "UPDATE analysis_runs SET done=?,phase=? WHERE id=?",
                (done, phase, identity),
            )

    def cancel(self, identity):
        """Request cancellation; running is retained until execution really stops."""
        with self.samples.db() as db:
            self._row(db, identity, context=False)
            db.execute(
                "UPDATE analysis_runs SET cancel_requested=1 "
                "WHERE id=? AND state IN ('queued','running')",
                (identity,),
            )
        return self.get(identity)

    def directory(self, identity):
        """Resolve only an owned run directory; reject local path redirection."""
        identity = run_id(identity)
        path = self.root / identity
        if self.root.is_symlink() or path.is_symlink():
            raise RiderError("analysis_artifact_path", 400)
        path.mkdir(mode=0o700, exist_ok=True)
        return path

    @staticmethod
    def filename(name):
        """Artifacts are flat server names, never caller-selected paths."""
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}", name) is None
            or ".." in name
        ):
            raise RiderError("analysis_artifact_path", 400)
        return name

    def write(self, identity, name, payload, mime):
        """Commit immutable derived bytes before publishing artifact metadata."""
        name = self.filename(name)
        if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_ARTIFACT_BYTES:
            raise RiderError("analysis_artifact_size", 413)
        if mime not in {
            "image/png",
            "image/jpeg",
            "application/json",
            "application/octet-stream",
        }:
            raise RiderError("analysis_artifact_format", 415)
        with self.samples.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, identity, context=False)
            if row["state"] != "running" or row["cancel_requested"]:
                raise RiderError("analysis_not_writable", 409)
            directory = self.directory(identity)
            used = sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
            if used + len(payload) > MAX_RUN_BYTES:
                raise RiderError("analysis_artifact_quota", 507)
            self.samples._space(len(payload) + 8192)
            target = directory / name
            if target.exists() or target.is_symlink():
                raise RiderError("analysis_artifact_exists", 409)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
                    temporary = stream.name
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temporary, target)  # atomic no-overwrite publication
                descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                digest = hashlib.sha256(payload).hexdigest()
                db.execute(
                    "INSERT INTO analysis_artifacts VALUES (?,?,?,?,?)",
                    (identity, name, digest, len(payload), mime),
                )
                return {
                    "name": name,
                    "sha256": digest,
                    "bytes": len(payload),
                    "mime": mime,
                }
            finally:
                if temporary is not None:
                    os.unlink(temporary)

    def artifacts(self, identity):
        """List committed artifacts; an orphan never appears as completed output."""
        with self.samples.db() as db:
            self._row(db, identity, context=False)
            return [
                dict(row)
                for row in db.execute(
                    "SELECT name,sha AS sha256,bytes,mime FROM analysis_artifacts "
                    "WHERE run_id=? ORDER BY name",
                    (identity,),
                ).fetchall()
            ]

    def artifact(self, identity, name):
        """Verify size and hash whenever serving a derived artifact."""
        name = self.filename(name)
        with self.samples.db() as db:
            self._row(db, identity)
            row = db.execute(
                "SELECT * FROM analysis_artifacts WHERE run_id=? AND name=?",
                (identity, name),
            ).fetchone()
            if row is None:
                raise RiderError("analysis_artifact_not_found", 404)
        try:
            path = SampleArchive.safe_file(self.directory(identity), name)
            payload = path.read_bytes()
        except (RiderError, OSError) as exc:
            raise RiderError("analysis_artifact_corrupt", 409) from exc
        if (
            len(payload) != row["bytes"]
            or hashlib.sha256(payload).hexdigest() != row["sha"]
        ):
            raise RiderError("analysis_artifact_corrupt", 409)
        return payload, row["mime"]

    def finish(self, identity, state, *, result=None, error=None):
        """Success requires the complete matching committed report, not an ACK."""
        if state not in TERMINAL:
            raise RiderError("analysis_state_invalid", 400)
        result = result or {}
        encoded = canonical(result)
        if len(encoded) > 256 * 1024:
            raise RiderError("analysis_result_size", 413)
        report = None
        if state == "succeeded":
            try:
                report = json.loads(self.artifact(identity, "report.json")[0])
            except ValueError as exc:
                raise RiderError("analysis_report_incomplete", 409) from exc
        with self.samples.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, identity)
            if row["state"] in TERMINAL:
                return self._decode(row)
            if state == "succeeded" and (
                row["state"] != "running"
                or row["cancel_requested"]
                or row["done"] != row["total"]
                or not isinstance(report, dict)
                or report.get("run_id") != identity
                or report.get("input_digest")
                != json.loads(row["context"])["input_digest"]
                or not isinstance(report.get("summary"), dict)
                or report["summary"].get("frames") != row["total"]
            ):
                raise RiderError("analysis_report_incomplete", 409)
            db.execute(
                "UPDATE analysis_runs SET state=?,finished=?,phase=?,error=?,result=? "
                "WHERE id=?",
                (state, self.samples.wall(), state, error, encoded.decode(), identity),
            )
        return self.get(identity)

    def recover(self):
        """Only the execution manager holding its process lease calls recovery."""
        with self.samples.db() as db:
            db.execute(
                "UPDATE analysis_runs SET state='failed',phase='failed',"
                "error='analysis_interrupted',finished=? "
                "WHERE state IN ('queued','running')",
                (self.samples.wall(),),
            )
