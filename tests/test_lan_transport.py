"""Isolated HTTP LAN fixtures: identity, address changes, no replay or hardware."""

import asyncio
import json
import socket

import httpx
import pytest

from venom_module_rider import connection_recovery
from venom_module_rider.client import PiClient, expected_contract
from venom_module_rider.config import Device, configured_devices
from venom_module_rider.demo import DemoPi, create_app
from venom_module_rider.errors import RiderError
from venom_module_rider.lan_transport import IDENTITY_PATH, resolve_address
from venom_module_rider.service import RiderService

PEER = "fixture_peer_01"
DEVICE = Device(
    "prod",
    "LAN fixture",
    "http://rider.test:8080",
    transport_profile="trusted_lan",
    expected_peer_id=PEER,
    reconnect_command="/must/not/run",
)


def identity(**changes):
    return {
        "protocol_version": 1,
        "peer_id": PEER,
        "service_role": "rider_pi",
        **changes,
    }


def response(path, **data):
    contract, version = expected_contract(path)
    return httpx.Response(
        200,
        json={
            "meta": {
                "envelope_version": 1,
                "contract_id": contract,
                "contract_version": version,
            },
            "data": data,
        },
    )


def test_profiles_are_opt_in_and_private_peer_binding_is_required():
    row = {"id": "prod", "label": "Pi", "url": "http://rider.test:8080"}
    old = configured_devices(json.dumps([row]))[0]
    assert old.transport_profile == "secure" and old.expected_peer_id is None
    for extra in [
        {"transport_profile": "trusted_lan"},
        {"transport_profile": "trusted_lan", "expected_peer_id": ""},
        {"transport_profile": "trusted_lan", "expected_peer_id": "bad/id"},
        {"transport_profile": "trusted_lan", "expected_peer_id": PEER, "mode": "demo"},
        {"transport_profile": "invalid"},
        {"transport_profile": []},
    ]:
        with pytest.raises(RiderError, match="invalid_device_configuration"):
            configured_devices(json.dumps([{**row, **extra}]))
    new = configured_devices(
        json.dumps(
            [{**row, "transport_profile": "trusted_lan", "expected_peer_id": PEER}]
        )
    )[0]
    assert new.id == old.id and new.url == old.url
    assert "expected_peer_id" not in new.public() and "url" not in new.public()


@pytest.mark.parametrize(
    "path",
    [
        "/api/control/agent/pulse",
        "/api/voice/playback",
        "/api/voice/playback/cancel",
        "/api/control",
        "/api/safety",
    ],
)
async def test_identity_precedes_commands_and_tokens_and_pins_canonical_host(
    monkeypatch, path
):
    monkeypatch.setenv("RIDER_AGENT_TOKEN", "fixture-only-token")
    calls, names = [], []

    async def resolver(host, port):
        names.append((host, port))
        return "192.0.2.7"

    def handle(request):
        calls.append(request)
        if request.url.path == IDENTITY_PATH:
            return httpx.Response(200, json=identity())
        return response(request.url.path)

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle), resolver=resolver)
    try:
        assert not client.credential_transport_allowed
        await client.json(
            path, method="POST", body={"fixture": True}, stop=path == "/api/safety"
        )
        assert [r.url.path for r in calls] == [IDENTITY_PATH, path]
        assert calls[0].method == "GET" and "authorization" not in calls[0].headers
        assert names == [("rider.test", 8080)]
        assert all(
            r.url.host == "192.0.2.7"
            and r.headers["host"] == "rider.test:8080"
            and r.extensions["sni_hostname"] == "rider.test"
            for r in calls
        )
        if path in {
            "/api/control/agent/pulse",
            "/api/voice/playback",
            "/api/voice/playback/cancel",
        }:
            assert calls[1].headers["authorization"] == "Bearer fixture-only-token"
        assert (
            client.credential_transport_allowed
            and not client.credential_transport_secure
        )
        generation = client.lan.current
        assert (
            len({id(generation.http), id(generation.stop), id(generation.media)}) == 3
        )
        assert client.transport_status()["identity_verified"]
    finally:
        await client.close()
    assert all(
        c.is_closed for c in [generation.http, generation.stop, generation.media]
    )


