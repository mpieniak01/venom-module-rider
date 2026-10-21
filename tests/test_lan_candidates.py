"""Named LAN candidates fail over without extending STOP or trusting another peer."""

import asyncio
import socket
import time

import httpx
import pytest
from test_lan_transport import DEVICE, identity, response

from venom_module_rider import lan_transport
from venom_module_rider.client import PiClient
from venom_module_rider.errors import RiderError
from venom_module_rider.lan_transport import IDENTITY_PATH, resolve_address


def track_pools(client):
    generations = []
    original = client.lan._pools

    def pools(address):
        generation = original(address)
        generations.append(generation)
        return generation

    client.lan._pools = pools
    return generations


def closed(generation):
    return all(
        pool.is_closed for pool in (generation.http, generation.stop, generation.media)
    )


async def test_resolver_keeps_unique_ordered_unscoped_a_and_aaaa_only(monkeypatch):
    async def lookup(host, port, **kwargs):
        assert (host, port, kwargs) == (
            "rider.test",
            8080,
            {"type": socket.SOCK_STREAM},
        )
        return [
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                6,
                "",
                ("2001:0db8:0:0:0:0:0:7", port, 0, 0),
            ),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::7", port, 0, 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1", port, 0, 7)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1%eth0", port, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.7", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.7", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-address", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("2001:db8::7", port)),
            (socket.AF_UNIX, socket.SOCK_STREAM, 0, "", ("unused", port)),
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    assert await resolve_address("rider.test", 8080) == ("2001:db8::7", "192.0.2.7")


@pytest.mark.parametrize("scope", [0, 3])
async def test_scoped_ipv6_only_name_is_explicitly_rejected_before_http(
    monkeypatch, scope
):
    calls = []

    async def lookup(*args, **kwargs):
        address = "fe80::1" if scope else "fe80::1%eth0"
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 8080, 0, scope))]

    async def resolver(host, port):
        return await resolve_address(host, port)

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    client = PiClient(
        DEVICE,
        transport=httpx.MockTransport(lambda r: calls.append(r)),
        resolver=resolver,
    )
    try:
        with pytest.raises(RiderError, match="pi_name_unresolved"):
            await client.json("/api/control", method="POST", body={})
        assert not calls and client.lan.number == 0
    finally:
        await client.close()


@pytest.mark.parametrize("first_error", [httpx.ConnectError, httpx.ConnectTimeout])
async def test_unreachable_first_unique_candidate_falls_through_with_own_identity(
    monkeypatch, first_error
):
    monkeypatch.setenv("RIDER_AGENT_TOKEN", "fixture-token")
    calls = []

    async def resolver(host, port):
        return ("2001:db8::1", "2001:db8::1", "192.0.2.7")

    def handle(request):
        calls.append(request)
        if request.url.host == "2001:db8::1":
            raise first_error("fixture target unreachable", request=request)
        if request.url.path == IDENTITY_PATH:
            return httpx.Response(200, json=identity())
        return response(request.url.path)

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle), resolver=resolver)
    generations = track_pools(client)
    try:
        await client.json("/api/control/agent/pulse", method="POST", body={})
        assert [(r.method, r.url.host, r.url.path) for r in calls] == [
            ("GET", "2001:db8::1", IDENTITY_PATH),
            ("GET", "192.0.2.7", IDENTITY_PATH),
            ("POST", "192.0.2.7", "/api/control/agent/pulse"),
        ]
        assert all("authorization" not in r.headers for r in calls if r.method == "GET")
        assert calls[-1].headers["authorization"] == "Bearer fixture-token"
        assert all(
            r.headers["host"] == "rider.test:8080"
            and r.extensions["sni_hostname"] == "rider.test"
            for r in calls
        )
        assert len(generations) == 2 and closed(generations[0])
        assert client.lan.current is generations[1] and client.lan.number == 1
        assert not closed(generations[1])
    finally:
        await client.close()
    assert all(closed(generation) for generation in generations)


async def test_unscoped_ipv6_candidate_pins_host_without_changing_canonical_host():
    calls = []

    async def resolver(host, port):
        return ("2001:db8::7",)

    def handle(request):
        calls.append(request)
        return (
            httpx.Response(200, json=identity())
            if request.url.path == IDENTITY_PATH
            else response(request.url.path)
        )

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle), resolver=resolver)
    try:
        await client.json("/state")
        assert len(calls) == 2
        assert all(
            r.url.host == "2001:db8::7" and r.headers["host"] == "rider.test:8080"
            for r in calls
        )
    finally:
        await client.close()


@pytest.mark.parametrize("address", ["fe80::1%3", "fe80::1%eth0"])
async def test_injected_scoped_address_is_not_silently_unscoped(address):
    calls = []

    async def resolver(host, port):
        return address

    client = PiClient(
        DEVICE,
        transport=httpx.MockTransport(lambda r: calls.append(r)),
        resolver=resolver,
    )
    try:
        with pytest.raises(RiderError, match="pi_name_unresolved"):
            await client.json("/state")
        assert not calls
    finally:
        await client.close()


