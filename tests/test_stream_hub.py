"""Shared native ingestion is bounded and independent of a browser's lifetime."""

import asyncio
from types import SimpleNamespace

import pytest
from test_stream_contract import native

from venom_module_rider.age_anchor import age_anchor
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
    now = hub.clock()
    profile["latest_frame"] = native(now=now).source.model_dump(mode="json")
    profile["_age_anchor"] = age_anchor(profile, now, now)
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
            observed = await a
            assert await b is observed
            assert observed.source == incoming.source
            assert observed.payload == incoming.payload
            assert client.opened == 1
            assert first.readers == 2
        assert not first.closed
        assert first.readers == 1
        await emit(client, native(2))
        assert (await hub.frame(first)).source.sequence == 2
    assert first.closed
    assert not first.frames
    assert hub.connection is None
    assert client.closed == 1
    await hub.close()


async def test_reference_warmup_expires_without_an_eternal_background_collector():
    client, domain, profile, hub = setup(idle_s=0.02)
    async with hub.lease(domain, profile, keepalive=True) as connection:
        await emit(client, native())
        assert (await hub.frame(connection)).source.sequence == 1
    assert not connection.closed
    assert connection.readers == 0
    await asyncio.sleep(0.06)
    assert connection.closed
    assert hub.connection is None
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
        assert connection.received == received
        assert connection.rejected == 2
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
        assert (await hub.frame(connection)).source == one.source
        await emit(client, RiderError("stream_disconnected", 503))
        assert not connection.frames
        await asyncio.sleep(0.12)
        await emit(client, one, native(2))
        assert (await hub.frame(connection)).source.sequence == 2
        assert client.opened == 2
        assert connection.reconnects == 1
        assert connection.rejected >= 1
    await hub.close()


async def test_old_epoch_is_rejected_and_new_profile_retires_all_old_consumers():
    client, domain, profile, hub = setup()
    async with hub.lease(domain, profile) as old:
        await emit(client, native())
        assert (await hub.frame(old)).source.sequence == 1
        waiting = asyncio.create_task(hub.frame(old, after=native().source.identity()))
        new_profile = {**profile, "transport_epoch": 3, "camera_session": "new-session"}
        new_profile["latest_frame"] = native(
            session="new-session", epoch=3
        ).source.model_dump(mode="json")
        new_profile["_age_anchor"] = age_anchor(new_profile, hub.clock(), hub.clock())
        async with hub.lease(domain, new_profile) as current:
            assert old.closed
            assert not old.frames
            with pytest.raises(RiderError, match="stream_unavailable"):
                await waiting
            await emit(client, native(2))  # Late packet from the retired producer.
            assert current.error == "stream_epoch_changed"
            assert not current.frames
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
        assert a == b == b"canonical-image"
        assert len(calls) == 1
        await hub.close()
        assert connection.closed
        assert not connection.canonical


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
        assert not connection.frames
        assert hub.status(connection)["ready"] is False
    assert reader.done()
    assert reaper.done()
    count = len(attempts)
    await asyncio.sleep(0.12)
    assert len(attempts) == count
    await hub.close()


