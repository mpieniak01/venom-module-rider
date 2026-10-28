"""Opt-in named LAN connections, verified and pinned for one pool generation."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import random
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .config import IDENTIFIER, Device
from .errors import RiderError
from .transport_cleanup import finish_native_cleanup

IDENTITY_PATH = "/api/v1/transport/identity"
MAX_IDENTITY_BYTES = 4096
DNS_TIMEOUT = 0.75
IDENTITY_TIMEOUT = 1.5
URGENT_IDENTITY_TIMEOUT = 0.5
CONNECT_BUDGET = DNS_TIMEOUT + IDENTITY_TIMEOUT
URGENT_CONNECT_BUDGET = DNS_TIMEOUT + URGENT_IDENTITY_TIMEOUT


async def resolve_address(host: str, port: int) -> tuple[str, ...]:
    """Return unique unscoped A/AAAA targets for this name in resolver order."""
    rows = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM),
        timeout=DNS_TIMEOUT,
    )
    addresses = []
    for family, _kind, _protocol, _name, endpoint in rows:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        # The URL-based pin does not carry sockaddr scope safely. Never drop
        # the interface and accidentally dial a different link-local peer.
        if "%" in endpoint[0] or (
            family == socket.AF_INET6 and len(endpoint) > 3 and endpoint[3]
        ):
            continue
        try:
            address = ipaddress.ip_address(endpoint[0])
        except ValueError:
            continue
        if (family == socket.AF_INET) != (address.version == 4):
            continue
        addresses.append(str(address))
    return address_candidates(addresses)


def address_candidates(resolved):
    # Keep injected single-address resolvers compatible with existing callers.
    values = (resolved,) if isinstance(resolved, str) else resolved
    addresses = tuple(
        dict.fromkeys(
            value
            for value in values
            if isinstance(value, str) and value and "%" not in value
        )
    )
    if not addresses:
        raise socket.gaierror("no supported unscoped peer address")
    return addresses


class PinnedTransport(httpx.AsyncBaseTransport):
    """Keep canonical Host/SNI while all lanes dial the verified address only."""

    def __init__(self, address, limits, *, transport=None):
        self.address = address
        self.inner = transport or httpx.AsyncHTTPTransport(
            trust_env=False, retries=0, limits=limits
        )

    async def handle_async_request(self, request):
        extensions = {**request.extensions, "sni_hostname": request.url.host}
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=self.address),
            headers=request.headers,
            stream=request.stream,
            extensions=extensions,
        )
        return await self.inner.handle_async_request(pinned)

    async def aclose(self):
        await self.inner.aclose()


@dataclass
class Generation:
    number: int
    http: httpx.AsyncClient
    stop: httpx.AsyncClient
    media: httpx.AsyncClient
    verified_mono: float = 0.0

    async def close(self):
        async def release():
            await asyncio.gather(
                self.http.aclose(), self.stop.aclose(), self.media.aclose()
            )

        await finish_native_cleanup(asyncio.create_task(release()))


class LanConnection:
    def __init__(self, device: Device, *, transport=None, resolver=None):
        self.device = device
        self.transport = transport
        self.resolver = resolver or resolve_address
        self.lock = asyncio.Lock()
        self.urgent_lock = asyncio.Lock()
        self.revision = 0
        self.current: Generation | None = None
        self.number = 0
        self.failures = 0
        self.retry_at = 0.0
        self.state = "unverified"
        self.error: str | None = None
        self.closed = False
        self.invalidated: Callable | None = None

    def snapshot(self):
        return {
            "profile": "trusted_lan",
            "state": self.state,
            "generation": self.number,
            "identity_verified": self.current is not None,
            "error": self.error,
            "retry_in_ms": max(0, round((self.retry_at - time.monotonic()) * 1000)),
        }

    def _pools(self, address):
        def client(connections, keepalive):
            limits = httpx.Limits(
                max_connections=connections, max_keepalive_connections=keepalive
            )
            return httpx.AsyncClient(
                base_url=self.device.url,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(1.5, connect=0.5, pool=0.5),
                transport=PinnedTransport(address, limits, transport=self.transport),
            )

        return Generation(self.number + 1, client(8, 4), client(2, 1), client(1, 1))

    async def _identity(self, generation, *, urgent=False):
        self.state = "verifying_identity"
        lane = generation.stop if urgent else generation.http
        async with lane.stream(
            "GET",
            IDENTITY_PATH,
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(
                0.5 if urgent else 1.5, connect=0.5, pool=0.1 if urgent else 0.5
            ),
        ) as response:
            if response.is_redirect:
                raise RiderError("pi_redirect_refused", 502)
            if response.status_code >= 400:
                raise RiderError("pi_identity_unavailable", 503)
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > MAX_IDENTITY_BYTES:
                    raise RiderError("pi_identity_invalid", 502)
            try:
                identity = json.loads(content)
            except ValueError as exc:
                raise RiderError("pi_identity_invalid", 502) from exc
            if (
                not isinstance(identity, dict)
                or type(identity.get("protocol_version")) is not int
                or identity.get("protocol_version") != 1
                or identity.get("service_role") != "rider_pi"
                or not isinstance(identity.get("peer_id"), str)
                or not IDENTIFIER.fullmatch(identity["peer_id"])
            ):
                raise RiderError("pi_identity_invalid", 502)
            if identity["peer_id"] != self.device.expected_peer_id:
                raise RiderError("pi_identity_mismatch", 503)
            generation.verified_mono = time.monotonic()

    def _backoff(self, error):
        self.failures += 1
        self.retry_at = time.monotonic() + min(
            30.0, 2 ** min(self.failures - 1, 5) * random.uniform(0.8, 1.2)
        )
        self.state = (
            "identity_mismatch" if error == "pi_identity_mismatch" else "backoff"
        )
        self.error = error

    @staticmethod
    def _error(exc):
        if isinstance(exc, RiderError):
            return exc
        if isinstance(exc, (socket.gaierror, TimeoutError)):
            return RiderError("pi_name_unresolved", 503)
        if isinstance(exc, httpx.TimeoutException):
            return RiderError("pi_connection_timeout", 503)
        return RiderError("pi_connect_failed", 503)

    async def _verify_identity(self, generation, *, urgent=False):
        # A slow-drip body cannot extend a preflight one read timeout at a time.
        try:
            async with asyncio.timeout(
                URGENT_IDENTITY_TIMEOUT if urgent else IDENTITY_TIMEOUT
            ):
                await self._identity(generation, urgent=urgent)
        except TimeoutError as exc:
            raise httpx.ReadTimeout("identity preflight deadline exceeded") from exc

    async def acquire(self, *, urgent=False):
        if self.closed:
            raise RiderError("module_closed", 503)
        if self.current is not None:
            return self.current
        revision, failures = self.revision, self.failures
        try:
            # One budget includes lock admission, DNS and ALL candidate probes;
            # an additional A/AAAA record cannot multiply STOP's deadline.
            async with asyncio.timeout(
                URGENT_CONNECT_BUDGET if urgent else CONNECT_BUDGET
            ):
                # STOP has its own lane, bypassing ordinary resolver/backoff.
                async with self.urgent_lock if urgent else self.lock:
                    if self.closed:
                        raise RiderError("module_closed", 503)
                    if self.current is not None:
                        return self.current
                    if not urgent and time.monotonic() < self.retry_at:
                        raise RiderError(self.error or "pi_connect_failed", 503)
                    return await self._connect(urgent=urgent)
        except TimeoutError as exc:
            if revision == self.revision and not self.closed:
                if self.current is not None:
                    self.state = "online"
                    return self.current
                if self.failures == failures:
                    self._backoff("pi_connection_timeout")
            raise RiderError("pi_connection_timeout", 503) from exc

    async def _connect(self, *, urgent):
        generation, revision = None, self.revision
        try:
            parsed = urlsplit(self.device.url)
            host = parsed.hostname
            if host is None:
                raise RiderError("invalid_device_configuration", 503)
            self.state = "resolving"
            # An explicitly supplied in-process test transport does not use DNS.
            resolved: str | tuple[str, ...]
            if self.transport is not None and self.resolver is resolve_address:
                resolved = host
            else:
                lookup = asyncio.wait_for(
                    self.resolver(
                        host, parsed.port or (443 if parsed.scheme == "https" else 80)
                    ),
                    timeout=DNS_TIMEOUT,
                )
                resolved = await lookup
            last_error = None
            for address in address_candidates(resolved):
                self.state = "connecting"
                generation = self._pools(address)
                try:
                    await self._verify_identity(generation, urgent=urgent)
                    break
                except (httpx.HTTPError, httpx.InvalidURL) as exc:
                    # Only transport failures allow another target. Responding
                    # wrong/invalid identities or redirects stay fail-closed.
                    last_error = exc
                    await generation.close()
                    generation = None
            else:
                if last_error is not None:
                    raise last_error
                raise socket.gaierror("no peer candidate available")
            if self.closed:
                raise RiderError("module_closed", 503)
            if revision != self.revision:
                raise RiderError("stale_transport_generation", 409)
            if self.current is None:
                generation.number = self.number + 1
                self.number = generation.number
                self.current = generation
            self.state, self.error = "online", None
            self.retry_at, self.failures = 0.0, 0
            return self.current
        except (
            socket.gaierror,
            TimeoutError,
            httpx.HTTPError,
            httpx.InvalidURL,
            RiderError,
        ) as exc:
            error = self._error(exc)
            if (
                self.current is not None
                and revision == self.revision
                and not self.closed
            ):
                self.state = "online"
                # A parallel healthy generation can rescue transport failure,
                # but cannot override this request's terminal identity result.
                if not isinstance(exc, RiderError):
                    return self.current
            elif revision == self.revision and not self.closed:
                self._backoff(error.code)
            raise error from exc
        finally:
            if generation is not None and self.current is not generation:
                await generation.close()

    async def revalidate(self, generation, *, started, urgent=False):
        self.check(generation)
        # A handshake completed for this admission already covers its command.
        if generation.verified_mono >= started:
            return
        try:
            await self._verify_identity(generation, urgent=urgent)
            self.check(generation)
            self.state = "online"
        except (httpx.HTTPError, httpx.InvalidURL, RiderError) as exc:
            error = self._error(exc)
            await self.fail(generation, error.code, owner=asyncio.current_task())
            raise error from exc

    def check(self, generation):
        if self.closed or self.current is not generation:
            raise RiderError("stale_transport_generation", 409)

    async def fail(self, generation, error, *, owner=None):
        if self.current is not generation:
            return
        self.current = None
        self.revision += 1
        self._backoff(error)
        if self.invalidated is not None:
            self.invalidated(owner)
        await generation.close()

    async def reconnect(self):
        # Explicit Refresh bypasses backoff once, but never queues a mutation.
        async with self.lock:
            generation, self.current = self.current, None
            self.revision += 1
            self.retry_at = 0.0
            self.state, self.error = "unverified", None
            if self.invalidated is not None:
                self.invalidated()
            if generation is not None:
                await generation.close()

    async def close(self):
        self.closed = True
        self.revision += 1
        async with self.lock, self.urgent_lock:
            generation, self.current = self.current, None
            if generation is not None:
                await generation.close()
