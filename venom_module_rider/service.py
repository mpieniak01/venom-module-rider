"""Shared manual domain and demand-driven, deduplicated Pi observations."""

from __future__ import annotations

import asyncio
import copy
import math
import time
import uuid
from contextlib import suppress
from typing import Any

from .client import PiClient
from .config import Device
from .errors import RiderError

POLLING = {
    "overview_ms": 2000,
    "control_ms": 500,
    "calibration_ms": 1000,
    "resources_ms": 5000,
    "face_ms": 5000,
    "compute_ms": 5000,
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
    "overview": ("control", "settings", "telemetry", "health", "system"),
    "control": ("control", "settings", "telemetry", "health", "history"),
    "calibration": ("control", "settings", "history"),
    "resources": ("control", "settings", "health", "catalog", "logic"),
    "face": ("control", "settings", "health"),
    "compute": ("control", "settings", "health"),
}
RESOURCE_NAMES = ("camera", "lcd", "led", "mic", "speaker", "uart")


class RiderService:
    def __init__(self, devices: tuple[Device, ...], *, client_factory=PiClient):
        self.devices = {device.id: device for device in devices}
        self.device: Device | None = None
        self.client: PiClient | None = None
        self.session = uuid.uuid4().hex
        self.factory = client_factory
        self.cache: dict[str, tuple[float, dict]] = {}
        self.inflight: dict[str, asyncio.Task] = {}
        self.commands: set[asyncio.Task] = set()
        self.stops: set[asyncio.Task] = set()
        self.operations: set[asyncio.Task] = set()
        self.last_seen: float | None = None
        self.closed = False
        self.epoch = 0
        self.sequences: dict[str, int] = {}
        self._selection_lock = asyncio.Lock()
        self.conversation_invalidator = None
        self.conversation_drainer = None

    def config(self):
        return {
            "devices": [device.public() for device in self.devices.values()],
            "device_id": self.device.id if self.device else None,
            "session": self.session,
        }

    def check_session(self, session):
        if session != self.session:
            raise RiderError("stale_device_session")

    async def select(self, device_id, session):
        self.check_session(session)
        if device_id is not None and device_id not in self.devices:
            raise RiderError("unknown_device", 404)
        async with self._selection_lock:
            self.check_session(session)
            if (self.device.id if self.device else None) == device_id:
                return self.config()
            # No new command can use this epoch. Pi's hold watchdog remains
            # authoritative; selection never auto-arms the new target.
            self.epoch += 1
            if self.conversation_invalidator:
                self.conversation_invalidator()
            if self.conversation_drainer:
                await self.conversation_drainer()
            await self._close_transport()
            self.device = self.devices.get(device_id)
            self.client = self.factory(self.device) if self.device else None
            self.session = uuid.uuid4().hex
            self.cache.clear()
            self.sequences.clear()
            self.last_seen = None
            return self.config()

    def transport(self):
        if self.closed:
            raise RiderError("module_closed", 503)
        if self.client is None:
            raise RiderError("device_not_selected", 503)
        return self.client

    async def read(self, path: str, ttl: float, *, force=False) -> dict:
        now = time.monotonic()
        cached = self.cache.get(path)
        if cached and not force and now - cached[0] < ttl:
            return copy.deepcopy(cached[1])
        session = self.session
        epoch = self.epoch
        task = self.inflight.get(path)
        if task is None:
            client = self.transport()
            task = asyncio.create_task(client.json(path))
            self.inflight[path] = task
        try:
            payload = await asyncio.shield(task)
            self.check_session(session)
            if epoch != self.epoch:
                raise RiderError("stale_device_session")
            self.cache[path] = (time.monotonic(), payload)
            self.last_seen = time.time()
            return copy.deepcopy(payload)
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
            "polling": dict(POLLING),
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
        calls = [
            self.read(READS[key][0], READS[key][1], force=key == "control")
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
        if snapshot["errors"]:
            connection["error"] = next(iter(snapshot["errors"].values()))
        return snapshot

    async def validate_intent(
        self, body, session, *, source="manual", require_ready=True
    ):
        control = await self.read("/api/control/state", 0.8)
        self.check_session(session)
        safety = control["data"].get("safety") or {}
        generation = safety.get("generation")
        if not generation or body.get("generation") != generation:
            raise RiderError("stale_generation")
        if (
            safety.get("stale") is True
            or require_ready
            and safety.get("ready") is not True
        ):
            raise RiderError("motion_not_ready")
        cache_time = self.cache["/api/control/state"][0]
        server_now = (
            control["meta"].get("server_time", 0) + time.monotonic() - cache_time
        )
        ts = body.get("ts")
        ttl = body.get("ttl_ms")
        if (
            not isinstance(ts, (int, float))
            or isinstance(ts, bool)
            or not math.isfinite(ts)
            or type(ttl) is not int
            or not 1 <= ttl <= 500
            or (server_now - ts) * 1000 > ttl
            or ts > server_now + 0.1
        ):
            raise RiderError("expired_command")
        seq = body.get("seq")
        floor = max(safety.get("last_seq", -1), self.sequences.get(generation, -1))
        if type(seq) is not int or not floor < seq < 2**63:
            raise RiderError("stale_sequence")
        if body.get("source") != source or body.get("execution_context") != "panel":
            raise RiderError("manual_panel_only")
        return safety

    async def control(self, body: dict, session):
        action = body.get("action")
        stop = action in {"stop", "disarm"}
        if not stop:
            self.check_session(session)
        if self.conversation_invalidator:
            self.conversation_invalidator()
        client = self.transport()
        epoch = self.epoch
        if not stop:
            safety = await self.validate_intent(
                body, session, require_ready=action != "clear_estop"
            )
            generation = safety["generation"]
            seq = body["seq"]
            if action in {"move", "height", "balance"}:
                if (
                    safety.get("can_move") is not True
                    or safety.get("panel_motion_enabled") is not True
                    or safety.get("owner") != "manual"
                ):
                    raise RiderError("motion_not_armed")
                if body.get("settings_revision") != (
                    safety.get("manual_settings") or {}
                ).get("revision"):
                    raise RiderError("manual_settings_stale")
            self.sequences[generation] = seq
        else:
            self.epoch += 1
            epoch = self.epoch
            for task in tuple(self.commands - self.stops):
                task.cancel()
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
                stop=stop,
            )
        )
        self.commands.add(task)
        if stop:
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

    async def mutate(self, path, body, session, *, method="POST"):
        self.check_session(session)
        client = self.transport()
        task = asyncio.create_task(client.json(path, method=method, body=body))
        self.operations.add(task)
        try:
            result = await task
            self.check_session(session)
            return result
        except asyncio.CancelledError as exc:
            raise RiderError("stale_device_session") from exc
        finally:
            self.operations.discard(task)
            # A timed-out operation may have completed on Pi; never reuse its old state.
            if session == self.session:
                self.cache.clear()

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
            safety = await self.validate_intent(body, session, source=owner)
            if safety.get("owner") != owner or safety.get("can_move") is not True:
                raise RiderError("operator_motion_permission_required")
            self.sequences[safety["generation"]] = body["seq"]
        return await self.mutate("/api/logic/feature/" + name, body, session)

    async def camera(self, stream):
        if stream not in {"raw", "proc"}:
            raise RiderError("unknown_stream", 404)
        client = self.transport()
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
        return await client.image("/camera/" + stream)

    async def _close_transport(self):
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
        if self.client:
            await self.client.close()
        self.client = None

    async def close(self):
        self.epoch += 1
        if self.conversation_invalidator:
            self.conversation_invalidator()
        if self.conversation_drainer:
            await self.conversation_drainer()
        self.closed = True
        await self._close_transport()
        self.cache.clear()
        with suppress(Exception):
            self.device = None
