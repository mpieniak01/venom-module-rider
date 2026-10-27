"""Opt-in, passive integration inventory for newer Venom cores.

Importing or calling this hook never creates a service/transport, selects a
robot, runs status(), or changes a control/camera/model/collection lifecycle.
The return value is plain data so older cores need no new schema import.
"""

from __future__ import annotations

import math
import re
import time
from datetime import UTC, datetime
from typing import Any

from .config import Device, configured_devices
from .errors import RiderError

MAX_OBSERVATION_AGE_S = 60.0
MAX_RESOURCES = 64
SUPPORTED_CAPABILITIES = ("manual_control", "camera", "vision", "samples", "navigation")
_PRIVATE_LABEL = re.compile(
    r"[a-z][a-z0-9+.-]*://|@|(?:\d{1,3}\.){3}\d{1,3}|"
    r"(?:[0-9a-f]{0,4}:){2,}[0-9a-f]{0,4}|"
    r"bearer|token|password|secret|[\[\]\x00-\x1f]",
    re.IGNORECASE,
)


def _utc(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat()


def _label(device: Device) -> str:
    # Labels are public configuration, but pasted credentials/addresses are not
    # useful in this cross-module inventory. Never inspect private env values.
    label = device.label.strip()
    return "Rider-Pi" if _PRIVATE_LABEL.search(label) else label[:120]


def _evidence(service: Any):
    if service is None or service.closed or service.switching:
        return None
    observed = service.connection_observation
    # Direct ordinary reads also invalidate previous evidence on failure. Do not
    # issue a read, renew a cached hit, or hide a failure behind old successes.
    reads = [
        row
        for row in service.read_observations.values()
        if observed is None or row[0] > observed[0]
    ]
    if not reads:
        return observed
    mono, wall, _ = max(reads, key=lambda row: row[0])
    recent = [row for row in reads if mono - row[0] <= MAX_OBSERVATION_AGE_S]
    status = (
        "online"
        if all(row[2] for row in recent)
        else "degraded"
        if any(row[2] for row in recent)
        else "offline"
    )
    return mono, wall, status


def _data(payload: Any) -> dict:
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else {}


def _reported(service: Any, now: float) -> list[str] | None:
    reported = []
    cached = service.cache.get("/api/control/state")
    if cached and 0 <= now - cached[0] <= MAX_OBSERVATION_AGE_S:
        safety = _data(cached[1]).get("safety", {})
        capabilities = (
            safety.get("capabilities", {}) if isinstance(safety, dict) else {}
        )
        if isinstance(capabilities, dict) and isinstance(
            capabilities.get("move"), dict
        ):
            reported.append("manual_control")
    cached = service.cache.get("/api/resource/camera/pipeline")
    if cached and 0 <= now - cached[0] <= MAX_OBSERVATION_AGE_S:
        data = _data(cached[1])
        if data.get("contract_id") == "rider.camera_pipeline":
            reported.append("camera")
    return reported or None


def _resource(device: Device, service: Any, selected: bool, now: float) -> dict:
    row = {
        "id": device.id,
        "label": _label(device),
        "kind": "robot",
        "state": "unverified",
        "configuration": "configured",
        "selected": selected,
        "mode": device.mode,
        "source": "configuration",
        "reason_code": "not_observed" if selected else "device_not_selected",
        "observed_at": None,
        "supported_capabilities": list(SUPPORTED_CAPABILITIES),
        "reported_capabilities": None,
        # Pi motion admission, resource ownership and model readiness are separate
        # contracts. API contact never implies current action availability.
        "available_capabilities": None,
    }
    evidence = _evidence(service) if selected else None
    if evidence is None:
        return row
    mono, wall, status = evidence
    if (
        status not in {"online", "offline", "degraded"}
        or not math.isfinite(mono)
        or not math.isfinite(wall)
        or wall <= 0
    ):
        return row
    try:
        observed_at = _utc(wall)
    except (ValueError, OverflowError, OSError):
        return row
    row["source"] = "device_cache"
    row["observed_at"] = observed_at
    age = now - mono
    if not 0 <= age <= MAX_OBSERVATION_AGE_S:
        row.update(state="stale", reason_code="device_observation_stale")
    elif status == "offline":
        row.update(state="offline", reason_code="device_offline")
    else:
        row.update(
            state="connected",
            reason_code="demo_observation"
            if device.mode == "demo"
            else "device_online",
            reported_capabilities=_reported(service, now),
        )
    return row


async def observe_integration(app: Any) -> dict:
    """Read only the already initialized service, or validate idle configuration.

    Wrapper time is the inventory read, resource time is the cached connection
    evidence. No background worker or cleanup is needed by this function.
    """
    service = getattr(app.state, "rider", None)
    now = time.monotonic()
    result: dict[str, Any] = {
        "resources": [],
        "resources_complete": True,
        "connectivity": "unknown",
        "configuration": "unconfigured",
        "reason_code": "device_not_configured",
        "observed_at": _utc(time.time()),
    }
    try:
        devices = (
            tuple(
                device
                for device in service.devices.values()
                if device.id != getattr(service, "builtin_demo_id", None)
                or device is service.device
            )
            if service
            else configured_devices()
        )
    except RiderError:
        result.update(
            resources_complete=False,
            configuration="unknown",
            reason_code="device_configuration_invalid",
        )
        return result
    if not devices:
        return result
    selected_id = service.device.id if service and service.device else None
    resources = [
        _resource(device, service, device.id == selected_id, now)
        for device in devices[:MAX_RESOURCES]
    ]
    result.update(
        resources=resources,
        resources_complete=len(devices) <= MAX_RESOURCES,
        configuration="configured",
        reason_code="not_observed",
    )
    selected = next((row for row in resources if row["selected"]), None)
    if selected:
        result["reason_code"] = selected["reason_code"]
        result["connectivity"] = {
            "connected": "ok",
            "offline": "down",
            "stale": "unknown",
        }.get(selected["state"], "unknown")
        if (
            service
            and (evidence := _evidence(service)) is not None
            and evidence[2] == "degraded"
            and selected["state"] == "connected"
        ):
            result["connectivity"] = "degraded"
    return result
