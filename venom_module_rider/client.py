"""One negotiated Pi transport, including binary media and an independent STOP lane."""

from __future__ import annotations

import hashlib
import json
import os

import httpx

from .config import Device, credential_transport_secure
from .errors import RiderError

ACCEPT = "application/vnd.rider.api+json;version=1"
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 4 * 1024 * 1024
IMAGE_HEADERS = {
    "x-rider-contract",
    "x-rider-observed-at",
    "x-rider-file-observed-at",
    "x-rider-recorded-at",
    "x-rider-source",
    "x-rider-frame-id",
    "x-rider-stream",
    "x-rider-stale",
    "x-rider-placeholder",
    "x-face-source",
}


def expected_contract(path: str):
    path = path.split("?", 1)[0]
    exact = {
        "/state": ("rider.telemetry", 1),
        "/healthz": ("rider.health", 1),
        "/sysinfo": ("rider.system", 1),
        "/api/control/state": ("rider.safety", 1),
        "/api/control/agent/state": ("rider.safety", 1),
        "/api/control/agent/pulse": ("rider.safety", 1),
        "/api/stop": ("rider.safety", 1),
        "/api/voice/playback": ("rider.audio", 1),
        "/api/voice/playback/cancel": ("rider.audio", 1),
        "/api/control": ("rider.safety", 1),
        "/api/safety": ("rider.safety", 1),
        "/api/motion/queue": ("rider.safety", 1),
        "/api/calibration/controls": ("rider.calibration", 1),
        "/api/logic/catalog": ("rider.catalog", 2),
        "/api/logic/state": ("rider.tasks", 2),
        "/api/services/graph": ("rider.service_graph", 2),
        "/api/navigation/config": ("rider.navigation_loop", 1),
        "/api/navigation/trial": ("rider.navigation_loop", 1),
        "/api/navigation/status": ("rider.navigation_loop", 1),
        "/api/compute/config": ("rider.compute", 1),
        "/api/compute/status": ("rider.compute", 1),
    }
    if path in exact:
        return exact[path]
    for prefix, contract in (
        ("/api/resource/", ("rider.resources", 1)),
        ("/api/logic/feature/", ("rider.tasks", 2)),
        ("/api/services/", ("rider.services", 2)),
        ("/api/face-studio/gestures", ("rider.face_studio", 1)),
        ("/api/face-studio/runtime", ("rider.face", 1)),
    ):
        if path.startswith(prefix):
            return contract
    raise RiderError("unsupported_pi_path", 400)


def reject_constant(_):
    raise ValueError("Non-finite JSON")


def preparation_identity(value):
    """Mirror Pi's existing start-id derivation for diagnostic correlation only."""
    if not isinstance(value, dict):
        return value
    request_id = value.get("request_id")
    if not isinstance(request_id, str) or not 0 < len(request_id) <= 64:
        return value
    start_id = (
        "trial-" + hashlib.sha256(request_id.encode()).hexdigest()[:32] + "-start"
    )
    return {**value, "operator_request_id": start_id}


def trial_error_details(payload):
    """Unwrap diagnostics retained by negotiated Pi errors, never reinterpret OK."""
    error = payload.get("error")
    data = payload.get("data")
    candidates = (
        error.get("details") if isinstance(error, dict) else None,
        payload.get("details"),
        data.get("details") if isinstance(data, dict) else None,
    )
    details = next((row for row in candidates if isinstance(row, dict)), payload)
    if isinstance(details.get("preparation"), dict):
        return {**details, "preparation": preparation_identity(details["preparation"])}
    return details


def json_timeout(path: str, method: str, stop: bool) -> httpx.Timeout:
    duration = 1.5
    path = path.split("?", 1)[0]
    if not stop:
        if path == "/api/voice/playback":
            duration = 60.0
        if method in {"POST", "DELETE"} and path.startswith(
            ("/api/logic/feature/", "/api/resource/", "/api/face-studio/runtime/")
        ):
            duration = 20.0
        elif method == "POST" and path == "/api/navigation/trial":
            duration = 6.0
        elif method == "GET" and path.startswith("/api/services/"):
            duration = 10.0
    return httpx.Timeout(duration, connect=0.5, pool=0.5)


