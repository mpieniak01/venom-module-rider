"""A failed JSON lane cannot tear down demonstrably fresh independent media."""

import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest
from test_lan_transport import DEVICE, PEER, identity, response
from test_stream_client import wire
from test_stream_contract import native

from venom_module_rider.age_anchor import age_anchor
from venom_module_rider.client import PiClient
from venom_module_rider.errors import RiderError
from venom_module_rider.lan_transport import IDENTITY_PATH
from venom_module_rider.stream_hub import NativeStreamHub


@pytest.fixture
async def live_media():
    queue, calls, invalidations = asyncio.Queue(), [], []
    failure, peer = [None], [PEER]
    stream_closed = asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                item = await queue.get()
                if item is None:
                    return
                if isinstance(item, Exception):
                    raise item
                yield wire(
                    item.payload, item.source.model_dump(mode="json")
                ).removesuffix(b"--rider--\r\n")

        async def aclose(self):
            stream_closed.set()
            await queue.put(None)

    def handle(request):
        calls.append(request)
        if request.url.path == IDENTITY_PATH:
            return httpx.Response(200, json=identity(peer_id=peer[0]))
        if request.url.path == "/api/resource/camera/stream.mjpg":
            return httpx.Response(
                200,
                headers={"content-type": "multipart/x-mixed-replace; boundary=rider"},
                stream=Stream(),
            )
        if failure[0] is not None:
            raise failure[0]("isolated fixture failure", request=request)
        return response(request.url.path)

    client = PiClient(DEVICE, transport=httpx.MockTransport(handle))
    client.transport_invalidated = lambda *args: invalidations.append(args)
    hub = NativeStreamHub()
    domain = SimpleNamespace(session="panel", transport=lambda: client)
    first = native()
    profile = {
        "contract_id": "rider.camera_pipeline",
        "contract_version": 1,
        "state": "active",
        "camera_session": first.source.camera_session,
        "transport_epoch": first.source.transport_epoch,
        "revision": first.source.pipeline_revision,
        "latest_frame": first.source.model_dump(mode="json"),
    }
    now = time.monotonic()
    profile["_age_anchor"] = age_anchor(profile, now, now)
    fixture = SimpleNamespace(
        client=client,
        hub=hub,
        domain=domain,
        profile=profile,
        first=first,
        queue=queue,
        calls=calls,
        failure=failure,
        peer=peer,
        invalidations=invalidations,
        stream_closed=stream_closed,
    )
    try:
        yield fixture
    finally:
        await hub.close()
        await client.close()


async def admit(fixture, connection, frame=None):
    frame = frame or fixture.first
    await fixture.queue.put(frame)
    return await fixture.hub.frame(
        connection,
        after=None if frame.source.sequence == 1 else fixture.first.source.identity(),
    )


@pytest.mark.parametrize(
    "error", [httpx.PoolTimeout, httpx.ConnectTimeout, httpx.ReadTimeout]
)
async def test_passive_timeout_preserves_fresh_verified_stream_without_retry(
    live_media, error
):
    f = live_media
    async with f.hub.lease(f.domain, f.profile) as connection:
        await admit(f, connection)
        generation = f.client.lan.current
        f.failure[0] = error
        with pytest.raises(RiderError, match="pi_connection_timeout"):
            await f.client.json("/state")
        assert f.client.lan.current is generation
        assert not any(
            pool.is_closed
            for pool in (generation.http, generation.stop, generation.media)
        )
        assert not f.invalidations and not f.stream_closed.is_set()
        assert (
            f.client.lan.retry_at == 0
            and f.client.transport_status()["state"] == "online"
        )
        assert len([r for r in f.calls if r.url.path == "/state"]) == 1
        failed = next(r for r in f.calls if r.url.path == "/state")
        assert failed.extensions["timeout"]["connect"] == 0.5
        next_frame = await admit(f, connection, native(2))
        assert next_frame.source.sequence == 2 and connection.reconnects == 0
        f.failure[0] = None
        await f.client.json("/healthz")
        assert f.client.lan.number == 1
    assert f.client._media_valid_until == 0 and f.stream_closed.is_set()


async def test_raw_frames_without_hub_age_and_authority_admission_are_not_evidence(
    live_media,
):
    f = live_media
    source = f.client.mjpeg()
    await f.queue.put(f.first)
    try:
        await anext(source)
        previous = f.client.lan.current
        assert f.client._media_valid_until == 0
        f.failure[0] = httpx.ConnectTimeout
        with pytest.raises(RiderError, match="pi_connection_timeout"):
            await f.client.json("/state")
        assert f.client.lan.current is None and len(f.invalidations) == 1
        assert all(
            pool.is_closed for pool in (previous.http, previous.stop, previous.media)
        )
    finally:
        await source.aclose()