@pytest.mark.parametrize(
    "payload,status,code",
    [
        (identity(peer_id="different"), 200, "pi_identity_mismatch"),
        (identity(service_role="venom"), 200, "pi_identity_invalid"),
        (identity(protocol_version=True), 200, "pi_identity_invalid"),
        (identity(protocol_version=2), 200, "pi_identity_invalid"),
        (identity(peer_id="bad/id"), 200, "pi_identity_invalid"),
        ([], 200, "pi_identity_invalid"),
        (None, 503, "pi_identity_unavailable"),
        (None, 302, "pi_redirect_refused"),
    ],
)
async def test_foreign_or_unsupported_peer_never_receives_credentials_or_command(
    monkeypatch, payload, status, code
):
    monkeypatch.setenv("RIDER_AGENT_TOKEN", "fixture-only-token")
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status, json=payload, headers={"location": "http://foreign.test/"}
        )

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle))
    try:
        for _ in range(2):
            with pytest.raises(RiderError, match=code):
                await client.json("/api/control/agent/pulse", method="POST", body={})
        assert len(calls) == 1  # bounded backoff, no automatic redirect or retry
        assert (
            calls[0].url.path == IDENTITY_PATH
            and "authorization" not in calls[0].headers
        )
        assert not client.credential_transport_allowed
        assert 0 < client.transport_status()["retry_in_ms"] <= 1200
    finally:
        await client.close()


@pytest.mark.parametrize("content", [b"{bad", b"x" * 4097])
async def test_identity_body_is_bounded_and_valid_json(content):
    client = PiClient(
        DEVICE,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content)),
    )
    try:
        with pytest.raises(RiderError, match="pi_identity_invalid"):
            await client.json("/state")
    finally:
        await client.close()


async def test_secure_profile_preserves_existing_http_credential_guard(monkeypatch):
    monkeypatch.setenv("RIDER_AGENT_TOKEN", "fixture-only-token")
    calls = []
    client = PiClient(
        Device("old", "Old", "http://rider.test"),
        transport=httpx.MockTransport(lambda req: calls.append(req)),
    )
    try:
        with pytest.raises(RiderError, match="agent_transport_insecure"):
            await client.json("/api/voice/playback", method="POST", body=b"fixture")
        assert not calls and client.lan is None
    finally:
        await client.close()


async def test_new_generation_reresolves_name_closes_pools_and_never_replays_post():
    address, fail, calls, lookups = ["192.0.2.1"], [False], [], []

    async def resolver(host, port):
        lookups.append((host, port))
        return address[0]

    def handle(request):
        calls.append((request.method, request.url.host, request.url.path))
        if request.url.path == IDENTITY_PATH:
            return httpx.Response(200, json=identity())
        if fail[0]:
            raise httpx.ConnectError("fixture connection lost", request=request)
        return response(request.url.path)

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle), resolver=resolver)
    try:
        await client.json("/state")
        old = client.lan.current
        address[0], fail[0] = "192.0.2.2", True
        with pytest.raises(RiderError, match="pi_connect_failed"):
            await client.json(
                "/api/control", method="POST", body={"request_id": "once"}
            )
        assert all(c.is_closed for c in [old.http, old.stop, old.media])
        assert client.lan.current is None
        fail[0], client.lan.retry_at = False, 0
        await client.json("/state")
        assert len(lookups) == 2 and client.device.id == "prod"
        assert client.lan.current.number == 2
        assert [r for r in calls if r[0] == "POST"] == [
            ("POST", "192.0.2.1", "/api/control")
        ]
        assert calls[-2:] == [
            ("GET", "192.0.2.2", IDENTITY_PATH),
            ("GET", "192.0.2.2", "/state"),
        ]
    finally:
        await client.close()


