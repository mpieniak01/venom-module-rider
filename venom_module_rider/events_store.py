"""Automatic-event metadata shares the sample DB, blobs, lease and writer."""

from dataclasses import asdict, replace

from .errors import RiderError
from .samples_store import encode


class EventsStore:
    def __init__(self, samples):
        self.samples = samples
        with samples.db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS automatic_runs (
                  id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                  session_id TEXT NOT NULL, device TEXT NOT NULL,
                  source_session TEXT NOT NULL, profile TEXT NOT NULL,
                  started REAL NOT NULL, ended REAL, state TEXT NOT NULL, reason TEXT);
                CREATE TABLE IF NOT EXISTS automatic_events (
                  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, event_id TEXT NOT NULL,
                  burst_index INTEGER NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL,
                  sample_id TEXT, error TEXT, saved_at REAL);
                CREATE INDEX IF NOT EXISTS automatic_event_run
                  ON automatic_events(run_id, event_id);
            """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS samples_session_sha "
                "ON samples(session_id,sha)"
            )

    def recover(self):
        with self.samples.db() as db:
            db.execute(
                "UPDATE automatic_runs SET state='off',reason='auto_interrupted',"
                "ended=? WHERE state='on'",
                (self.samples.wall(),),
            )
            db.execute(
                "UPDATE automatic_events SET state='failed',error='auto_interrupted' "
                "WHERE state='queued'"
            )

    def create(self, run_id, request_id, session_id, device, source_session, profile):
        import json

        with self.samples.db() as db:
            old = db.execute(
                "SELECT * FROM automatic_runs WHERE request_id=?", (request_id,)
            ).fetchone()
            if old:
                if (
                    old["session_id"],
                    old["device"],
                    old["source_session"],
                    old["profile"],
                ) != (session_id, device, source_session, encode(profile)):
                    raise RiderError("samples_request_conflict", 409)
                return {**dict(old), "profile": json.loads(old["profile"])}
            session = self.samples._session(db, session_id)
            if (
                session["state"] != "collecting"
                or session["source_device_id"] != device
            ):
                raise RiderError("auto_collection_required", 409)
            if (
                db.execute(
                    "SELECT COUNT(*) FROM automatic_runs WHERE session_id=?",
                    (session_id,),
                ).fetchone()[0]
                >= 100
            ):
                raise RiderError("auto_run_limit", 429)
            self.samples._space(32768)
            db.execute(
                "INSERT INTO automatic_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    request_id,
                    session_id,
                    device,
                    source_session,
                    encode(profile),
                    self.samples.wall(),
                    None,
                    "on",
                    None,
                ),
            )
            return {"id": run_id, "state": "on", "profile": profile}

    def finish(self, run_id, reason):
        with self.samples.db() as db:
            db.execute(
                "UPDATE automatic_runs SET state='off',reason=?,ended=? WHERE id=?",
                (reason, self.samples.wall(), run_id),
            )

    def page(self, run_id):
        import json

        with self.samples.db() as db:
            run = db.execute(
                "SELECT * FROM automatic_runs WHERE id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise RiderError("auto_run_not_found", 404)
            rows = db.execute(
                "SELECT * FROM automatic_events WHERE run_id=? "
                "ORDER BY rowid DESC LIMIT 20",
                (run_id,),
            ).fetchall()
            counts = dict(
                db.execute(
                    "SELECT state,COUNT(*) FROM automatic_events "
                    "WHERE run_id=? GROUP BY state",
                    (run_id,),
                ).fetchall()
            )
        return {
            **dict(run),
            "profile": json.loads(run["profile"]),
            "counts": counts,
            "events": [{**dict(row), "body": json.loads(row["body"])} for row in rows],
        }

    def persist(self, run_id, session_id, event, frame, age_ms, profile):
        request_id = f"auto-{run_id}-{event.event_id}-{event.burst_index}"
        body = {
            "event_id": event.event_id,
            "burst_index": event.burst_index,
            "reasons": list(event.reasons),
            "trigger": asdict(event.trigger),
            "source": asdict(event.source),
            "confidence": None,
            "policy_revision": 1,
            "profile": profile,
        }
        self.samples._space(65536)
        with self.samples.db() as db:
            duplicate = db.execute(
                "SELECT id FROM samples WHERE session_id=? AND sha=? LIMIT 1",
                (session_id, frame.sha256),
            ).fetchone()
            db.execute(
                "INSERT INTO automatic_events VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    run_id,
                    event.event_id,
                    event.burst_index,
                    "skipped" if duplicate else "queued",
                    encode(body),
                    duplicate[0] if duplicate else None,
                    None,
                    None,
                ),
            )
        if duplicate:
            return "skipped"
        state, sample_id, error, saved_at = "failed", None, None, None
        try:
            context = {**frame.context(), "automatic_capture": body}
            frame = replace(frame, context_json=encode(context).encode())
            result = self.samples.capture(
                session_id, request_id, frame.key, frame, receipt_age_ms=age_ms
            )
            sample_id, saved_at = result["sample_id"], result["saved_at"]
            if result["state"] != "saved":
                raise RiderError(result["error"] or "samples_write_failed", 507)
            self.samples.label(
                sample_id,
                {
                    "expected_state": event.source.state,
                    "class_name": "unknown",
                    "review": "pending",
                    "note": ",".join(event.reasons),
                },
                expected_revision=0,
                author_kind="auto",
            )
            state = "saved"
        except Exception as exc:
            error = exc.code if isinstance(exc, RiderError) else "auto_write_failed"
        with self.samples.db() as db:
            db.execute(
                "UPDATE automatic_events SET state=?,sample_id=?,error=?,saved_at=? "
                "WHERE id=?",
                (state, sample_id, error, saved_at, request_id),
            )
        return state
