"""Shared native ingestion is bounded and independent of a browser's lifetime."""

import asyncio
from types import SimpleNamespace

import pytest
from test_stream_contract import native

from venom_module_rider.errors import RiderError
from venom_module_rider.stream_hub import MAX_REFERENCES, NativeStreamHub


class Client:
    def __init__(self):
        self.device = SimpleNamespace(id="pi")
        self.queue = asyncio.Queue()
        self.opened = self.closed = 0

    async def mjpeg(self):
        self.opened += 1
        try:
            while True:
                item = await self.queue.get()
                if isinstance(item, Exception):
                    raise item
                if item is None:
                    return
                yield item
        finally:
            self.closed += 1


def setup(*, clock=None, idle_s=2.0):
    client = Client()
    domain = SimpleNamespace(
        session="module-session", epoch=0, transport=lambda: client
    )
    profile = {
        "contract_id": "rider.camera_pipeline",
        "contract_version": 1,
        "state": "active",
        "camera_session": "session",
        "transport_epoch": 2,
        "revision": 3,
    }
    hub = NativeStreamHub(**({"clock": clock} if clock else {}), idle_s=idle_s)
    return client, domain, profile, hub


async def emit(client, *frames):
    for frame in frames:
        await client.queue.put(frame)
    for _ in range(3):
        await asyncio.sleep(0)


async def test_two_panels_share_one_camera_reader_and_cancel_only_their_lease():
    client, domain, profile, hub = setup()
    async with hub.lease(domain, profile) as first:
        async with hub.lease(domain, profile) as second:
            assert first is second
            a = asyncio.create_task(hub.frame(first))
            b = asyncio.create_task(hub.frame(second))
            incoming = native()
            await emit(client, incoming)
            assert await a is incoming
            assert await b is incoming
            assert client.opened == 1 and first.readers == 2
        assert not first.closed and first.readers == 1
        await emit(client, native(2))
        assert (await hub.frame(first)).source.sequence == 2
    assert first.closed and not first.frames and hub.connection is None
    assert client.closed == 1
    await hub.close()


async def test_reference_warmup_expires_without_an_eternal_background_collector():
    client, domain, profile, hub = setup(idle_s=0.02)
    async with hub.lease(domain, profile, keepalive=True) as connection:
        await emit(client, native())
        assert (await hub.frame(connection)).source.sequence == 1
    assert not connection.closed and connection.readers == 0
    await asyncio.sleep(0.06)
    assert connection.closed and hub.connection is None
    assert client.closed == client.opened == 1
    await hub.close()


async def test_recent_reference_ring_has_no_backlog_and_duplicates_do_not_rejuvenate():
    now = [10.0]
    client, domain, profile, hub = setup(clock=lambda: now[0])
    async with hub.lease(domain, profile) as connection:
        frames = [native(i, now=now[0]) for i in range(1, 25)]
        await emit(client, *frames)
        latest = await hub.frame(connection)
        assert latest.source.sequence == 24
        assert len(connection.frames) == MAX_REFERENCES
        with pytest.raises(RiderError, match="stream_frame_unavailable"):
            await hub.frame(
                connection, source=frames[0].source.reference(), timeout=0.001
            )
        received = connection.received
        await emit(client, frames[-1], frames[-2])
        assert connection.received == received and connection.rejected == 2
        now[0] += 0.5
        assert hub.status(connection)["ready"] is False
        assert not connection.frames
        with pytest.raises(RiderError, match="stream_frame_unavailable"):
            await hub.frame(connection, source=latest.source.reference(), timeout=0.001)
    await hub.close()


async def test_reconnect_discards_old_input_and_does_not_accept_replayed_sequence():
    client, domain, profile, hub = setup()
    async with hub.lease(domain, profile) as connection:
        one = native()
        await emit(client, one)
        assert await hub.frame(connection) is one
        await emit(client, RiderError("stream_disconnected", 503))
        assert not connection.frames
        await asyncio.sleep(0.12)
        await emit(client, one, native(2))
        assert (await hub.frame(connection)).source.sequence == 2
        assert client.opened == 2 and connection.reconnects == 1
        assert connection.rejected >= 1
    await hub.close()