async def test_invalid_url_candidate_is_caught_closed_and_followed_by_verified_target():
    calls = []

    async def resolver(host, port):
        return ("::not-an-ip::", "192.0.2.7")

    def handle(request):
        calls.append(request)
        return (
            httpx.Response(200, json=identity())
            if request.url.path == IDENTITY_PATH
            else response(request.url.path)
        )

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle), resolver=resolver)
    generations = track_pools(client)
    try:
        await client.json("/state")
        assert len(generations) == 2 and closed(generations[0])
        assert [(r.url.host, r.url.path) for r in calls] == [
            ("192.0.2.7", IDENTITY_PATH),
            ("192.0.2.7", "/state"),
        ]
    finally:
        await client.close()


@pytest.mark.parametrize(
    "payload,status,error",
    [
        (identity(peer_id="different-peer"), 200, "pi_identity_mismatch"),
        (identity(service_role="venom"), 200, "pi_identity_invalid"),
        (None, 302, "pi_redirect_refused"),
    ],
)
async def test_responding_foreign_candidate_stops_fallthrough_without_credentials(
    monkeypatch, payload, status, error
):
    monkeypatch.setenv("RIDER_AGENT_TOKEN", "fixture-token")
    calls = []

    async def resolver(host, port):
        return ("192.0.2.1", "192.0.2.7")

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status, json=payload, headers={"location": "http://foreign.test/"}
        )

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle), resolver=resolver)
    generations = track_pools(client)
    try:
        with pytest.raises(RiderError, match=error):
            await client.json("/api/control/agent/pulse", method="POST", body={})
        assert [(r.method, r.url.host, r.url.path) for r in calls] == [
            ("GET", "192.0.2.1", IDENTITY_PATH)
        ]
        assert all("authorization" not in r.headers for r in calls)
        assert client.lan.current is None and client.lan.number == 0
        assert not client.credential_transport_allowed and all(
            closed(g) for g in generations
        )
    finally:
        await client.close()


async def test_urgent_candidates_share_one_dns_and_preflight_deadline(monkeypatch):
    monkeypatch.setattr(lan_transport, "URGENT_CONNECT_BUDGET", 0.12)
    monkeypatch.setattr(lan_transport, "URGENT_IDENTITY_TIMEOUT", 0.07)
    calls, lookups = [], []

    async def resolver(host, port):
        lookups.append(host)
        await asyncio.sleep(0.02)
        return tuple(f"192.0.2.{i}" for i in range(1, 9))

    async def handle(request):
        calls.append(request)
        await asyncio.sleep(10)
        return httpx.Response(200, json=identity())

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle), resolver=resolver)
    generations = track_pools(client)
    started = time.monotonic()
    try:
        with pytest.raises(RiderError, match="pi_connection_timeout"):
            await client.json(
                "/api/safety", method="POST", body={"action": "stop"}, stop=True
            )
        assert time.monotonic() - started < 0.3
        assert len(lookups) == 1 and 1 <= len(calls) <= 2
        assert all(r.method == "GET" and r.url.path == IDENTITY_PATH for r in calls)
        assert client.lan.number == 0 and all(closed(g) for g in generations)
    finally:
        await client.close()


async def test_urgent_lock_wait_is_inside_the_shared_deadline(monkeypatch):
    monkeypatch.setattr(lan_transport, "URGENT_CONNECT_BUDGET", 0.03)
    client = PiClient(
        DEVICE,
        transport=httpx.MockTransport(lambda r: pytest.fail("unexpected request")),
    )
    await client.lan.urgent_lock.acquire()
    try:
        with pytest.raises(RiderError, match="pi_connection_timeout"):
            await asyncio.wait_for(client.lan.acquire(urgent=True), 0.2)
    finally:
        client.lan.urgent_lock.release()
        await client.close()


async def test_cancelling_second_candidate_closes_all_owned_pools():
    calls, entered = [], asyncio.Event()

    async def resolver(host, port):
        return ("192.0.2.1", "192.0.2.7")

    async def handle(request):
        calls.append(request)
        if request.url.host == "192.0.2.1":
            raise httpx.ConnectError("first target unavailable", request=request)
        entered.set()
        await asyncio.Event().wait()

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle), resolver=resolver)
    generations = track_pools(client)
    task = asyncio.create_task(client.json("/api/control", method="POST", body={}))
    try:
        await asyncio.wait_for(entered.wait(), 0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(generations) == 2 and all(closed(g) for g in generations)
        assert all(r.method == "GET" for r in calls) and client.lan.current is None
    finally:
        await client.close()


async def test_identity_slow_drip_cannot_renew_preflight_deadline(monkeypatch):
    monkeypatch.setattr(lan_transport, "IDENTITY_TIMEOUT", 0.04)
    released = asyncio.Event()

    class Drip(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"{"
            while True:
                await asyncio.sleep(0.02)
                yield b" "

        async def aclose(self):
            released.set()

    client = PiClient(
        DEVICE,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Drip())),
    )
    try:
        with pytest.raises(RiderError, match="pi_connection_timeout"):
            await asyncio.wait_for(client.json("/state"), 0.2)
        assert released.is_set() and client.lan.current is None
    finally:
        await client.close()


