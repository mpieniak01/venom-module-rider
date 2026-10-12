"""One negotiated Pi transport, including binary media and an independent STOP lane."""

from __future__ import annotations

import json

import httpx

from .config import Device
from .errors import RiderError

ACCEPT = "application/vnd.rider.api+json;version=1"
MAX_JSON_BYTES = 512 * 1024
MAX_IMAGE_BYTES = 4 * 1024 * 1024
IMAGE_HEADERS = {
    "x-rider-contract",
    "x-rider-observed-at",
    "x-rider-file-observed-at",
    "x-rider-source",
    "x-rider-frame-id",
    "x-rider-stream",
    "x-rider-stale",
    "x-rider-placeholder",
    "x-face-source",
}


class PiClient:
    def __init__(self, device: Device, *, transport=None):
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

    async def json(self, path: str, *, method="GET", body=None, stop=False) -> dict:
        client = self.stop_http if stop else self.http
        try:
            async with client.stream(
                method, path, json=body, headers={"Accept": ACCEPT}
            ) as response:
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_JSON_BYTES:
                        raise RiderError("pi_response_too_large", 502)
                if response.is_redirect:
                    raise RiderError("pi_redirect_refused", 502)
                try:
                    payload = json.loads(content)
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
                        payload,
                    )
                meta = payload.get("meta")
                if not isinstance(meta, dict) or meta.get("envelope_version") != 1:
                    raise RiderError("unsupported_pi_contract", 502)
                if not isinstance(payload.get("data"), dict):
                    raise RiderError("invalid_pi_response", 502)
                return payload
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