async def test_slow_canonical_jobs_remain_bounded_across_expiry_cancel_and_retire(
    monkeypatch,
):
    import threading

    from test_stream_contract import jpeg

    from venom_module_rider import stream_hub

    now = [10.0]
    client, domain, profile, hub = setup(clock=lambda: now[0])
    gate, entered = threading.Event(), threading.Event()
    decoded, lock = [], threading.Lock()

    def slow_decode(frame):
        with lock:
            decoded.append(frame.source.sequence)
        entered.set()
        if not gate.wait(2):
            raise RuntimeError("test_decoder_gate_not_released")
        return jpeg()

    monkeypatch.setattr(stream_hub, "canonical_jpeg", slow_decode)
    callers, shared, retiring = [], None, None
    try:
        async with hub.lease(domain, profile) as connection:
            first = None
            for sequence in range(1, MAX_REFERENCES + 1):
                frame = native(sequence, now=now[0])
                first = first or frame
                assert hub._admit(connection, frame)
                callers.append(
                    asyncio.create_task(hub.canonical_frame(connection, frame))
                )
                await asyncio.sleep(0)
            assert await asyncio.to_thread(entered.wait, 1)
            assert len(connection.canonical_jobs) == MAX_REFERENCES

            # Request cancellation does not cancel shared to_thread work.
            for task in callers:
                task.cancel()
            await asyncio.gather(*callers, return_exceptions=True)
            assert len(connection.canonical_jobs) == MAX_REFERENCES
            now[0] += 0.6
            hub._purge(connection)
            assert not connection.frames
            assert not connection.canonical

            # TTL/LRU churn cannot free admission slots for unfinished work.
            for sequence in range(MAX_REFERENCES + 1, MAX_REFERENCES * 3 + 1):
                frame = native(sequence, now=now[0])
                assert hub._admit(connection, frame)
                with pytest.raises(RiderError, match="stream_busy"):
                    await hub.canonical_frame(connection, frame)
                now[0] += 0.05
            assert len(connection.frames) <= MAX_REFERENCES
            assert len(connection.canonical_jobs) == MAX_REFERENCES

            # Even after its cache entry expires, the original source still
            # shares its existing job instead of creating a ninth decoder.
            shared = asyncio.create_task(hub.canonical_frame(connection, first))
            await asyncio.sleep(0)
            assert not shared.done()
            assert len(connection.canonical_jobs) == MAX_REFERENCES
            changed = connection.changed
            retiring = asyncio.create_task(hub.invalidate())
            await asyncio.wait_for(changed.wait(), 1)
            assert connection.closed
            assert not retiring.done()
            with pytest.raises(RiderError, match="stream_unavailable"):
                await hub.canonical_frame(connection, frame)

            gate.set()
            await asyncio.wait_for(retiring, 1)
            assert await asyncio.wait_for(shared, 1) == jpeg()
            assert not connection.canonical_jobs
            assert not connection.canonical
            assert hub.connection is None
            assert client.closed == client.opened
            assert sorted(decoded) == list(range(1, MAX_REFERENCES + 1))
    finally:
        gate.set()
        await asyncio.gather(
            *callers,
            *([shared] if shared else []),
            *([retiring] if retiring else []),
            return_exceptions=True,
        )
        await hub.close()


async def test_missing_past_reference_is_rejected_without_waiting_for_new_frames():
    client, domain, profile, hub = setup()
    try:
        async with hub.lease(domain, profile) as connection:
            await emit(client, native())
            current = await hub.frame(connection)
            altered = current.source.reference().model_copy(update={"sha256": "b" * 64})
            with pytest.raises(RiderError, match="stream_frame_unavailable"):
                await asyncio.wait_for(
                    hub.frame(connection, source=altered, timeout=1), 0.05
                )
    finally:
        await hub.close()


async def test_canonical_frame_uses_its_own_lane_not_the_global_queue(monkeypatch):
    client, domain, profile, hub = setup()
    try:
        async with hub.lease(domain, profile) as connection:
            await emit(client, native())
            current = await hub.frame(connection)

            async def shared_queue(*_args, **_kwargs):
                raise AssertionError("shared application queue is busy")

            monkeypatch.setattr(asyncio, "to_thread", shared_queue)
            assert await hub.canonical_frame(connection, current) == current.payload
    finally:
        await hub.close()


async def test_accepted_frame_resets_backoff_even_when_its_iterator_then_fails(
    monkeypatch,
):
    """A delivered frame resets reconnect delay before its peer iterator can fail."""
    now = 10.0
    client, domain, profile, hub = setup(clock=lambda: now)
    attempts = 0
    delays = []
    third_sleep = asyncio.Event()
    blocked = asyncio.Event()
    real_sleep = asyncio.sleep
    connection = None

    async def reconnecting():
        """Fail twice, admit a real fixture frame, then fail before normal return."""
        nonlocal attempts
        attempts += 1
        if attempts == 3:
            yield native(now=now)
        raise RiderError("stream_disconnected", 503)

    async def tracked_sleep(delay):
        """Observe only this hub reader and pause before an unwanted fourth retry."""
        if connection is not None and asyncio.current_task() is connection.reader:
            delays.append(delay)
            if len(delays) == 3:
                third_sleep.set()
                await blocked.wait()
            else:
                await real_sleep(0)
        else:
            await real_sleep(delay)

    monkeypatch.setattr(client, "mjpeg", reconnecting)
    monkeypatch.setattr("venom_module_rider.stream_hub.asyncio.sleep", tracked_sleep)
    try:
        async with hub.lease(domain, profile) as connection:
            await asyncio.wait_for(third_sleep.wait(), 1)
            assert attempts == 3
            assert connection.received == 1
            assert connection.sequence == 1
            assert connection.reconnects == 3
            assert delays == pytest.approx([0.1, 0.2, 0.1])
    finally:
        await hub.close()