async def test_concurrent_reads_coalesce_one_identity_and_old_reply_is_discarded():
    entered, release, identities = asyncio.Event(), asyncio.Event(), []

    async def handle(request):
        if request.url.path == IDENTITY_PATH:
            identities.append(request)
            return httpx.Response(200, json=identity())
        if request.url.path == "/state":
            entered.set()
            await release.wait()
        return response(request.url.path)

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle))
    try:
        old = asyncio.create_task(client.json("/state"))
        await entered.wait()
        await asyncio.gather(*(client.json("/healthz") for _ in range(8)))
        assert len(identities) == 1
        await client.reconnect()
        await client.json("/healthz")
        release.set()
        with pytest.raises(RiderError, match="stale_transport_generation"):
            await old
        assert len(identities) == 2
    finally:
        release.set()
        await client.close()


@pytest.mark.parametrize(
    "kind,code",
    [
        ("dns", "pi_name_unresolved"),
        ("timeout", "pi_connection_timeout"),
        ("connect", "pi_connect_failed"),
    ],
)
async def test_connection_diagnostics_and_backoff_distinguish_failure_kind(kind, code):
    lookups, calls = [], []

    async def resolver(host, port):
        lookups.append(host)
        if kind == "dns":
            raise socket.gaierror("fixture name absent")
        return "192.0.2.7"

    def handle(request):
        calls.append(request)
        error = httpx.ReadTimeout if kind == "timeout" else httpx.ConnectError
        raise error("fixture", request=request)

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle), resolver=resolver)
    try:
        for _ in range(5):
            with pytest.raises(RiderError, match=code):
                await client.json("/state")
        assert len(lookups) == 1 and len(calls) == (0 if kind == "dns" else 1)
        for _ in range(10):
            client.lan._backoff(code)
        assert client.transport_status()["retry_in_ms"] <= 30000
    finally:
        await client.close()


async def test_default_resolver_only_looks_up_configured_name(monkeypatch):
    seen = []

    async def lookup(host, port, **kwargs):
        seen.append((host, port, kwargs))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.7", port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    assert await resolve_address("rider.test", 8080) == ("192.0.2.7",)
    assert seen == [("rider.test", 8080, {"type": socket.SOCK_STREAM})]

    async def empty(*args, **kwargs):
        return []

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", empty)
    with pytest.raises(socket.gaierror):
        await resolve_address("rider.test", 8080)


async def test_refresh_coalesces_reresolves_and_never_invokes_ssh_or_changes_selection(
    monkeypatch, tmp_path
):
    pi, calls, lookups = DemoPi(), [], []
    app = create_app(pi)
    entered, release = asyncio.Event(), asyncio.Event()
    slow = [False]

    async def resolver(host, port):
        lookups.append(host)
        if slow[0]:
            entered.set()
            await release.wait()
        return "192.0.2.7"

    async def handle(request):
        calls.append(request)
        if request.url.path == IDENTITY_PATH:
            return httpx.Response(200, json=identity())
        return await httpx.ASGITransport(app=app).handle_async_request(request)

    async def forbidden(_):
        pytest.fail("trusted LAN invoked SSH recovery")

    monkeypatch.setattr(connection_recovery, "recover_connection", forbidden)
    service = RiderService(
        (DEVICE,),
        selection_file=tmp_path / "choice.json",
        client_factory=lambda d: PiClient(
            d, transport=httpx.MockTransport(handle), resolver=resolver
        ),
    )
    await service.restore("prod")
    session = service.session
    try:
        assert (await service.status("navigation"))["connection"]["status"] == "online"
        old = service.client.lan.current
        slow[0] = True
        first = asyncio.create_task(service.refresh(session))
        await entered.wait()
        second = asyncio.create_task(service.refresh(session))
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(first, second)
        assert len(lookups) == 2 and all(
            r["recovery"]["state"] == "recovered" for r in results
        )
        assert service.session == session and service.device.id == "prod"
        assert (
            not pi.camera_on
            and pi.state == "DISARMED"
            and all(r.method == "GET" for r in calls)
        )
        assert not service.selection_file.exists()
        assert all(c.is_closed for c in [old.http, old.stop, old.media])
        assert results[0]["connection"]["transport"]["identity_verified"]
    finally:
        release.set()
        await service.close()


