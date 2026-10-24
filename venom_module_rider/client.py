"""One negotiated Pi transport, including binary media and an independent STOP lane."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections.abc import AsyncGenerator, Callable
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

import httpx

from .config import Device, credential_transport_secure
from .errors import RiderError
from .transport_cleanup import finish_native_cleanup

if TYPE_CHECKING:
    from .lan_transport import Generation
    from .stream_contract import NativeFrame

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
    "x-rider-source-json",
    "x-rider-local-engine",
    "x-rider-local-model-id",
    "x-rider-frame-age-ms",
    "x-rider-frame-age-source",
    "x-rider-camera-session",
    "x-rider-sequence",
    "x-rider-transport-epoch",
    "x-rider-pipeline-revision",
    "x-rider-captured-monotonic-ns",
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
        "/svc/frame-distributor.service": ("rider.services", 2),
        "/api/navigation/config": ("rider.navigation_loop", 1),
        "/api/navigation/image-reference": ("rider.navigation_loop", 1),
        "/api/navigation/model-selection": ("rider.navigation_loop", 1),
        "/api/navigation/local-models": ("rider.navigation_loop", 1),
        "/api/navigation/trial": ("rider.navigation_loop", 1),
        "/api/navigation/journey": ("rider.navigation_loop", 1),
        "/api/navigation/status": ("rider.navigation_loop", 1),
        "/api/compute/config": ("rider.compute", 1),
        "/api/compute/status": ("rider.compute", 1),
        "/api/resource/camera/pipeline": ("rider.camera_pipeline", 1),
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
    if not isinstance(request_id, str) or not 0 < len(request_id) <= 128:
        return value
    prefix = "journey-" if value.get("journey_version") == 1 else "trial-"
    start_id = prefix + hashlib.sha256(request_id.encode()).hexdigest()[:32] + "-start"
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
            (
                "/api/logic/feature/",
                "/api/resource/",
                "/api/face-studio/runtime/",
                "/svc/frame-distributor.service",
            )
        ):
            duration = 20.0
        elif method == "POST" and path in {
            "/api/navigation/trial",
            "/api/navigation/journey",
        }:
            # Pi bounds service preparation to 15 s, including at most 5 s
            # for the final start after ARM. Do not time out before its result.
            duration = 18.0
        elif method == "GET" and path.startswith("/api/services/"):
            duration = 10.0
    return httpx.Timeout(duration, connect=0.5, pool=0.5)


class PiClient:
    def __init__(self, device: Device, *, transport=None, resolver=None):
        self.device = device
        from .lan_transport import LanConnection

        self.lan = (
            LanConnection(device, transport=transport, resolver=resolver)
            if device.transport_profile == "trusted_lan"
            else None
        )
        self.transport_invalidated: Callable | None = None
        self._reported_lan_failure_generation: int | None = None
        self._media_generation: Generation | None = None
        self._media_frame: NativeFrame | None = None
        self._media_token: object | None = None
        self._media_valid_until = 0.0
        if self.lan is not None:
            self.lan.invalidated = self._invalidated
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
        self.media_http = httpx.AsyncClient(
            **options,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )
        # One urgent operator request leaves the second reserved STOP slot free.
        self.operator_lane = asyncio.Lock()
        self.closed = False

    @property
    def credential_transport_allowed(self):
        return bool(self.lan.current) if self.lan else self.credential_transport_secure

    def transport_status(self):
        return (
            self.lan.snapshot()
            if self.lan
            else {
                "profile": "secure",
                "state": "configured",
                "generation": 0,
                "identity_verified": False,
                "error": None,
                "retry_in_ms": 0,
            }
        )

    def _invalidated(self, owner=None):
        if self.transport_invalidated is not None:
            self.transport_invalidated(self, owner)

    async def reconnect(self):
        if self.lan is not None:
            await self.lan.reconnect()

    async def _lane(self, lane, *, urgent=False):
        if self.closed:
            raise RiderError("module_closed", 503)
        if self.lan is None:
            return {
                "http": self.http,
                "stop": self.stop_http,
                "media": self.media_http,
            }[lane], None
        generation = await self.lan.acquire(urgent=urgent)
        return getattr(generation, lane), generation

    def _check_generation(self, generation):
        if self.lan is not None:
            self.lan.check(generation)

    def note_media_health(self, frame, valid_until):
        """Accept only hub-admitted evidence from this exact live upstream."""
        self._media_valid_until = 0.0
        if (
            self.lan is not None
            and self._media_generation is self.lan.current
            and self._media_frame is not None
            and frame is not None
            and frame.source is self._media_frame.source
            and frame.received_mono == self._media_frame.received_mono
        ):
            self._media_valid_until = valid_until

    def _preserve_media_on_timeout(self, generation, error, *, passive):
        # A local endpoint timeout does not contradict a still-fresh verified
        # stream. Keep reporting the failed read; never retry or extend its age.
        return (
            passive
            and isinstance(
                error, (httpx.PoolTimeout, httpx.ConnectTimeout, httpx.ReadTimeout)
            )
            and self.lan is not None
            and generation is not None
            and self.lan.current is generation
            and self._media_generation is generation
            and time.monotonic() < self._media_valid_until
        )

    def _log_lan_json_failure(self, path, method, generation, error):
        # One primary failure per generation; pool teardown may cause several
        # secondary errors. Never log URL, query, exception text, body or headers.
        if (
            self.lan is None
            or self.lan.current is not generation
            or self._reported_lan_failure_generation == generation.number
        ):
            return
        self._reported_lan_failure_generation = generation.number
        safe_path = urlsplit(path).path
        if (
            not safe_path.startswith("/")
            or len(safe_path) > 256
            or not safe_path.isprintable()
        ):
            safe_path = "<invalid-path>"
        safe_method = (
            method if method in {"GET", "POST", "PUT", "PATCH", "DELETE"} else "OTHER"
        )
        logging.getLogger(__name__).warning(
            "Rider LAN JSON failure generation=%s method=%s path=%s exception=%s",
            generation.number,
            safe_method,
            safe_path,
            type(error).__name__,
        )

    async def _failed(self, generation, error):
        if self.lan is not None:
            await finish_native_cleanup(
                asyncio.create_task(
                    self.lan.fail(generation, error, owner=asyncio.current_task())
                )
            )

    async def json(
        self,
        path: str,
        *,
        method="GET",
        body=None,
        stop=False,
        headers=None,
        priority=False,
    ) -> dict:
        if priority:
            async with self.operator_lane:
                return await self._json(
                    path, method=method, body=body, stop=True, headers=headers
                )
        return await self._json(
            path, method=method, body=body, stop=stop, headers=headers
        )

    async def _json(
        self, path: str, *, method="GET", body=None, stop=False, headers=None
    ) -> dict:
        admitted = time.monotonic()
        client, generation = await self._lane("stop" if stop else "http", urgent=stop)
        if self.lan is not None and method != "GET":
            await self.lan.revalidate(generation, started=admitted, urgent=stop)
        request_headers = {"Accept": ACCEPT, **(headers or {})}
        if method != "GET" and path in {
            "/api/control/agent/pulse",
            "/api/voice/playback",
            "/api/voice/playback/cancel",
        }:
            if not self.credential_transport_allowed:
                raise RiderError("agent_transport_insecure", 503)
            token = os.getenv(self.device.agent_token_env, "")
            if not token:
                raise RiderError("agent_credentials_unavailable", 503)
            request_headers["Authorization"] = "Bearer " + token
        binary = body if isinstance(body, bytes) else None
        payload = None if isinstance(body, bytes) else body
        request_started_mono = time.monotonic()
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
                        if path in {"/api/navigation/trial", "/api/navigation/journey"}
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
                if (
                    path == "/api/resource/camera/pipeline"
                    and type(meta.get("contract_version")) is int
                    and (
                        meta.get("contract_id"),
                        meta.get("contract_version"),
                    )
                    == ("rider.resources", 1)
                ):
                    # Legacy resource wildcard routes can return a generic
                    # camera/resource response for an unknown subresource.
                    # This is explicit lack of pipeline support, not success.
                    raise RiderError("camera_pipeline_unsupported", 404)
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
                if method == "GET" and path == "/api/resource/camera/pipeline":
                    payload = {
                        **payload,
                        "_rider_request_started_mono": request_started_mono,
                        "_rider_response_received_mono": time.monotonic(),
                    }
                self._check_generation(generation)
                return payload
        except httpx.TimeoutException as exc:
            self._log_lan_json_failure(path, method, generation, exc)
            if not self._preserve_media_on_timeout(
                generation,
                exc,
                passive=method == "GET"
                and not stop
                and body is None
                and not any(
                    name.lower() in {"authorization", "proxy-authorization"}
                    for name in request_headers
                ),
            ):
                await self._failed(generation, "pi_connection_timeout")
            if method != "GET":
                raise RiderError(
                    "pi_operation_timeout", 504, {"outcome": "unknown"}
                ) from exc
            raise RiderError(
                "pi_connection_timeout" if self.lan else "pi_unreachable", 503
            ) from exc
        except httpx.HTTPError as exc:
            self._log_lan_json_failure(path, method, generation, exc)
            await self._failed(generation, "pi_connect_failed")
            raise RiderError(
                "pi_connect_failed" if self.lan else "pi_unreachable", 503
            ) from exc

    async def image(self, path: str, *, body=None) -> tuple[bytes, str, dict]:
        admitted = time.monotonic()
        client, generation = await self._lane("http")
        if self.lan is not None and body is not None:
            await self.lan.revalidate(generation, started=admitted)
        try:
            async with client.stream(
                "POST" if body is not None else "GET",
                path,
                json=body,
                headers={"Accept": "image/png,image/jpeg"},
            ) as response:
                if response.is_redirect:
                    raise RiderError("pi_redirect_refused", 502)
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
                self._check_generation(generation)
                return bytes(content), mime, headers
        except httpx.HTTPError as exc:
            await self._failed(generation, "pi_connect_failed")
            raise RiderError(
                "pi_connect_failed" if self.lan else "pi_unreachable", 503
            ) from exc

    async def mjpeg(self):
        """One persistent camera reader; opening it never starts the Pi camera."""
        from pydantic import ValidationError

        from .stream_contract import NativeFrame, SourceDescription
        from .stream_framing import MultipartJpegParser, boundary_from_type

        client, generation = await self._lane("media")
        token = self._media_token = object()
        self._media_generation = generation
        self._media_frame = None
        self._media_valid_until = 0.0
        try:
            request = client.build_request(
                "GET",
                "/api/resource/camera/stream.mjpg",
                headers={"Accept": "multipart/x-mixed-replace"},
                timeout=httpx.Timeout(1.0, connect=0.5, pool=0.5),
            )
            response = await client.send(request, stream=True)
            chunks = cast(AsyncGenerator[bytes, None], response.aiter_bytes())
            reading = None
            try:
                if response.is_redirect:
                    raise RiderError("pi_redirect_refused", 502)
                if response.status_code >= 400:
                    raise RiderError("stream_unavailable", response.status_code)
                parser = MultipartJpegParser(
                    boundary_from_type(response.headers.get("content-type", ""))
                )
                while True:
                    # Do not deliver Task.cancel into httpcore while it may be
                    # releasing its pool slot after EOF/network failure. Keep
                    # just one owned I/O future, never a frame backlog.
                    reading = asyncio.ensure_future(anext(chunks))
                    try:
                        incoming = await asyncio.shield(reading)
                    except StopAsyncIteration:
                        break
                    reading = None
                    for offset in range(0, len(incoming), 16384):
                        chunk = incoming[offset : offset + 16384]
                        for headers, payload in parser.feed(chunk):
                            try:
                                source = SourceDescription.model_validate_json(
                                    headers.get("x-rider-source", "")
                                )
                            except (ValueError, ValidationError) as exc:
                                raise RiderError("stream_invalid_source", 502) from exc
                            frame = NativeFrame(source, payload, time.monotonic())
                            frame.validate_payload()
                            self._check_generation(generation)
                            if self._media_token is token:
                                self._media_frame = frame
                            yield frame
                parser.finish()
            finally:
                if self._media_token is token:
                    self._media_frame = None
                    self._media_valid_until = 0.0

                async def release():
                    try:
                        await response.aclose()
                    finally:
                        if reading is not None:
                            await asyncio.gather(reading, return_exceptions=True)
                        await chunks.aclose()

                await finish_native_cleanup(asyncio.create_task(release()))
            await self._failed(generation, "pi_connect_failed")
        except httpx.HTTPError as exc:
            await self._failed(generation, "pi_connect_failed")
            raise RiderError(
                "pi_connect_failed" if self.lan else "stream_unavailable", 503
            ) from exc
        finally:
            if self._media_token is token:
                self._media_token = None
                self._media_generation = None
                self._media_frame = None
                self._media_valid_until = 0.0

    async def close(self):
        self.closed = True
        if self.lan is not None:
            await self.lan.close()
        await self.http.aclose()
        await self.stop_http.aclose()
        await self.media_http.aclose()
