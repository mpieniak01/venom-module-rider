"""Shared manual domain and demand-driven, deduplicated Pi observations."""

from __future__ import annotations

import asyncio
import copy
import logging
import math
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from .client import PiClient
from .config import Device, persist_device, restored_device
from .errors import RiderError
from .loop_timing import LoopTiming, ReadMetrics

POLLING = {
    "overview_ms": 2000,
    "control_ms": 500,
    "calibration_ms": 1000,
    "resources_ms": 5000,
    "face_ms": 5000,
    "compute_ms": 5000,
    "vision_ms": 500,
}
READS = {
    "telemetry": ("/state", 2.0),
    "health": ("/healthz", 5.0),
    "system": ("/sysinfo", 2.0),
    "control": ("/api/control/state", 0.4),
    "settings": ("/api/calibration/controls", 2.0),
    "catalog": ("/api/logic/catalog", 30.0),
    "logic": ("/api/logic/state", 5.0),
    "history": ("/api/motion/queue", 2.0),
}
SECTIONS = {
    "navigation": ("control", "settings"),
    "vision": ("control", "settings", "health", "telemetry", "catalog", "logic"),
    "overview": ("control", "settings", "telemetry", "health", "system"),
    "control": (
        "control",
        "settings",
        "telemetry",
        "health",
        "history",
        "catalog",
        "logic",
    ),
    "calibration": ("control", "settings", "history"),
    "resources": ("control", "settings", "health", "catalog", "logic"),
    "face": ("control", "settings", "health"),
    "compute": ("control", "settings", "health", "catalog", "logic"),
}
RESOURCE_NAMES = ("camera", "lcd", "led", "mic", "speaker", "uart")