async def test_http_close_server_supports_verified_authenticated_commands(
    monkeypatch,
):
    """Flask's close-after-response behavior must not force credentials back to SSH."""
    monkeypatch.setenv("RIDER_AGENT_TOKEN", "fixture-only-token")
    calls = []

    async def handle(reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode().split("\r\n")
        method, path, _ = head[0].split(" ")
        headers = dict(line.split(": ", 1) for line in head[1:] if ": " in line)
        length = int(headers.get("Content-Length", "0"))
        if length:
            await reader.readexactly(length)
        calls.append((method, path, headers))
        content = (
            json.dumps(identity()).encode()
            if path == IDENTITY_PATH
            else response(path).content
        )
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Connection: close\r\nContent-Length: "
            + str(len(content)).encode()
            + b"\r\n\r\n"
            + content
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def resolver(host, requested_port):
        assert host == "close-server.test" and requested_port == port
        return "127.0.0.1"

    device = Device(
        "prod",
        "Close-server fixture",
        f"http://close-server.test:{port}",
        transport_profile="trusted_lan",
        expected_peer_id=PEER,
    )
    client = PiClient(device, resolver=resolver)
    try:
        await client.json("/api/control/agent/pulse", method="POST", body={})
        await client.json("/api/voice/playback/cancel", method="POST", body={})
        assert [r[1] for r in calls] == [
            IDENTITY_PATH,
            "/api/control/agent/pulse",
            IDENTITY_PATH,
            "/api/voice/playback/cancel",
        ]
        assert all(row[2]["Host"] == f"close-server.test:{port}" for row in calls)
        assert "Authorization" not in calls[0][2]
        assert all(
            row[2]["Authorization"] == "Bearer fixture-only-token"
            for row in calls
            if row[0] == "POST"
        )
        assert all("Authorization" not in row[2] for row in calls if row[0] == "GET")
        assert client.lan.number == 1
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


async def test_same_pinned_address_reassigned_without_http_error_is_reverified(
    monkeypatch,
):
    monkeypatch.setenv("RIDER_AGENT_TOKEN", "fixture-only-token")
    peer, calls = [PEER], []

    def handle(request):
        calls.append(request)
        if request.url.path == IDENTITY_PATH:
            return httpx.Response(200, json=identity(peer_id=peer[0]))
        return response(request.url.path)

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle))
    try:
        await client.json("/state")
        peer[0] = "different-peer-at-same-address"
        with pytest.raises(RiderError, match="pi_identity_mismatch"):
            await client.json("/api/voice/playback", method="POST", body=b"fixture")
        assert [r.url.path for r in calls] == [IDENTITY_PATH, "/state", IDENTITY_PATH]
        assert all(
            r.method == "GET" and "authorization" not in r.headers for r in calls
        )
        assert client.lan.current is None and not client.credential_transport_allowed
    finally:
        await client.close()


async def test_stop_bypasses_a_stalled_ordinary_resolver_and_coalesces_generation():
    entered, release, calls, resolutions = asyncio.Event(), asyncio.Event(), [], []

    async def resolver(host, port):
        resolutions.append(host)
        if len(resolutions) == 1:
            entered.set()
            await release.wait()
        return "192.0.2.7"

    def handle(request):
        calls.append(request)
        return (
            httpx.Response(200, json=identity())
            if request.url.path == IDENTITY_PATH
            else response(request.url.path)
        )

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle), resolver=resolver)
    ordinary = asyncio.create_task(client.json("/state"))
    try:
        await entered.wait()
        await asyncio.wait_for(
            client.json(
                "/api/safety", method="POST", body={"action": "stop"}, stop=True
            ),
            0.2,
        )
        assert not ordinary.done()
        assert len([r for r in calls if r.method == "POST"]) == 1
        release.set()
        await ordinary
        assert client.lan.number == 1
    finally:
        release.set()
        await ordinary
        await client.close()


