"""Explicit, operator-configured targets; no discovery or production fallback."""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import RiderError

IDENTIFIER = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def credential_transport_secure(url: str) -> bool:
    """Bearer credentials require TLS or a locally terminated encrypted tunnel."""
    parsed = urlsplit(url)
    if parsed.scheme == "https":
        return True
    if parsed.scheme != "http":
        return False
    if parsed.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Device:
    id: str
    label: str
    url: str
    mode: str = "device"
    compute_token_env: str | None = None
    agent_token_env: str = "RIDER_AGENT_TOKEN"

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
        if not isinstance(row, dict) or set(row) - {
            "id",
            "label",
            "url",
            "mode",
            "compute_token_env",
            "agent_token_env",
        }:
            raise RiderError("invalid_device_configuration", 503)
        id_ = row.get("id")
        label = row.get("label")
        url = row.get("url")
        mode = row.get("mode", "device")
        token_env = row.get("compute_token_env")
        agent_env = row.get("agent_token_env", "RIDER_AGENT_TOKEN")
        if (
            not isinstance(id_, str)
            or not IDENTIFIER.fullmatch(id_)
            or not isinstance(label, str)
            or not 1 <= len(label) <= 128
            or not isinstance(url, str)
            or mode not in {"demo", "device"}
            or not isinstance(agent_env, str)
            or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", agent_env)
            or token_env is not None
            and (
                not isinstance(token_env, str)
                or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", token_env)
            )
            or os.getenv("RIDER_DEMO_ONLY", "").lower() in {"1", "true"}
            and mode != "demo"
        ):
            raise RiderError("invalid_device_configuration", 503)
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise RiderError("invalid_device_configuration", 503) from exc
        if (
            port is not None
            and not 1 <= port <= 65535
            or parsed.scheme not in {"http", "https"}
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
        devices.append(Device(id_, label, url.rstrip("/"), mode, token_env, agent_env))
    return tuple(devices)
