"""Explicit, operator-configured targets; no discovery or production fallback."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import RiderError

IDENTIFIER = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@dataclass(frozen=True)
class Device:
    id: str
    label: str
    url: str
    mode: str = "device"

    def public(self) -> dict:
        return {"id": self.id, "label": self.label, "mode": self.mode}


def configured_devices(raw: str | None = None) -> tuple[Device, ...]:
    try:
        rows = json.loads(
            raw if raw is not None else os.getenv("RIDER_DEVICES_JSON", "[]")
        )
    except ValueError as exc:
        raise RiderError("invalid_device_configuration", 503) from exc
    if not isinstance(rows, list) or len(rows) > 16:
        raise RiderError("invalid_device_configuration", 503)
    devices: list[Device] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) - {"id", "label", "url", "mode"}:
            raise RiderError("invalid_device_configuration", 503)
        id_ = row.get("id")
        label = row.get("label")
        url = row.get("url")
        mode = row.get("mode", "device")
        if (
            not isinstance(id_, str)
            or not IDENTIFIER.fullmatch(id_)
            or not isinstance(label, str)
            or not 1 <= len(label) <= 128
            or not isinstance(url, str)
            or mode not in {"demo", "device"}
        ):
            raise RiderError("invalid_device_configuration", 503)
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise RiderError("invalid_device_configuration", 503)
        if mode == "demo" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise RiderError("demo_requires_loopback", 503)
        if id_ in {device.id for device in devices}:
            raise RiderError("duplicate_device_id", 503)
        devices.append(Device(id_, label, url.rstrip("/"), mode))
    return tuple(devices)