@pytest.mark.parametrize("failed_lane", ["json", "media"])
async def test_stop_revalidates_independently_during_transient_backoff(failed_lane):
    fail, calls = [False], []

    def handle(request):
        calls.append(request)
        if request.url.path == IDENTITY_PATH:
            return httpx.Response(200, json=identity())
        if fail[0]:
            raise httpx.ReadTimeout("fixture disconnect", request=request)
        return response(request.url.path)

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle))
    try:
        await client.json("/state")
        fail[0] = True
        with pytest.raises(RiderError):
            if failed_lane == "json":
                await client.json("/state")
            else:
                await anext(client.mjpeg())
        assert client.lan.current is None
        client.lan.retry_at += 30
        fail[0] = False
        await asyncio.wait_for(
            client.json(
                "/api/safety", method="POST", body={"action": "stop"}, stop=True
            ),
            0.2,
        )
        assert calls[-2].url.path == IDENTITY_PATH and calls[-2].method == "GET"
        assert calls[-1].url.path == "/api/safety" and calls[-1].method == "POST"
        assert len([r for r in calls if r.method == "POST"]) == 1
    finally:
        await client.close()


async def test_stop_identity_check_uses_reserved_pool_even_when_json_pool_is_busy(
    monkeypatch,
):
    calls = []

    def handle(request):
        calls.append(request)
        return (
            httpx.Response(200, json=identity())
            if request.url.path == IDENTITY_PATH
            else response(request.url.path)
        )

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle))
    try:
        await client.json("/state")

        def blocked(*args, **kwargs):
            pytest.fail("STOP preflight occupied general JSON pool")

        monkeypatch.setattr(client.lan.current.http, "stream", blocked)
        await client.json(
            "/api/safety", method="POST", body={"action": "stop"}, stop=True
        )
        assert [r.url.path for r in calls[-2:]] == [IDENTITY_PATH, "/api/safety"]
    finally:
        await client.close()


async def test_repeated_cancellation_finishes_all_owned_generation_pool_cleanup():
    closing, release = asyncio.Event(), asyncio.Event()
    completed = []

    class SlowClose(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return (
                httpx.Response(200, json=identity())
                if request.url.path == IDENTITY_PATH
                else response(request.url.path)
            )

        async def aclose(self):
            closing.set()
            await release.wait()
            completed.append(True)

    client = PiClient(DEVICE, transport=SlowClose())
    await client.json("/state")
    previous = client.lan.current
    task = asyncio.create_task(client.reconnect())
    await closing.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(completed) == 3
    assert all(c.is_closed for c in [previous.http, previous.stop, previous.media])
    await client.close()


@pytest.mark.parametrize(
    "error_class",
    [
        httpx.PoolTimeout,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.ConnectError,
        httpx.ReadError,
    ],
)
async def test_first_lan_json_failure_logs_exact_class_per_generation_without_secrets(
    caplog, error_class
):
    failing, count = [False], [0]
    release = asyncio.Event()

    async def handle(request):
        if request.url.path == IDENTITY_PATH:
            return httpx.Response(200, json=identity())
        if failing[0]:
            count[0] += 1
            if count[0] == 2:
                release.set()
            await release.wait()
            raise error_class("Bearer private-body-secret", request=request)
        return response(request.url.path)

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle))
    try:
        await client.json("/healthz")
        failing[0] = True
        with caplog.at_level("WARNING", logger="venom_module_rider.client"):
            results = await asyncio.gather(
                client.json("/state?access_token=private-query-secret"),
                client.json("/healthz?token=private-query-secret"),
                return_exceptions=True,
            )
        assert all(isinstance(row, RiderError) for row in results)
        records = [
            r.message for r in caplog.records if "Rider LAN JSON failure" in r.message
        ]
        assert len(records) == 1
        assert "generation=1 method=GET path=/" in records[0]
        assert "exception=" + error_class.__name__ in records[0]
        assert all(
            value not in records[0]
            for value in ["secret", "Bearer", "access_token", "?", "rider.test"]
        )

        failing[0], client.lan.retry_at = False, 0
        await client.json("/healthz")
        failing[0] = True
        with caplog.at_level("WARNING", logger="venom_module_rider.client"):
            with pytest.raises(RiderError):
                await client.json("/state?token=private-query-secret")
        records = [
            r.message for r in caplog.records if "Rider LAN JSON failure" in r.message
        ]
        assert len(records) == 2 and "generation=2 " in records[-1]
    finally:
        release.set()
        await client.close()