class PiClient:
    def __init__(self, device: Device, *, transport=None):
        self.device = device
        # ASGI fixtures are in-process and never transmit credentials on a network.
        self.credential_transport_secure = credential_transport_secure(
            device.url
        ) or isinstance(transport, httpx.ASGITransport)
        options = {
            "base_url": device.url,
            "trust_env": False,
            "follow_redirects": False,
            "transport": transport,
            "timeout": httpx.Timeout(1.5, connect=0.5),
        }
        self.http = httpx.AsyncClient(
            **options,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )
        self.stop_http = httpx.AsyncClient(
            **options,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
        )
        self.closed = False

    async def json(
        self, path: str, *, method="GET", body=None, stop=False, headers=None
    ) -> dict:
        client = self.stop_http if stop else self.http
        request_headers = {"Accept": ACCEPT, **(headers or {})}
        if method != "GET" and path in {
            "/api/control/agent/pulse",
            "/api/voice/playback",
            "/api/voice/playback/cancel",
        }:
            if not self.credential_transport_secure:
                raise RiderError("agent_transport_insecure", 503)
            token = os.getenv(self.device.agent_token_env, "")
            if not token:
                raise RiderError("agent_credentials_unavailable", 503)
            request_headers["Authorization"] = "Bearer " + token
        binary = body if isinstance(body, bytes) else None
        payload = None if isinstance(body, bytes) else body
        try:
            async with client.stream(
                method,
                path,
                content=binary,
                json=payload,
                headers=request_headers,
                timeout=json_timeout(path, method, stop),
            ) as response:
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_JSON_BYTES:
                        raise RiderError("pi_response_too_large", 502)
                if response.is_redirect:
                    raise RiderError("pi_redirect_refused", 502)
                try:
                    payload = json.loads(content, parse_constant=reject_constant)
                except (ValueError, UnicodeError) as exc:
                    raise RiderError("invalid_pi_response", 502) from exc
                if not isinstance(payload, dict):
                    raise RiderError("invalid_pi_response", 502)
                if response.status_code >= 400:
                    error = payload.get("error")
                    code = error.get("code") if isinstance(error, dict) else error
                    raise RiderError(
                        str(code or "pi_request_failed")[:128],
                        response.status_code,
                        trial_error_details(payload)
                        if path == "/api/navigation/trial"
                        else payload,
                    )
                meta = payload.get("meta")
                if (
                    not isinstance(meta, dict)
                    or type(meta.get("envelope_version")) is not int
                    or meta.get("envelope_version") != 1
                ):
                    raise RiderError("unsupported_pi_contract", 502)
                if not isinstance(payload.get("data"), dict):
                    raise RiderError("invalid_pi_response", 502)
                expected = expected_contract(path)
                if (
                    type(meta.get("contract_version")) is not int
                    or (meta.get("contract_id"), meta.get("contract_version"))
                    != expected
                ):
                    raise RiderError("unsupported_pi_contract", 502)
                data = payload["data"]
                if path == "/api/calibration/controls" and (
                    data.get("contract_id") != "rider.manual_settings"
                    or data.get("contract_version") != 2
                    or not isinstance(data.get("parameter_schema"), dict)
                    or data["parameter_schema"].get("version") != 1
                    or not isinstance(data.get("control_modes"), dict)
                    or data["control_modes"].get("version") != 1
                ):
                    raise RiderError("unsupported_manual_settings", 502)
                if (
                    path == "/api/control/state"
                    and (data.get("safety") or {}).get("contract_version") != 1
                ):
                    raise RiderError("unsupported_safety_contract", 502)
                if path == "/api/navigation/status" and isinstance(
                    data.get("preparation"), dict
                ):
                    payload = {
                        **payload,
                        "data": {
                            **data,
                            "preparation": preparation_identity(data["preparation"]),
                        },
                    }
                return payload
        except httpx.TimeoutException as exc:
            if method != "GET":
                raise RiderError(
                    "pi_operation_timeout", 504, {"outcome": "unknown"}
                ) from exc
            raise RiderError("pi_unreachable", 503) from exc
        except httpx.HTTPError as exc:
            raise RiderError("pi_unreachable", 503) from exc

    async def image(self, path: str, *, body=None) -> tuple[bytes, str, dict]:
        try:
            async with self.http.stream(
                "POST" if body is not None else "GET",
                path,
                json=body,
                headers={"Accept": "image/png,image/jpeg"},
            ) as response:
                if response.status_code >= 400:
                    raise RiderError(
                        "frame_unavailable"
                        if response.status_code == 404
                        else "frame_busy"
                        if response.status_code == 429
                        else "frame_failed",
                        response.status_code,
                    )
                mime = response.headers.get("content-type", "").split(";")[0]
                if mime not in {"image/png", "image/jpeg", "image/webp"}:
                    raise RiderError("invalid_image_type", 502)
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_IMAGE_BYTES:
                        raise RiderError("image_too_large", 502)
                headers = {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower() in IMAGE_HEADERS
                }
                headers["Cache-Control"] = "no-store"
                return bytes(content), mime, headers
        except httpx.HTTPError as exc:
            raise RiderError("pi_unreachable", 503) from exc

    async def close(self):
        self.closed = True
        await self.http.aclose()
        await self.stop_http.aclose()
