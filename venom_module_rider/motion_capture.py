"""Bounded command events on the existing session writer, isolated from control."""

from __future__ import annotations

import hashlib
import time
import uuid

from .errors import RiderError
from .evidence_contract import (
    MAX_EVIDENCE_SESSION_BYTES,
    MAX_EVIDENCE_SESSION_RECORDS,
    validate_evidence,
)
from .evidence_store import canonical
from .motion_contract import DirectionValues, MotionCommand, MotionIntent, MotionProfile
from .sample_evidence import number

DIRECTIONS = ("forward", "backward", "left", "right")
DIRECTION_FIELDS = ("speed", "duration", "minimum", "maximum", "step")
VALUE_FIELDS = (
    "linear_rate",
    "turn_rate",
    "linear_deceleration",
    "turn_deceleration",
    "reversal_pause",
    "ramp_mode",
)


def text(value, maximum=128):
    return value[:maximum] if isinstance(value, str) and value else None


def integer(value):
    return value if type(value) is int else None


def profile(domain, body):
    cached = domain.cache.get("/api/calibration/controls")
    settings = cached[1].get("data", {}) if cached else {}
    safety = (
        domain.cache.get("/api/control/state", (0, {}))[1]
        .get("data", {})
        .get("safety", {})
    )
    actuators = safety.get("actuators", {})
    directions = {}
    for name in DIRECTIONS:
        row = settings.get("directions", {}).get(name, {})
        if all(number(row.get(key)) is not None for key in DIRECTION_FIELDS):
            directions[name] = DirectionValues.model_validate(
                {key: float(row[key]) for key in DIRECTION_FIELDS}
            )
    request_revision = integer(body.get("settings_revision"))
    revision = integer(settings.get("revision"))
    return MotionProfile(
        request_revision=request_revision,
        motion_revision=revision,
        profile_id=text(settings.get("active_profile")),
        values={
            key: float(settings["values"][key])
            for key in VALUE_FIELDS
            if number(settings.get("values", {}).get(key)) is not None
        },
        directions=directions,
        height=number(actuators.get("height", {}).get("value")),
        balance=actuators.get("balance", {}).get("enabled")
        if type(actuators.get("balance", {}).get("enabled")) is bool
        else None,
        settings_cache_age_s=max(0.0, time.monotonic() - cached[0]) if cached else None,
        qualification="request-revision-match"
        if request_revision is not None
        and request_revision == revision
        and body.get("device_settings") is True
        else "unverified",
        timing_revision=domain.timing.revision,
        timing=dict(domain.timing.values),
    ).model_dump(mode="json")


def record(domain, kind, host, identity):
    mono, wall = time.monotonic(), time.time()
    return validate_evidence(
        {
            "schema_version": 2,
            "record_id": hashlib.sha256(canonical(identity).encode()).hexdigest(),
            "kind": kind,
            "device_id": domain.device.id,
            "source_session": domain.session,
            "received_mono": mono,
            "received_at": wall,
            "source_clock_domain": "host-monotonic",
            "host": host,
        }
    )


def admit(registry, targets, records):
    """Admission is synchronous/no IO. Already admitted terminal events survive STOP.

    The existing bounded writer and store enforce the shared byte/record quota.
    A closing collection rejects late results; its requested event remains pending.
    """
    jobs = []
    with registry.evidence.lock:
        for sid, state in targets:
            if registry.closed or sid not in registry.active or sid in registry.closing:
                state["dropped"] += len(records)
                continue
            admitted = []
            for row in records:
                key = row["record_id"]
                if key in state["seen"]:
                    continue
                size = len(canonical(row).encode())
                if (
                    len(state["seen"]) >= MAX_EVIDENCE_SESSION_RECORDS
                    or state["admitted_bytes"] + size > MAX_EVIDENCE_SESSION_BYTES
                ):
                    state["dropped"] += 1
                    continue
                state["seen"].add(key)
                state["admitted_bytes"] += size
                admitted.append(row)
            if admitted:
                jobs.append((sid, state, admitted))
    if not jobs:
        return

    registry.motion_writer.submit(jobs)


class MotionWriter:
    """One bounded queue drained by the existing serialized registry writer."""

    def __init__(self, registry):
        from collections import deque

        self.registry = registry
        self.pending: deque[tuple[str, dict, list[dict], int]] = deque()
        self.bytes = 0
        self.records = 0
        self.draining = False

    def submit(self, jobs):
        registry = self.registry
        with registry.evidence.lock:
            for sid, state, rows in jobs:
                size = sum(len(canonical(row).encode()) for row in rows)
                if self.bytes + size > 64 * 1024 or self.records + len(rows) > 64:
                    state["dropped"] += len(rows)
                    continue
                self.pending.append((sid, state, rows, size))
                self.bytes += size
                self.records += len(rows)
            if self.draining or not self.pending:
                return
            self.draining = True
            try:
                registry._submit(self.flush)
            except Exception:
                self.draining = False
                while self.pending:
                    _, state, rows, _ = self.pending.popleft()
                    state["dropped"] += len(rows)
                self.bytes = self.records = 0

    def flush(self):
        registry = self.registry
        while True:
            with registry.evidence.lock:
                if not self.pending:
                    self.draining = False
                    return
                sid, state, rows, size = self.pending.popleft()
                self.bytes -= size
                self.records -= len(rows)
            try:
                saved, size = registry.store.evidence.append(sid, rows)
                with registry.evidence.lock:
                    state["saved"] += saved
                    state["bytes"] += size
            except Exception:
                with registry.evidence.lock:
                    state["failed"] += len(rows)
            finally:
                try:
                    registry.store.evidence.status(
                        sid, registry.evidence.session_status(state)
                    )
                except Exception:
                    registry.failures += 1