async def test_age_derived_evidence_expires_without_any_further_hub_poll(live_media):
    f = live_media
    # Preserve a conservative slow-bootstrap bound: only ~70 ms of the original
    # 500 ms admission budget remains, despite bytes having just arrived.
    now = time.monotonic()
    f.profile["_age_anchor"] = age_anchor(f.profile, now - 0.43, now)
    async with f.hub.lease(f.domain, f.profile) as connection:
        frame = await admit(f, connection)
        assert frame.age_floor_ms >= 430
        assert 0 < f.client._media_valid_until - time.monotonic() < 0.075
        await asyncio.sleep(0.09)
        f.failure[0] = httpx.ReadTimeout
        with pytest.raises(RiderError, match="pi_connection_timeout"):
            await f.client.json("/state")
        assert f.client.lan.current is None and len(f.invalidations) == 1


async def test_replayed_sequence_cannot_renew_media_health(live_media):
    f = live_media
    async with f.hub.lease(f.domain, f.profile) as connection:
        await admit(f, connection)
        deadline = f.client._media_valid_until
        await f.queue.put(f.first)
        for _ in range(6):
            await asyncio.sleep(0)
        assert connection.received == 1 and connection.rejected == 1
        assert f.client._media_valid_until <= deadline


@pytest.mark.parametrize(
    "error", [httpx.ConnectError, httpx.ReadError, httpx.WriteTimeout]
)
async def test_other_network_failures_retire_even_with_fresh_media(live_media, error):
    f = live_media
    async with f.hub.lease(f.domain, f.profile) as connection:
        await admit(f, connection)
        f.failure[0] = error
        with pytest.raises(RiderError):
            await f.client.json("/state")
        assert f.client.lan.current is None and len(f.invalidations) == 1


@pytest.mark.parametrize("stop", [False, True])
async def test_mutation_timeout_still_unknown_never_replayed_and_stop_can_revalidate(
    live_media, stop
):
    f = live_media
    async with f.hub.lease(f.domain, f.profile) as connection:
        await admit(f, connection)
        f.failure[0] = httpx.ConnectTimeout
        path = "/api/safety" if stop else "/api/control"
        with pytest.raises(RiderError, match="pi_operation_timeout") as caught:
            await f.client.json(path, method="POST", body={"action": "stop"}, stop=stop)
        assert caught.value.details == {"outcome": "unknown"}
        assert f.client.lan.current is None and len(f.invalidations) == 1
        assert len([r for r in f.calls if r.method == "POST"]) == 1
        f.client.lan.retry_at += 30
        f.failure[0] = None
        await asyncio.wait_for(
            f.client.json(
                "/api/safety", method="POST", body={"action": "stop"}, stop=True
            ),
            0.2,
        )
        assert [r.url.path for r in f.calls[-2:]] == [IDENTITY_PATH, "/api/safety"]


async def test_peer_change_still_blocks_credentials_while_old_media_is_fresh(
    live_media, monkeypatch
):
    f = live_media
    monkeypatch.setenv("RIDER_AGENT_TOKEN", "fixture-token")
    async with f.hub.lease(f.domain, f.profile) as connection:
        await admit(f, connection)
        f.peer[0] = "replacement-peer"
        with pytest.raises(RiderError, match="pi_identity_mismatch"):
            await f.client.json("/api/voice/playback/cancel", method="POST", body={})
        assert f.client.lan.current is None and len(f.invalidations) == 1
        assert all(
            r.method == "GET" and "authorization" not in r.headers for r in f.calls
        )


@pytest.mark.parametrize("ending", [None, httpx.ReadTimeout("media failed")])
async def test_media_eof_or_failure_discards_proof_and_retires_generation(
    live_media, ending
):
    f = live_media
    async with f.hub.lease(f.domain, f.profile) as connection:
        await admit(f, connection)
        await f.queue.put(ending)
        await asyncio.wait_for(f.stream_closed.wait(), 0.2)
        for _ in range(8):
            await asyncio.sleep(0)
        assert f.client._media_valid_until == 0 and f.client.lan.current is None
        assert len(f.invalidations) == 1


async def test_refresh_retires_fresh_stream_generation(live_media):
    f = live_media
    async with f.hub.lease(f.domain, f.profile) as connection:
        await admit(f, connection)
        previous = f.client.lan.current
        await f.client.reconnect()
        assert f.client.lan.current is None and len(f.invalidations) == 1
        assert all(
            pool.is_closed for pool in (previous.http, previous.stop, previous.media)
        )
        # Refresh removed the old generation regardless of its prior video proof.
        assert not f.client._preserve_media_on_timeout(
            previous, httpx.ReadTimeout("old"), passive=True
        )


@pytest.mark.parametrize("invalid", ["hash", "authority"])
async def test_invalid_source_cannot_supply_transport_health(live_media, invalid):
    f = live_media
    async with f.hub.lease(f.domain, f.profile) as connection:
        await admit(f, connection)
        frame = native(2, epoch=3 if invalid == "authority" else 2)
        if invalid == "hash":
            object.__setattr__(frame, "payload", b"tampered")
        await f.queue.put(frame)
        await asyncio.wait_for(f.stream_closed.wait(), 0.2)
        assert f.client._media_valid_until == 0
        f.failure[0] = httpx.ConnectTimeout
        with pytest.raises(RiderError, match="pi_connection_timeout"):
            await f.client.json("/state")
        assert f.client.lan.current is None and len(f.invalidations) == 1
