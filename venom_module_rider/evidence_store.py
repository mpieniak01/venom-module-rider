"""Evidence persistence through SampleStore's existing serialized writer."""

import json

from .errors import RiderError
from .evidence_contract import (
    MAX_EVIDENCE_SESSION_BYTES,
    MAX_EVIDENCE_SESSION_RECORDS,
    validate_evidence,
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class EvidenceStore:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS evidence (
                  session_id TEXT NOT NULL, record_id TEXT NOT NULL,
                  body TEXT NOT NULL, bytes INTEGER NOT NULL,
                  PRIMARY KEY(session_id, record_id));
                CREATE TABLE IF NOT EXISTS evidence_status (
                  session_id TEXT PRIMARY KEY, body TEXT NOT NULL);
            """)

    @staticmethod
    def size(db, session_id):
        return db.execute(
            "SELECT COALESCE(SUM(bytes),0) FROM evidence WHERE session_id=?",
            (session_id,),
        ).fetchone()[0]

    def collection_context(self, session_id, context):
        """Filter live associations against the collection owning the sample.

        A ticket is immutable and can be captured in several collections. Its
        host receipt window is shared, but archived evidence ownership is not.
        Historical contexts retain their original imported provenance.
        """
        evidence = context.get("evidence")
        if context.get("input_kind") == "historical-import" or not isinstance(
            evidence, dict
        ):
            return context
        requested = evidence.get("record_ids")
        if not isinstance(requested, list):
            return context
        references = [value for value in requested if isinstance(value, str)][:8]
        with self.store.db() as db:
            owned = {
                value
                for value in references
                if db.execute(
                    "SELECT 1 FROM evidence WHERE session_id=? AND record_id=?",
                    (session_id, value),
                ).fetchone()
            }
        kept = [value for value in references if value in owned]
        missing = len(requested) - len(kept)
        if missing == 0:
            return context
        result = {
            **context,
            "evidence": {
                **evidence,
                "record_ids": kept,
                "availability": "partial" if kept else "unavailable-for-collection",
                "unavailable_count": missing,
            },
        }
        for name in ("telemetry", "commands"):
            row = result.get(name)
            if (
                isinstance(row, dict)
                and "record_id" in row
                and row["record_id"] not in owned
            ):
                result[name] = None
        return result

    def append(self, session_id, records, *, imported=False):
        checked = [validate_evidence(row) for row in records]
        saved = size = 0
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            session = self.store._session(db, session_id)
            if session["state"] != "collecting":
                raise RiderError("samples_session_closed", 409)
            if (
                not imported
                and self.store.wall() - session["started_at"]
                > self.store.limits.session_seconds
            ):
                raise RiderError("samples_session_expired", 409)
            count, used = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(bytes),0) "
                "FROM evidence WHERE session_id=?",
                (session_id,),
            ).fetchone()
            images = db.execute(
                "SELECT COALESCE(SUM(bytes),0) FROM samples WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
            for record in checked:
                if db.execute(
                    "SELECT 1 FROM evidence WHERE session_id=? AND record_id=?",
                    (session_id, record["record_id"]),
                ).fetchone():
                    continue
                body = canonical(record)
                extra = len(body.encode())
                if (
                    count >= MAX_EVIDENCE_SESSION_RECORDS
                    or used + extra > MAX_EVIDENCE_SESSION_BYTES
                    or images + used + extra > self.store.limits.session_bytes
                ):
                    raise RiderError("samples_evidence_quota_reached", 507)
                self.store._space(extra * 2)
                db.execute(
                    "INSERT INTO evidence VALUES (?,?,?,?)",
                    (session_id, record["record_id"], body, extra),
                )
                count += 1
                used += extra
                saved += 1
                size += extra
        return saved, size

    @staticmethod
    def summary(db, session_id):
        saved, size = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM evidence WHERE session_id=?",
            (session_id,),
        ).fetchone()
        row = db.execute(
            "SELECT body FROM evidence_status WHERE session_id=?", (session_id,)
        ).fetchone()
        result = json.loads(row[0]) if row else {"dropped": 0, "failed": 0}
        result.update(
            saved=saved,
            bytes=size,
            limits={
                "session_records": MAX_EVIDENCE_SESSION_RECORDS,
                "session_bytes": MAX_EVIDENCE_SESSION_BYTES,
            },
        )
        return result

    def status(self, session_id, status):
        with self.store.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO evidence_status VALUES (?,?)",
                (session_id, canonical(status)),
            )

    @staticmethod
    def snapshot(db, session_id):
        records = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT body FROM evidence WHERE session_id=? ORDER BY rowid",
                (session_id,),
            ).fetchall()
        ]
        status = db.execute(
            "SELECT body FROM evidence_status WHERE session_id=?", (session_id,)
        ).fetchone()
        if not records and status is None:
            return None
        result = json.loads(status[0]) if status else {}
        result.update(
            saved=len(records),
            bytes=sum(len(canonical(row).encode()) for row in records),
        )
        return {"schema_version": 1, "records": records, "status": result}


def validate_extension(extension):
    if (
        not isinstance(extension, dict)
        or set(extension) != {"schema_version", "records", "status"}
        or type(extension["schema_version"]) is not int
        or extension["schema_version"] != 1
        or not isinstance(extension["records"], list)
        or not isinstance(extension["status"], dict)
        or len(extension["records"]) > MAX_EVIDENCE_SESSION_RECORDS
    ):
        raise RiderError("samples_import_evidence_invalid", 400)
    try:
        for key in ("saved", "dropped", "failed", "bytes"):
            value = extension["status"].get(key, 0)
            if type(value) is not int or value < 0:
                raise ValueError("invalid evidence counter")
        records = [validate_evidence(row) for row in extension["records"]]
        if (
            len(records) > MAX_EVIDENCE_SESSION_RECORDS
            or len({row["record_id"] for row in records}) != len(records)
            or sum(len(canonical(row).encode()) for row in records)
            > MAX_EVIDENCE_SESSION_BYTES
            or len(canonical(extension["status"]).encode()) > 8192
        ):
            raise ValueError("evidence bounds")
    except (ValueError, TypeError) as exc:
        raise RiderError("samples_import_evidence_invalid", 400) from exc
    return records
