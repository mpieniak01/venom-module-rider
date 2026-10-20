"""Bounded copies of already received observations; no transport or models."""

import hashlib
import math
import threading
from collections import OrderedDict

from .evidence_contract import (
    EVIDENCE_ASSOCIATION,
    EVIDENCE_RAM_TTL_S,
    MAX_EVIDENCE_RAM_BYTES,
    MAX_EVIDENCE_RAM_RECORDS,
    MAX_EVIDENCE_SESSION_BYTES,
    MAX_EVIDENCE_SESSION_RECORDS,
    validate_evidence,
)
from .evidence_store import canonical
from .samples_frame import MAX_CONTEXT_BYTES

LIMITS = {
    "record_bytes": 8192,
    "ram_records": MAX_EVIDENCE_RAM_RECORDS,
    "ram_bytes": MAX_EVIDENCE_RAM_BYTES,
    "ram_ttl_s": EVIDENCE_RAM_TTL_S,
    "session_records": MAX_EVIDENCE_SESSION_RECORDS,
    "session_bytes": MAX_EVIDENCE_SESSION_BYTES,
}


def number(value):
    return (
        float(value) if type(value) in {int, float} and math.isfinite(value) else None
    )


def observations(path, payload, device, source_session, mono, wall):
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return []
    data = payload["data"]
    identity: object
    if path == "/state":
        source = data.get("devices", {}).get("xgo", {})
        if not isinstance(source, dict):
            return []
        observation = source.get("observation")
        observation = observation if isinstance(observation, dict) else {}
        quality = observation.get("sensor_quality", source.get("sensor_quality", {}))
        quality = quality if isinstance(quality, dict) else {}
        sensors = quality.get("sensors", quality)
        if not isinstance(sensors, dict):
            return []
        selected = {
            key: sensors[key]
            for key in ("accel_x", "accel_y", "accel_z", "roll", "pitch", "yaw")
            if isinstance(sensors.get(key), dict)
        }
        if not selected:
            return []
        values = {key: number(row.get("value")) for key, row in selected.items()}
        units = {
            key: str(row.get("unit", "unknown"))[:128] for key, row in selected.items()
        }
        stamps = [number(row.get("observed_at")) for row in selected.values()]
        observed = max(
            (value for value in stamps if value is not None),
            default=number(source.get("observed_at")),
        )

        def flag(name, *, every=False):
            if name in quality:
                return quality[name]
            if name in observation:
                return observation[name]
            if name in source:
                return source[name]
            flags = [row.get(name) for row in selected.values()]
            if all(type(value) is bool for value in flags):
                return all(flags) if every else any(flags)
            return "unknown"

        physical = quality.get("physical_validation")
        if physical is None:
            validations = {
                row.get("physical_validation", "unverified")
                for row in selected.values()
            }
            physical = (
                next(iter(validations)) if len(validations) == 1 else "unverified"
            )
        details = {
            "sensors": selected,
            "ready": flag("ready", every=True),
            "offline": flag("offline"),
            "simulated": True if source.get("source") == "demo" else flag("simulated"),
            "physical_validation": physical,
            "stale": flag("stale"),
            "source": source.get("source", "unknown"),
            "clock_sync_uncertainty_ms": None,
        }
        orientation = quality.get("orientation", observation.get("orientation"))
        if isinstance(orientation, dict):
            details["orientation"] = {
                key: value
                for key in ("convention", "qualification_ref", "instance_id", "unit")
                if isinstance(value := orientation.get(key), str)
                and 0 < len(value) <= 128
            }
            for key, value_type in (("revision", int), ("verified", bool)):
                if type(orientation.get(key)) is value_type and (
                    key != "revision" or 0 <= orientation[key] <= 1000000
                ):
                    details["orientation"][key] = orientation[key]
        instance = observation.get("instance_id", source.get("instance_id"))
        if not isinstance(instance, str):
            instances = {row.get("instance_id") for row in selected.values()}
            instance = next(iter(instances)) if len(instances) == 1 else None
        kind, executor = "imu", None
        identity = [values, units, stamps]
        if "orientation" in details:
            identity.append(details["orientation"])
    elif path == "/api/control/state":
        source = data.get("safety", data)
        if not isinstance(source, dict):
            return []
        observed = number(source.get("observed_at"))
        names = (
            "state",
            "owner",
            "generation",
            "last_seq",
            "last_ack",
            "execution_context",
            "arming_mode",
            "output",
            "target",
            "moving",
            "deadline",
            "lease_remaining_s",
            "actuators",
            "operator_control",
        )
        executor = {key: source[key] for key in names if key in source}
        values, units = {}, {}
        details = {
            "ready": source.get("ready", "unknown"),
            "stale": source.get("stale", "unknown"),
            "physical_validation": "unverified",
            "clock_sync_uncertainty_ms": None,
            "offline": source.get("offline", "unknown"),
            "simulated": source.get("simulated", "unknown"),
        }
        instance = source.get("instance_id")
        kind, identity = "executor", executor
    else:
        return []
    instance = instance if isinstance(instance, str) else None
    # Receipt clocks and quality ageing never define a new source observation.
    digest = hashlib.sha256(
        canonical([kind, device, source_session, instance, observed, identity]).encode()
    ).hexdigest()
    return [
        validate_evidence(
            {
                "record_id": digest,
                "kind": kind,
                "device_id": device,
                "source_session": source_session,
                "source_instance_id": instance,
                "source_observed_at": observed,
                "received_mono": mono,
                "received_at": wall,
                "values": values,
                "units": units,
                "quality": details,
                "executor": executor,
            }
        )
    ]