def begin(domain, body, session):
    """Capture maneuver and authoritative settings for active recordings."""
    registry = domain.samples
    if (
        registry is None
        or domain.device is None
        or not getattr(registry, "active", None)
    ):
        return None
    if session != domain.session and body.get("action") not in {"stop", "disarm"}:
        return None
    with registry.evidence.lock:
        now = registry.frames.clock()
        targets = [
            (sid, state)
            for sid, state in registry.evidence.sessions.items()
            if sid in registry.active
            and sid not in registry.closing
            and not state["retired"]
            and state["device"] == domain.device.id
            and now - state["started_mono"] <= registry.store.limits.session_seconds
            and state["source_session"] in {None, domain.session}
        ]
        for _, state in targets:
            state["source_session"] = domain.session
    if not targets:
        return None
    snapshot = profile(domain, body)
    # Age is context, not profile identity; stable revision/settings deduplicate.
    identity = {
        key: value for key, value in snapshot.items() if key != "settings_cache_age_s"
    }
    profile_row = record(
        domain,
        "profile",
        snapshot,
        ["profile", domain.device.id, domain.session, identity],
    )
    direction = body.get("direction") if body.get("direction") in DIRECTIONS else None
    row = snapshot["directions"].get(direction, {}) if direction else {}
    mode = (
        body.get("mode") if body.get("mode") in {"pulse", "hold", "release"} else None
    )
    intent = MotionIntent(
        action=text(body.get("action"), 64) or "unknown",
        request_id=text(body.get("request_id")),
        panel_id=text(body.get("panel_id")),
        supersedes_operation_id=text(body.get("supersedes_operation_id")),
        generation=text(body.get("generation")),
        seq=integer(body.get("seq")),
        direction=direction,
        mode=mode,
        turn_mode="in_place" if body.get("turn_mode") == "in_place" else None,
        figure_id=text(body.get("figure_id"), 64),
        figure_version=integer(body.get("figure_version")),
        figure_run_id=text(body.get("figure_run_id")),
        intensity=number(row.get("speed"))
        if snapshot["qualification"] == "request-revision-match"
        else None,
        duration_s=number(row.get("duration"))
        if mode == "pulse" and snapshot["qualification"] == "request-revision-match"
        else None,
        settings_revision=integer(body.get("settings_revision")),
    )
    event = MotionCommand(
        operation_id=uuid.uuid4().hex,
        phase="requested",
        intent=intent,
        profile_ref=profile_row["record_id"],
        outcome="pending",
    ).model_dump(mode="json")
    requested = record(
        domain, "command", event, [domain.session, event["operation_id"], "requested"]
    )
    admit(registry, targets, [profile_row, requested])
    return registry, targets, requested


def finish(domain, ticket, result=None, error=None):
    if ticket is None:
        return
    registry, targets, requested = ticket
    # Retain original identity even when selection changes during an old request.
    host = {
        **requested["host"],
        "phase": "result",
        "outcome": "unknown",
        "reason": None,
    }
    if error is not None:
        code = error.code if isinstance(error, RiderError) else type(error).__name__
        host["reason"] = text(code)
        host["outcome"] = (
            "cancelled"
            if code in {"CancelledError", "command_superseded", "stale_device_session"}
            else "rejected"
            if isinstance(error, RiderError) and error.status < 500
            else "unknown"
        )
    elif isinstance(result, dict):
        data = result.get("data")
        data = data if isinstance(data, dict) else {}
        safety = data.get("safety")
        safety = safety if isinstance(safety, dict) else {}
        ack = safety.get("last_ack") or data.get("ack")
        ack = ack if isinstance(ack, dict) else {}
        host["ack_status"] = text(ack.get("status"), 64)
        accepted = data.get("accepted", ack.get("accepted"))
        host["outcome"] = (
            "accepted"
            if accepted is True
            else "rejected"
            if accepted is False or data.get("error")
            else "unknown"
        )
        host["reason"] = text(data.get("error") or ack.get("reason"))
    row = {
        **requested,
        "record_id": hashlib.sha256(
            canonical([requested["record_id"], "result"]).encode()
        ).hexdigest(),
        "received_mono": time.monotonic(),
        "received_at": time.time(),
        "host": host,
    }
    admit(registry, targets, [validate_evidence(row)])


def safe_begin(domain, body, session):
    try:
        return begin(domain, body, session)
    except Exception:
        report_failure(domain)
        return None


def safe_finish(domain, ticket, result=None, error=None):
    try:
        finish(domain, ticket, result, error)
    except Exception:
        report_failure(domain)


def report_failure(domain):
    """Diagnostics must never become a control-path dependency."""
    try:
        if domain.samples is not None:
            domain.samples.failures += 1
    except Exception:
        pass