async def test_expired_parallel_probe_uses_only_the_other_verified_generation(
    monkeypatch,
):
    monkeypatch.setattr(lan_transport, "CONNECT_BUDGET", 0.05)
    entered, calls, lookups = asyncio.Event(), [], []

    async def resolver(host, port):
        lookups.append(host)
        return "192.0.2.1" if len(lookups) == 1 else "192.0.2.7"

    async def handle(request):
        calls.append(request)
        if request.url.host == "192.0.2.1":
            entered.set()
            await asyncio.Event().wait()
        return (
            httpx.Response(200, json=identity())
            if request.url.path == IDENTITY_PATH
            else response(request.url.path)
        )

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle), resolver=resolver)
    generations = track_pools(client)
    ordinary = asyncio.create_task(client.json("/state"))
    try:
        await asyncio.wait_for(entered.wait(), 0.2)
        await client.json(
            "/api/safety", method="POST", body={"action": "stop"}, stop=True
        )
        await asyncio.wait_for(ordinary, 0.2)
        assert client.lan.number == 1 and client.transport_status()["state"] == "online"
        assert client.lan.current is generations[1] and closed(generations[0])
        assert [(r.method, r.url.path) for r in calls if r.url.host == "192.0.2.1"] == [
            ("GET", IDENTITY_PATH)
        ]
        assert [(r.method, r.url.path) for r in calls if r.url.host == "192.0.2.7"] == [
            ("GET", IDENTITY_PATH),
            ("POST", "/api/safety"),
            ("GET", "/state"),
        ]
    finally:
        if not ordinary.done():
            ordinary.cancel()
        await asyncio.gather(ordinary, return_exceptions=True)
        await client.close()


async def test_refresh_during_candidate_fallthrough_rejects_late_identity():
    entered, release, calls = asyncio.Event(), asyncio.Event(), []

    async def resolver(host, port):
        return ("192.0.2.1", "192.0.2.7")

    async def handle(request):
        calls.append(request)
        if request.url.host == "192.0.2.1":
            raise httpx.ConnectError("unreachable first address", request=request)
        entered.set()
        await release.wait()
        return httpx.Response(200, json=identity())

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle), resolver=resolver)
    generations = track_pools(client)
    command = asyncio.create_task(
        client.json("/api/safety", method="POST", body={"action": "stop"}, stop=True)
    )
    try:
        await asyncio.wait_for(entered.wait(), 0.2)
        await client.reconnect()
        release.set()
        with pytest.raises(RiderError, match="stale_transport_generation"):
            await command
        assert client.lan.current is None and client.lan.number == 0
        assert len(generations) == 2 and all(closed(g) for g in generations)
        assert all(
            r.method == "GET" and "authorization" not in r.headers for r in calls
        )
    finally:
        release.set()
        await asyncio.gather(command, return_exceptions=True)
        await client.close()


@pytest.mark.parametrize(
    "payload,status,error",
    [
        (identity(peer_id="different-peer"), 200, "pi_identity_mismatch"),
        (identity(service_role="venom"), 200, "pi_identity_invalid"),
        (None, 302, "pi_redirect_refused"),
    ],
)
async def test_terminal_probe_error_does_not_use_or_retire_parallel_verified_current(
    monkeypatch, payload, status, error
):
    monkeypatch.setenv("RIDER_AGENT_TOKEN", "fixture-token")
    entered, release, calls, lookups = asyncio.Event(), asyncio.Event(), [], []

    async def resolver(host, port):
        lookups.append(host)
        return "192.0.2.1" if len(lookups) == 1 else "192.0.2.7"

    async def handle(request):
        calls.append(request)
        if request.url.host == "192.0.2.1":
            entered.set()
            await release.wait()
            return httpx.Response(
                status, json=payload, headers={"location": "http://foreign.test/"}
            )
        return (
            httpx.Response(200, json=identity())
            if request.url.path == IDENTITY_PATH
            else response(request.url.path)
        )

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle), resolver=resolver)
    generations = track_pools(client)
    command = asyncio.create_task(
        client.json("/api/control/agent/pulse", method="POST", body={})
    )
    try:
        await asyncio.wait_for(entered.wait(), 0.2)
        await client.json(
            "/api/safety", method="POST", body={"action": "stop"}, stop=True
        )
        healthy = client.lan.current
        release.set()
        with pytest.raises(RiderError, match=error):
            await command
        assert client.lan.current is healthy and healthy is generations[1]
        assert client.lan.number == 1 and client.transport_status()["state"] == "online"
        assert client.lan.retry_at == 0 and closed(generations[0])
        assert all(
            not pool.is_closed for pool in (healthy.http, healthy.stop, healthy.media)
        )
        assert not any(r.url.path == "/api/control/agent/pulse" for r in calls)
        assert all("authorization" not in r.headers for r in calls)
        await client.json("/state")
    finally:
        release.set()
        await asyncio.gather(command, return_exceptions=True)
        await client.close()