async def test_old_epoch_is_rejected_and_new_profile_retires_all_old_consumers():
    client, domain, profile, hub = setup()
    async with hub.lease(domain, profile) as old:
        await emit(client, native())
        assert (await hub.frame(old)).source.sequence == 1
        waiting = asyncio.create_task(hub.frame(old, after=native().source.identity()))
        new_profile = {**profile, "transport_epoch": 3, "camera_session": "new-session"}
        async with hub.lease(domain, new_profile) as current:
            assert old.closed and not old.frames
            with pytest.raises(RiderError, match="stream_unavailable"):
                await waiting
            await emit(client, native(2))  # Late packet from the retired producer.
            assert current.error == "stream_epoch_changed" and not current.frames
            assert hub.status(current)["ready"] is False
    await hub.close()


async def test_invalid_frames_failed_workers_and_reader_limit_are_bounded(monkeypatch):
    client, domain, profile, hub = setup()
    async with hub.lease(domain, profile) as connection:
        await emit(client, native())
        current = await hub.frame(connection)
        await emit(client, RuntimeError("unexpected peer failure"))
        assert connection.error == "stream_unavailable"
        assert not connection.frames
        await asyncio.sleep(0.12)
        await emit(client, None)
        await asyncio.sleep(0)
        assert connection.reconnects == 2
        connection.readers = 8
        with pytest.raises(RiderError, match="stream_busy"):
            async with hub.lease(domain, profile):
                pytest.fail("too many consumers admitted")
        connection.readers = 1
        invalid = native(2)
        object.__setattr__(invalid, "payload", b"tampered")
        with pytest.raises(RiderError, match="stream_hash_mismatch"):
            hub._admit(connection, invalid)
        stale = native(3, now=hub.clock() - 1)
        hub._admit(connection, stale)
        assert connection.rejected >= 1
        assert current.source.sequence == 1
    await hub.close()
    with pytest.raises(RiderError, match="module_closed"):
        async with hub.lease(domain, profile):
            pytest.fail("closed module admitted")


async def test_canonical_image_transform_is_shared_between_browsers(monkeypatch):
    client, domain, profile, hub = setup()
    calls = []

    def canonical(frame):
        calls.append(frame.source.identity())
        return b"canonical-image"

    monkeypatch.setattr("venom_module_rider.stream_hub.canonical_jpeg", canonical)
    async with hub.lease(domain, profile) as connection:
        await emit(client, native())
        frame = await hub.frame(connection)
        a, b = await asyncio.gather(
            hub.canonical_frame(connection, frame),
            hub.canonical_frame(connection, frame),
        )
        assert a == b == b"canonical-image" and len(calls) == 1
        await hub.close()
        assert connection.closed and not connection.canonical


@pytest.mark.parametrize(
    "changes",
    [
        {"contract_id": "other"},
        {"contract_version": 2},
        {"state": "stopped"},
        {"camera_session": None},
    ],
)
def test_pipeline_identity_is_required_before_a_reader_is_opened(changes):
    client, domain, profile, hub = setup()
    with pytest.raises(RiderError, match="stream_unavailable"):
        hub.binding(domain, {**profile, **changes})
    assert client.opened == 0


async def test_failed_peer_reconnect_backoff_is_bounded_and_cancellable(monkeypatch):
    client, domain, profile, hub = setup()
    attempts = []

    async def failed():
        attempts.append(asyncio.get_running_loop().time())
        raise RiderError("stream_unavailable", 503)
        yield  # Async-generator protocol, no synthetic heartbeat.

    monkeypatch.setattr(client, "mjpeg", failed)
    async with hub.lease(domain, profile) as connection:
        await asyncio.sleep(0.36)
        assert 2 <= len(attempts) <= 3
        assert attempts[1] - attempts[0] >= 0.09
        if len(attempts) == 3:
            assert attempts[2] - attempts[1] >= 0.18
        reader, reaper = connection.reader, connection.reaper
        assert not connection.frames and hub.status(connection)["ready"] is False
    assert reader.done() and reaper.done()
    count = len(attempts)
    await asyncio.sleep(0.12)
    assert len(attempts) == count
    await hub.close()