class RiderService:
    def __init__(
        self,
        devices: tuple[Device, ...],
        *,
        client_factory=PiClient,
        selection_file: Path | None = None,
    ):
        self.timing = LoopTiming(
            selection_file.with_name("loop-timing.json") if selection_file else None
        )
        self.read_metrics = ReadMetrics()
        self.devices = {device.id: device for device in devices}
        self.device: Device | None = None
        self.client: PiClient | None = None
        self.session = uuid.uuid4().hex
        self.factory = client_factory
        self.cache: dict[str, tuple[float, dict]] = {}
        self.inflight: dict[str, asyncio.Task] = {}
        self.commands: set[asyncio.Task] = set()
        self.interventions: dict[asyncio.Task, tuple[str, str]] = {}
        self.stops: set[asyncio.Task] = set()
        self.operations: set[asyncio.Task] = set()
        self.last_seen: float | None = None
        # Passive evidence from existing reads, never a new inventory poller.
        self.read_observations: dict[str, tuple[float, float, bool]] = {}
        self.connection_observation: tuple[float, float, str] | None = None
        self.closed = False
        self.epoch = 0
        self.sequences: dict[str, int] = {}
        self._selection_lock = asyncio.Lock()
        self.selection_file = selection_file
        self.switching = False
        self.selection_warning: str | None = None
        self.conversation_invalidator = None
        self.conversation_drainer = None
        from .study_sampling import StudySampler

        self.study_sampler = StudySampler(self)
        self.samples: Any = None
        self.media_hub: Any = None
        self.pipeline_unavailable_until = 0.0
        self.refresh_task: asyncio.Task | None = None
        self.transport_cleanup: set[asyncio.Task] = set()

    def config(self):
        return {
            "devices": [device.public() for device in self.devices.values()],
            "device_id": self.device.id if self.device else None,
            "session": self.session,
            "selection_warning": self.selection_warning,
        }

    def check_session(self, session):
        if self.switching:
            raise RiderError("device_switch_in_progress")
        if session != self.session:
            raise RiderError("stale_device_session")

    async def restore(self, default=None):
        selected = restored_device(self.selection_file, self.devices, default)
        await self.select(selected, self.session, persist=False)

    def stop_sample_events(self, reason):
        from .evidence_observer import retire_received

        self.study_sampler.cancel()
        retire_received(self, reason)
        if self.samples is not None:
            try:
                self.samples.automatic.invalidate(reason)
            except Exception:
                # Optional collection must never prevent hardware STOP.
                self.samples.failures += 1

    async def select(self, device_id, session, *, persist=True):
        self.check_session(session)
        if device_id is not None and device_id not in self.devices:
            raise RiderError("unknown_device", 404)
        async with self._selection_lock:
            self.check_session(session)
            if (self.device.id if self.device else None) == device_id:
                if persist:
                    persist_device(self.selection_file, device_id)
                return self.config()
            self.switching = True
            self.stop_sample_events("auto_device_changed")
            try:
                self.cancel_pending_motion()
                if self.conversation_drainer:
                    await self.conversation_drainer()
                await self._cancel_transport_tasks()
                warning = await self._stop_previous()
                if persist:
                    persist_device(self.selection_file, device_id)
                await self._close_transport()
                self.device = self.devices.get(device_id)
                self.client = self.factory(self.device) if self.device else None
                if self.client is not None and isinstance(self.client, PiClient):
                    self.client.transport_invalidated = self._transport_invalidated
                self.session = uuid.uuid4().hex
                self.cache.clear()
                self.sequences.clear()
                self.last_seen = None
                self.read_observations.clear()
                self.connection_observation = None
                self.pipeline_unavailable_until = 0.0
                self.selection_warning = warning
                return self.config()
            finally:
                self.switching = False

    async def _stop_previous(self):
        """Quiesce the previous target without activating the next one."""
        if self.client is None:
            return None
        warning = None
        client = self.client
        try:
            safety = (await client.json("/api/control/state"))["data"]["safety"]
        except RiderError:
            safety = {"state": "unknown"}
        try:
            if safety.get("state") not in {"DISARMED", "ESTOP"} or safety.get("moving"):
                await client.json(
                    "/api/safety", method="POST", body={"action": "stop"}, stop=True
                )
            logic = (await client.json("/api/logic/state"))["data"]
            features = logic.get("state", logic).get("features", {})
            rows = (
                features
                if isinstance(features, list)
                else [
                    {"name": name, **row}
                    for name, row in features.items()
                    if isinstance(row, dict)
                ]
            )
            for row in rows:
                if row.get("name") in {
                    "s14_venom_navigation",
                    "s15_venom_perception",
                } and (row.get("requested") is True or row.get("enabled") is True):
                    await client.json(
                        "/api/logic/feature/" + row["name"],
                        method="POST",
                        body={"enabled": False},
                        stop=True,
                    )
        except RiderError:
            warning = "previous_device_stop_unconfirmed"
        return warning

    def transport(self):
        if self.closed:
            raise RiderError("module_closed", 503)
        if self.client is None:
            raise RiderError("device_not_selected", 503)
        return self.client

    async def read(self, path: str, ttl: float, *, force=False) -> dict:
        now = time.monotonic()
        cached = self.cache.get(path)
        metric = next((key for key, item in READS.items() if item[0] == path), "other")
        if cached and not force and now - cached[0] < ttl:
            self.read_metrics.count(metric, "cache_hits")
            return copy.deepcopy(cached[1])
        session = self.session
        epoch = self.epoch
        task = self.inflight.get(path)
        if task is None:
            client = self.transport()
            task = asyncio.create_task(
                self.read_metrics.measure(metric, lambda: client.json(path))
            )
            self.inflight[path] = task
        else:
            self.read_metrics.count(metric, "coalesced")
        try:
            payload = await asyncio.shield(task)
            self.check_session(session)
            if epoch != self.epoch:
                raise RiderError("stale_device_session")
            self.cache[path] = (time.monotonic(), payload)
            self.last_seen = time.time()
            self.read_observations[path] = (self.cache[path][0], self.last_seen, True)
            from .evidence_observer import observe_received

            observe_received(
                self,
                path,
                payload,
                session=session,
                received_mono=self.cache[path][0],
                received_at=self.last_seen,
            )
            return copy.deepcopy(payload)
        except Exception as exc:
            if session == self.session and epoch == self.epoch:
                # An HTTP error from Pi still proves contact with the device.
                reachable = isinstance(exc, RiderError) and exc.code not in {
                    "pi_unreachable",
                    "pi_operation_timeout",
                    "pi_name_unresolved",
                    "pi_connect_failed",
                    "pi_connection_timeout",
                    "pi_identity_mismatch",
                    "pi_identity_invalid",
                    "pi_identity_unavailable",
                    "stale_transport_generation",
                }
                self.read_observations[path] = (
                    time.monotonic(),
                    time.time(),
                    reachable,
                )
            raise
        finally:
            if task.done() and self.inflight.get(path) is task:
                self.inflight.pop(path, None)

    @staticmethod
    def data(payload):
        data = copy.deepcopy(payload["data"])
        if "server_time" not in data:
            data["server_time"] = payload["meta"].get("server_time")
        return data

    async def status(self, section="overview"):
        started = time.monotonic()
        if section not in SECTIONS:
            raise RiderError("unknown_section", 400)
        snapshot: dict[str, Any] = {
            "connection": {
                "status": "unconfigured",
                "configured": self.device is not None,
                "device_id": self.device.id if self.device else None,
                "label": self.device.label if self.device else None,
                "mode": self.device.mode if self.device else None,
                "session": self.session,
                "last_seen": self.last_seen,
                "error": None,
            },
            "polling": self.timing.polling(POLLING),
            "loop_timing": {
                **self.timing.snapshot(),
                "reads": self.read_metrics.snapshot(),
                "study_sampling": self.study_sampler.snapshot(),
            },
            "errors": {},
            **dict.fromkeys(READS),
            "resources": {},
            "face": {"library": None, "runtime": None},
            "compute": None,
        }
        if self.device is None:
            return snapshot
        session = self.session
        keys = SECTIONS[section]
        from .study_sampling import collecting

        if collecting(self):
            self.study_sampler.ensure()
        calls = [
            self.read(
                READS[key][0],
                (
                    self.timing.values["telemetry_ms"] / 1000
                    if key == "telemetry"
                    else READS[key][1]
                ),
                force=key == "control"
                or section == "navigation"
                and key != "telemetry",
            )
            for key in keys
        ]
        resource_keys = RESOURCE_NAMES if section == "resources" else ()
        calls += [self.read("/api/resource/" + name, 5.0) for name in resource_keys]
        face_keys = ("library", "runtime") if section == "face" else ()
        calls += [
            self.read(
                "/api/face-studio/" + ("gestures" if name == "library" else "runtime"),
                5.0,
            )
            for name in face_keys
        ]
        results = await asyncio.gather(*calls, return_exceptions=True)
        if session != self.session:
            raise RiderError("stale_device_session")
        good = 0
        names = list(keys) + list(resource_keys) + list(face_keys)
        for name, result in zip(names, results, strict=True):
            if isinstance(result, BaseException):
                snapshot["errors"][name] = (
                    result.code if isinstance(result, RiderError) else "pi_unreachable"
                )
                continue
            good += 1
            value = self.data(result)
            if name in resource_keys:
                snapshot["resources"][name] = value
            elif name in face_keys:
                snapshot["face"][name] = value
            else:
                snapshot[name] = value
        connection = snapshot["connection"]
        connection["status"] = (
            "online" if not snapshot["errors"] else "degraded" if good else "offline"
        )
        connection["last_seen"] = self.last_seen
        describe_transport = getattr(self.transport(), "transport_status", None)
        if describe_transport is not None:
            connection["transport"] = describe_transport()
        if snapshot["errors"]:
            connection["error"] = next(iter(snapshot["errors"].values()))
        paths = [READS[key][0] for key in keys]
        paths += ["/api/resource/" + name for name in resource_keys]
        paths += [
            "/api/face-studio/" + ("gestures" if name == "library" else "runtime")
            for name in face_keys
        ]
        evidence = [
            self.read_observations[path]
            for path in paths
            if path in self.read_observations
            and self.read_observations[path][0] >= started
        ]
        if evidence:
            latest = max(evidence, key=lambda row: row[0])
            previous = self.connection_observation
            if previous is None or latest[0] > previous[0]:
                status = (
                    "online"
                    if all(row[2] for row in evidence)
                    else "degraded"
                    if any(row[2] for row in evidence)
                    else "offline"
                )
                self.connection_observation = (latest[0], latest[1], status)
        return snapshot

    async def refresh(self, session):
        """Explicit reconnect, never an actuator retry or a background poll."""
        self.check_session(session)
        if self.refresh_task is None or self.refresh_task.done():
            self.refresh_task = asyncio.create_task(self._refresh_connection(session))
        result = await asyncio.shield(self.refresh_task)
        self.check_session(session)
        return result

    async def _fresh_connection_status(self):
        # Retire old GETs before clearing their cached results. Mutations and
        # the independent STOP lane are deliberately untouched.
        reads = list(self.inflight.values())
        for task in reads:
            task.cancel()
        if reads:
            await asyncio.gather(*reads, return_exceptions=True)
        self.inflight.clear()
        self.cache.clear()
        self.pipeline_unavailable_until = 0.0
        return await self.status("navigation")

    async def _refresh_connection(self, session):
        from .connection_recovery import recover_connection

        async with self._selection_lock:
            self.check_session(session)
            client = self.client
            if (
                self.device is not None
                and self.device.transport_profile == "trusted_lan"
            ):
                assert client is not None
                await client.reconnect()
                if self.transport_cleanup:
                    await asyncio.gather(
                        *tuple(self.transport_cleanup), return_exceptions=True
                    )
                snapshot = await self._fresh_connection_status()
                recovered = snapshot["connection"]["status"] == "online"
                return {
                    "connection": snapshot["connection"],
                    "recovery": {
                        "attempted": True,
                        "state": "recovered" if recovered else "failed",
                        "transport_profile": "trusted_lan",
                        **(
                            {"error": snapshot["connection"]["error"]}
                            if not recovered
                            else {}
                        ),
                    },
                }
            snapshot = await self._fresh_connection_status()
            recovery = {"attempted": False, "state": "not_needed"}
            if snapshot["errors"].get("control") == "pi_unreachable":
                executable = self.device.reconnect_command if self.device else None
                recovery["state"] = "not_configured"
                if executable:
                    recovery = {"attempted": True, "state": "recovered"}
                    try:
                        await recover_connection(executable)
                    except RiderError as exc:
                        recovery.update(state="failed", error=exc.code)
                    self.check_session(session)
                    snapshot = await self._fresh_connection_status()
                    if snapshot["errors"].get("control"):
                        recovery.update(state="failed")
                    else:
                        recovery["state"] = "recovered"
            return {"connection": snapshot["connection"], "recovery": recovery}

    async def validate_intent(
        self, body, session, *, source="manual", require_ready=True
    ):
        self.check_session(session)
        # This gateway verifies request shape/session, never synthesizes ARM
        # from a status GET. The Pi executor owns all motion admission decisions.
        generation = body.get("generation")
        ts, ttl, seq = (body.get(key) for key in ("ts", "ttl_ms", "seq"))
        if not isinstance(generation, str) or not generation:
            raise RiderError("stale_generation")
        if (
            type(ts) not in (int, float)
            or not math.isfinite(ts)
            or type(ttl) is not int
            or not 1 <= ttl <= 500
        ):
            raise RiderError("expired_command")
        if type(seq) is not int or not 0 <= seq < 2**63:
            raise RiderError("stale_sequence")
        if body.get("source") != source or body.get("execution_context") != "panel":
            raise RiderError("manual_panel_only")
        return {"generation": generation}

    def cancel_pending_motion(self):
        """Shared STOP boundary for manual and conversation actuator requests."""
        self.epoch += 1
        if self.conversation_invalidator:
            self.conversation_invalidator()
        for task in tuple(self.commands - self.stops):
            task.cancel()

    async def control(self, body: dict, session):
        from .motion_capture import safe_begin, safe_finish

        ticket = safe_begin(self, body, session)
        try:
            result = await self._control(body, session)
        except BaseException as exc:
            safe_finish(self, ticket, error=exc)
            raise
        safe_finish(self, ticket, result=result)
        return result

    async def _control(self, body: dict, session):
        action = body.get("action")
        stop = action in {"stop", "disarm"}
        intervention = (
            action == "move"
            and body.get("intervene") is True
            and type(body.get("operator_intervention_version")) is int
            and body["operator_intervention_version"] == 1
        )
        scoped_release = intervention and body.get("mode") == "release"
        priority = action == "navigation_pause" or (
            action == "move" and body.get("intervene") is True
        )
        if not stop:
            self.check_session(session)
        if stop:
            self.stop_sample_events("auto_global_stop")
        if scoped_release:
            # A cancelled press bypasses the pending operator ACK. Only its own
            # request is retired: an old release cannot revoke a newer gesture.
            target = (body.get("supersedes_operation_id"), body.get("panel_id"))
            for task, identity in tuple(self.interventions.items()):
                if identity == target:
                    task.cancel()
        elif stop or priority:
            self.cancel_pending_motion()
        elif self.conversation_invalidator:
            self.conversation_invalidator()
        client = self.transport()
        epoch = self.epoch
        if not stop:
            safety = await self.validate_intent(
                body, session, require_ready=action != "clear_estop"
            )
            if epoch != self.epoch:
                raise RiderError("command_superseded")
            generation = safety["generation"]
            seq = body["seq"]
            self.sequences[generation] = seq
        path = (
            "/api/safety"
            if action in {"arm", "disarm", "stop", "clear_estop"}
            else "/api/control"
        )
        task = asyncio.create_task(
            client.json(
                path,
                method="POST",
                body=body,
                stop=stop or scoped_release,
                **({"priority": True} if priority and not scoped_release else {}),
            )
        )
        self.commands.add(task)
        if intervention and not scoped_release:
            self.interventions[task] = (
                body.get("request_id", ""),
                body.get("panel_id", ""),
            )
        if stop or scoped_release:
            self.stops.add(task)
        try:
            result = await task
            if not stop and epoch != self.epoch:
                raise RiderError("command_superseded")
            safety = result["data"].get("safety")
            if epoch == self.epoch and isinstance(safety, dict):
                previous = copy.deepcopy(
                    self.cache.get("/api/control/state", (0, {}))[1]
                )
                if previous:
                    previous["data"]["safety"] = safety
                    previous["meta"]["server_time"] = result["meta"].get("server_time")
                    previous["data"]["server_time"] = result["meta"].get("server_time")
                    self.cache["/api/control/state"] = (time.monotonic(), previous)
            return result
        except asyncio.CancelledError as exc:
            raise RiderError("command_superseded") from exc
        finally:
            self.commands.discard(task)
            self.stops.discard(task)
            self.interventions.pop(task, None)

    async def mutate(self, path, body, session, *, method="POST"):
        self.check_session(session)
        epoch = self.epoch
        if path == "/api/navigation/config" or (
            isinstance(body, dict)
            and (
                body.get("action") == "stop"
                and (path == "/api/resource/camera" or path.startswith("/svc/camera-"))
                or body.get("enabled") is False
                and path
                in {
                    "/api/logic/feature/s14_venom_navigation",
                    "/api/logic/feature/s15_venom_perception",
                    "/api/logic/feature/s2_camera_preview",
                }
            )
        ):
            self.stop_sample_events("auto_source_stopped")
        client = self.transport()
        task = asyncio.create_task(client.json(path, method=method, body=body))
        self.operations.add(task)
        try:
            result = await task
            self.check_session(session)
            if (
                path in {"/api/navigation/trial", "/api/navigation/journey"}
                and epoch != self.epoch
            ):
                raise RiderError("command_superseded")
            return result
        except asyncio.CancelledError as exc:
            raise RiderError("stale_device_session") from exc
        finally:
            self.operations.discard(task)
            # A timed-out operation may have completed on Pi; never reuse its old state.
            if session == self.session:
                catalog = self.cache.get("/api/logic/catalog")
                self.cache.clear()
                # Definitions remain TTL-bounded; Pi revalidates availability.
                # Re-reading all unit definitions between ARM and Start wastes
                # the original operator intent's short validity window.
                if catalog:
                    self.cache["/api/logic/catalog"] = catalog

    async def settings(self, body, session):
        result = await self.mutate("/api/calibration/controls", body, session)
        self.cache["/api/calibration/controls"] = (time.monotonic(), result)
        return result

    async def goal(self, name, body, session):
        catalog = self.data(await self.read("/api/logic/catalog", 30))
        feature = next(
            (row for row in catalog.get("features", []) if row.get("name") == name),
            None,
        )
        if feature is None:
            raise RiderError("unknown_goal", 404)
        if feature.get("can_move") is True and body.get("enabled") is True:
            owner = feature.get("control_owner") or feature.get("motion_owner")
            intent = await self.validate_intent(body, session, source=owner)
            cached = self.cache.get("/api/control/state")
            safety: dict[str, Any] = {}
            if (
                cached
                and isinstance(cached[1], dict)
                and 0 <= time.monotonic() - cached[0] < 0.4
            ):
                safety = self.data(cached[1]).get("safety") or {}
            if (
                safety.get("generation") != intent["generation"]
                or safety.get("owner") != owner
                or safety.get("can_move") is not True
                or safety.get("stale") is True
            ):
                safety = (
                    self.data(
                        await self.read("/api/control/state", 0.4, force=True)
                    ).get("safety")
                    or {}
                )
            # Never redate an intent. Pi performs final admission under its lock;
            # a just-confirmed matching ARM does not need another network trip.
            if safety.get("owner") != owner or safety.get("can_move") is not True:
                raise RiderError("operator_motion_permission_required")
            self.sequences[intent["generation"]] = body["seq"]
        started = time.time()
        try:
            return await self.mutate("/api/logic/feature/" + name, body, session)
        except RiderError as exc:
            if (
                name == "s14_venom_navigation"
                and body.get("enabled") is True
                and exc.code == "start_failed"
            ):
                # Collect the failing run before another poll or retry replaces it.
                with suppress(RiderError):
                    state = self.data(
                        await self.read("/api/navigation/status", 0, force=True)
                    )
                    if (
                        isinstance(state.get("observed_at"), (int, float))
                        and state["observed_at"] >= started
                    ):
                        diagnostic = {
                            key: state.get(key)
                            for key in (
                                "run_id",
                                "observed_at",
                                "stop_reason",
                                "motion_failure",
                                "request_roundtrip_ms",
                                "response_age_ms",
                            )
                        }
                        details = (
                            dict(exc.details) if isinstance(exc.details, dict) else {}
                        )
                        details["navigation"] = diagnostic
                        exc.details = details
                        logging.getLogger(__name__).warning(
                            "rider navigation start_failed diagnostic=%s", diagnostic
                        )
            raise

    @staticmethod
    def unsupported_pipeline():
        return {
            "supported": False,
            "error": "camera_pipeline_unsupported",
            "capabilities": {
                "analysis_locations": ["venom"],
                "local_engines": [],
                "remote_transports": ["snapshots"],
                "stream_drive_supported": False,
                "local_drive_supported": False,
            },
        }

    async def pipeline(self, *, force=False):
        if not force and time.monotonic() < self.pipeline_unavailable_until:
            return self.unsupported_pipeline()
        try:
            response = await self.read(
                "/api/resource/camera/pipeline", 0.2, force=force
            )
        except RiderError as exc:
            if exc.status != 404:
                raise
            # A missing capability is not a per-frame polling task. Explicit
            # operator refresh bypasses this short compatibility memo.
            self.pipeline_unavailable_until = time.monotonic() + 5.0
            return self.unsupported_pipeline()
        self.pipeline_unavailable_until = 0.0
        status = response["data"]
        if (
            status.get("contract_id") != "rider.camera_pipeline"
            or type(status.get("contract_version")) is not int
            or status.get("contract_version") != 1
        ):
            raise RiderError("unsupported_pi_contract", 502)
        from .age_anchor import age_anchor

        anchor = age_anchor(
            status,
            response.get("_rider_request_started_mono"),
            response.get("_rider_response_received_mono"),
        )
        result = {**status, "supported": True, "_age_anchor": anchor}
        if self.media_hub is not None:
            self.media_hub.update_anchor(result)
        return result

    async def configure_pipeline(self, body, session):
        self.check_session(session)
        if body.get("action") == "stop":
            # Retire host requests before awaiting preview cleanup. Pi performs
            # the authoritative retirement before its capture teardown.
            self.cancel_pending_motion()
        self.stop_sample_events("auto_source_stopped")
        media = getattr(self, "media_hub", None)
        if media is not None:
            await media.invalidate()
        result = await self.mutate("/api/resource/camera/pipeline", body, session)
        return {**result["data"], "supported": True}

    async def stop_camera(self, body, session):
        """Retire preview ownership, then stop capture and the unused feed on Pi."""
        self.stop_sample_events("auto_camera_stopped")
        media = getattr(self, "media_hub", None)
        if media is not None:
            await media.invalidate()
        external = False
        try:
            await self.goal("s2_camera_preview", {"enabled": False}, session)
        except RiderError as exc:
            if exc.code != "external_services_running":
                raise
            external = True
        camera = await self.mutate(
            "/api/resource/camera",
            {
                **body,
                "units": body.get("units")
                or [
                    "camera-capture@raw.service",
                    "camera-capture@edge.service",
                    "camera-capture@ssd.service",
                ],
            },
            session,
        )
        # Pi's service manager independently refuses other task owners.
        feed = await self.mutate(
            "/svc/frame-distributor.service", {"action": "stop"}, session
        )
        if external:
            await self.goal("s2_camera_preview", {"enabled": False}, session)
        return {"camera": camera, "feed": feed}

    async def camera(self, stream):
        if stream not in {"raw", "proc"}:
            raise RiderError("unknown_stream", 404)
        client = self.transport()
        pipeline = await self.pipeline()
        if (
            stream == "raw"
            and pipeline.get("supported")
            and (pipeline.get("active_profile") or pipeline.get("profile") or {}).get(
                "remote_transport"
            )
            == "mjpeg"
        ):
            from .stream_contract import source_headers

            if self.media_hub is None:
                raise RiderError("stream_unavailable", 503)
            async with self.media_hub.lease(
                self, pipeline, keepalive=True
            ) as connection:
                frame = await self.media_hub.frame(connection, timeout=0.5)
                payload = await self.media_hub.canonical_frame(connection, frame)
                if connection.closed or self.client is not client:
                    raise RiderError("stream_epoch_changed", 409)
                if frame.age_ms(self.media_hub.clock()) >= 500:
                    raise RiderError("frame_stale", 409)
                mime = "image/jpeg"
                headers = source_headers(frame, self.media_hub.clock())
        else:
            camera = self.data(await self.read("/api/resource/camera", 2.0))
            activity = camera.get("activity") or camera
            state = activity.get("state") or camera.get("state")
            enabled = activity.get("on", activity.get("enabled"))
            if enabled is False or str(state).lower() in {"off", "stopped", "disabled"}:
                raise RiderError("camera_off", 404)
            if enabled is not True and str(state).lower() not in {
                "on",
                "running",
                "active",
            }:
                raise RiderError("camera_unknown", 503)
            payload, mime, headers = await client.image("/camera/" + stream)
        headers = {
            key: value
            for key, value in headers.items()
            if key.lower() != "x-rider-capture-key"
        }
        if stream == "raw" and self.samples is not None:
            from .sample_sources import raw

            key = raw(
                self.samples,
                client.device.id,
                self.session,
                payload,
                mime,
                headers,
                mode=client.device.mode,
            )
            if key:
                headers["x-rider-capture-key"] = key
        return payload, mime, headers

    def _transport_invalidated(self, client, owner=None):
        if self.client is not client:
            return
        self.epoch += 1
        self.stop_sample_events("auto_transport_changed")
        if self.conversation_invalidator:
            self.conversation_invalidator()
        self.cache.clear()
        self.connection_observation = (time.monotonic(), time.time(), "offline")
        self.pipeline_unavailable_until = 0.0
        for task in (self.commands - self.stops) | self.operations:
            if task is not owner:
                task.cancel()
        if self.media_hub is not None:
            cleanup = asyncio.create_task(self.media_hub.invalidate())
            self.transport_cleanup.add(cleanup)
            cleanup.add_done_callback(self.transport_cleanup.discard)

    async def _cancel_transport_tasks(self):
        tasks = (
            list(self.inflight.values()) + list(self.commands) + list(self.operations)
        )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.inflight.clear()
        self.commands.clear()
        self.stops.clear()
        self.operations.clear()

    async def _close_transport(self):
        if self.transport_cleanup:
            await asyncio.gather(*tuple(self.transport_cleanup), return_exceptions=True)
        media = getattr(self, "media_hub", None)
        if media is not None:
            await media.invalidate()
        await self._cancel_transport_tasks()
        if self.client:
            await self.client.close()
        self.client = None

    async def close(self):
        from .evidence_observer import retire_received

        await self.study_sampler.close()
        retire_received(self, "module_closed")
        self.epoch += 1
        if self.conversation_invalidator:
            self.conversation_invalidator()
        if self.conversation_drainer:
            await self.conversation_drainer()
        self.closed = True
        if self.refresh_task is not None:
            self.refresh_task.cancel()
            await asyncio.gather(self.refresh_task, return_exceptions=True)
        await self._close_transport()
        self.cache.clear()
        self.read_observations.clear()
        self.connection_observation = None
        with suppress(Exception):
            self.device = None