class SampleEvidenceCollector:
    def __init__(self, registry):
        self.registry = registry
        self.sessions = {}
        self.window = OrderedDict()
        self.bytes = 0
        self.slot = threading.BoundedSemaphore(1)
        self.lock = threading.RLock()

    def start(self, session):
        if session["policy"].get("kind") == "import":
            return
        with self.lock:
            self.sessions.setdefault(
                session["session_id"],
                {
                    "device": session["source_device_id"],
                    "purpose": session["policy"].get("scene_group"),
                    "source_session": None,
                    "retired": False,
                    "seen": set(),
                    "admitted_bytes": 0,
                    "started_mono": self.registry.frames.clock(),
                    "saved": 0,
                    "dropped": 0,
                    "failed": 0,
                    "bytes": 0,
                },
            )

    def purge(self):
        now = self.registry.frames.clock()
        while (
            self.window
            and now - next(iter(self.window.values()))["received_mono"]
            > EVIDENCE_RAM_TTL_S
        ):
            _, row = self.window.popitem(last=False)
            self.bytes -= len(canonical(row).encode())

    def retire(self, reason, session_id=None):
        with self.lock:
            for sid, state in self.sessions.items():
                if session_id is None or sid == session_id:
                    state["retired"] = True
                    state["retired_reason"] = reason
            if session_id is None or not any(
                sid in self.registry.active
                and sid not in self.registry.closing
                and not state["retired"]
                for sid, state in self.sessions.items()
            ):
                self.window.clear()
                self.bytes = 0

    def session_status(self, state):
        return {
            key: state.get(key)
            for key in (
                "saved",
                "dropped",
                "failed",
                "bytes",
                "retired",
                "retired_reason",
            )
        }

    def finish(self, session_id):
        # Executed behind admitted jobs on the existing writer.
        with self.lock:
            state = self.sessions.pop(session_id, None)
            if not self.sessions:
                self.window.clear()
                self.bytes = 0
        if state is not None:
            self.registry.store.evidence.status(session_id, self.session_status(state))

    def observe(self, path, payload, device, source_session, mono=None, wall=None):
        with self.lock:
            if (
                self.registry.closed
                or not self.sessions
                or path not in {"/state", "/api/control/state"}
            ):
                return
            mono = self.registry.frames.clock() if mono is None else mono
            wall = self.registry.store.wall() if wall is None else wall
            self.purge()
            eligible = []
            for sid, state in self.sessions.items():
                if (
                    sid not in self.registry.active
                    or sid in self.registry.closing
                    or state["retired"]
                    or state["device"] != device
                ):
                    continue
                if (
                    self.registry.frames.clock() - state["started_mono"]
                    > self.registry.store.limits.session_seconds
                ):
                    state["retired"] = True
                    state["retired_reason"] = "session_expired"
                    continue
                if state["source_session"] is None:
                    state["source_session"] = source_session
                elif state["source_session"] != source_session:
                    state["retired"] = True
                    state["retired_reason"] = "source_changed"
                    self.window.clear()
                    self.bytes = 0
                    continue
                eligible.append((sid, state))
            if not eligible:
                return
            try:
                records = observations(
                    path, payload, device, source_session, float(mono), float(wall)
                )
            except (ValueError, TypeError, AttributeError):
                for _, state in eligible:
                    state["dropped"] += 1
                return
            jobs = []
            for record in records:
                size = len(canonical(record).encode())
                targets = []
                for sid, state in eligible:
                    if record["record_id"] in state["seen"]:
                        continue
                    if (
                        len(state["seen"]) >= MAX_EVIDENCE_SESSION_RECORDS
                        or state["admitted_bytes"] + size > MAX_EVIDENCE_SESSION_BYTES
                    ):
                        state["dropped"] += 1
                        state["retired"] = True
                        state["retired_reason"] = "evidence_limit"
                        continue
                    state["seen"].add(record["record_id"])
                    state["admitted_bytes"] += size
                    targets.append((sid, state))
                if not targets:
                    continue
                jobs.append((record, targets))
            if not jobs:
                return
            if not self.slot.acquire(blocking=False):
                for _, targets in jobs:
                    for _, state in targets:
                        state["dropped"] += 1
                return

            def persist():
                try:
                    for record, targets in jobs:
                        for sid, state in targets:
                            with self.lock:
                                if state["retired"]:
                                    state["dropped"] += 1
                                    continue
                            try:
                                saved, size = self.registry.store.evidence.append(
                                    sid, [record]
                                )
                                with self.lock:
                                    state["saved"] += saved
                                    state["bytes"] += size
                                    if not state["retired"]:
                                        self._remember(record)
                            except Exception:
                                with self.lock:
                                    state["failed"] += 1
                            finally:
                                with self.lock:
                                    status = self.session_status(state)
                                try:
                                    self.registry.store.evidence.status(sid, status)
                                except Exception:
                                    with self.lock:
                                        state["failed"] += 1
                finally:
                    self.slot.release()

            try:
                self.registry._submit(persist)
            except Exception:
                self.slot.release()
                for _, targets in jobs:
                    for _, state in targets:
                        state["dropped"] += 1

    def _remember(self, record):
        """Only durable records can become image references; never renew a receipt."""
        self.purge()
        if (
            record["record_id"] in self.window
            or self.registry.frames.clock() - record["received_mono"]
            > EVIDENCE_RAM_TTL_S
        ):
            return
        size = len(canonical(record).encode())
        while self.window and (
            len(self.window) >= MAX_EVIDENCE_RAM_RECORDS
            or self.bytes + size > MAX_EVIDENCE_RAM_BYTES
        ):
            _, old = self.window.popitem(last=False)
            self.bytes -= len(canonical(old).encode())
        self.window[record["record_id"]] = record
        self.bytes += size

    def enrich(self, device, context):
        original = dict(context or {})
        if original.get("input_kind") == "historical-import":
            return original
        with self.lock:
            self.purge()
            source = original.get("host_device_session")
            candidates = [
                row
                for row in self.window.values()
                if row["device_id"] == device and row["source_session"] == source
            ]
            if not candidates:
                return original
            recent = candidates[-8:]
            evidence = {
                "record_ids": [row["record_id"] for row in recent],
                "association": EVIDENCE_ASSOCIATION,
                "clock_sync_uncertainty_ms": None,
            }
            extra = {"evidence": evidence}
            imu = next((row for row in reversed(recent) if row["kind"] == "imu"), None)
            executor = next(
                (row for row in reversed(recent) if row["kind"] == "executor"), None
            )
            if original.get("telemetry") is None and imu:
                extra["telemetry"] = {
                    "record_id": imu["record_id"],
                    "values": imu["values"],
                    "units": imu["units"],
                    "quality": imu["quality"],
                    "source_observed_at": imu["source_observed_at"],
                    "received_at": imu["received_at"],
                    "association": EVIDENCE_ASSOCIATION,
                }
            if original.get("commands") is None and executor:
                extra["commands"] = {
                    "record_id": executor["record_id"],
                    "executor": executor["executor"],
                    "quality": executor["quality"],
                    "source_observed_at": executor["source_observed_at"],
                    "received_at": executor["received_at"],
                    "association": EVIDENCE_ASSOCIATION,
                }
            combined = {**original, **extra}
            return (
                combined
                if len(canonical(combined).encode()) <= MAX_CONTEXT_BYTES
                else original
            )

    def status(self):
        with self.lock:
            self.purge()
            return {
                "limits": LIMITS,
                "ram_records": len(self.window),
                "ram_bytes": self.bytes,
                "sessions": {
                    sid: self.session_status(row) for sid, row in self.sessions.items()
                },
                **{
                    key: sum(row[key] for row in self.sessions.values())
                    for key in ("saved", "dropped", "failed", "bytes")
                },
            }
