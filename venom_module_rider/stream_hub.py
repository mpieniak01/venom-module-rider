"""One demand-driven native reader and bounded recent identities per selected Pi."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, field, replace

from .age_anchor import SourceAgeAnchor, preferred_anchor
from .client import finish_native_cleanup
from .errors import RiderError
from .stream_contract import NativeFrame, SourceReference, canonical_jpeg

MAX_FRAME_AGE_MS = 500
MAX_REFERENCES = 8
MAX_READERS = 8
logger = logging.getLogger(__name__)


@dataclass
class StreamConnection:
    binding: tuple
    client: object
    domain: object
    anchor: SourceAgeAnchor | None = None
    readers: int = 0
    last_used: float = 0.0
    hold_until: float = 0.0
    frames: OrderedDict[tuple, NativeFrame] = field(default_factory=OrderedDict)
    canonical: OrderedDict[tuple, asyncio.Task] = field(default_factory=OrderedDict)
    canonical_jobs: dict[tuple, asyncio.Task] = field(default_factory=dict)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    reader: asyncio.Task | None = None
    reaper: asyncio.Task | None = None
    sequence: int = -1
    error: str | None = None
    received: int = 0
    rejected: int = 0
    reconnects: int = 0
    closed: bool = False


class NativeStreamHub:
    def __init__(self, *, clock=time.monotonic, idle_s=2.0):
        self.clock, self.idle_s = clock, idle_s
        self.connection: StreamConnection | None = None
        self.lock = asyncio.Lock()
        self.closed = False

    @staticmethod
    def binding(domain, pipeline):
        session = pipeline.get("camera_session")
        if (
            pipeline.get("contract_id") != "rider.camera_pipeline"
            or pipeline.get("contract_version") != 1
            or pipeline.get("state") not in {"active", "waiting_frame"}
            or not isinstance(session, str)
            or not session
        ):
            raise RiderError("stream_unavailable", 503)
        client = domain.transport()
        return (
            client.device.id,
            domain.session,
            pipeline["transport_epoch"],
            session,
            pipeline["revision"],
        )

    @asynccontextmanager
    async def lease(self, domain, pipeline, *, keepalive=False):
        binding = self.binding(domain, pipeline)
        async with self.lock:
            if self.closed:
                raise RiderError("module_closed", 503)
            connection = self.connection
            if connection is not None and connection.binding != binding:
                await self._retire(connection)
                connection = None
            if connection is None or connection.closed:
                connection = StreamConnection(
                    binding=binding,
                    client=domain.transport(),
                    domain=domain,
                    anchor=pipeline.get("_age_anchor"),
                    last_used=self.clock(),
                )
                self.connection = connection
                connection.reader = asyncio.create_task(self._read(connection))
                connection.reaper = asyncio.create_task(self._reap(connection))
            if connection.readers >= MAX_READERS:
                raise RiderError("stream_busy", 429)
            self.update_anchor(pipeline)
            connection.readers += 1
            connection.last_used = self.clock()
        try:
            yield connection
        finally:
            connection.readers -= 1
            connection.last_used = self.clock()
            if keepalive:
                connection.hold_until = self.clock() + self.idle_s
            # A browser closes its read immediately. Reference requests retain
            # only a short bounded grace interval, never an infinite collector.
            if (
                not keepalive
                and connection.readers == 0
                and connection.hold_until <= self.clock()
            ):
                async with self.lock:
                    if connection.readers == 0 and self.connection is connection:
                        await self._retire(connection)

    def _notify(self, connection):
        changed, connection.changed = connection.changed, asyncio.Event()
        changed.set()

    def _purge(self, connection):
        now = self.clock()
        for identity, frame in tuple(connection.frames.items()):
            if frame.age_ms(now) >= MAX_FRAME_AGE_MS:
                del connection.frames[identity]
                connection.canonical.pop(identity, None)
        while len(connection.frames) > MAX_REFERENCES:
            identity, _frame = connection.frames.popitem(last=False)
            connection.canonical.pop(identity, None)

    def update_anchor(self, pipeline):
        connection, anchor = self.connection, pipeline.get("_age_anchor")
        if (
            connection is None
            or not isinstance(anchor, SourceAgeAnchor)
            or anchor.authority() != connection.binding[2:]
            or (
                connection.anchor is not None
                and anchor.request_started_mono
                <= connection.anchor.request_started_mono
            )
        ):
            return
        anchor = preferred_anchor(connection.anchor, anchor, self.clock())
        if anchor is connection.anchor:
            return
        connection.anchor = anchor
        for identity, frame in tuple(connection.frames.items()):
            age = anchor.upper_age_ms(frame.source, frame.received_mono)
            if age is not None:
                connection.frames[identity] = replace(
                    frame, age_floor_ms=max(frame.age_floor_ms, age)
                )
        self._purge(connection)
        self._notify(connection)

    def _admit(self, connection, frame):
        source = frame.source
        expected = connection.binding
        if (
            source.transport_epoch != expected[2]
            or source.camera_session != expected[3]
            or source.pipeline_revision != expected[4]
        ):
            raise RiderError("stream_epoch_changed", 409)
        frame.validate_payload()
        if connection.anchor is None:
            raise RiderError("stream_age_unavailable", 503)
        age = connection.anchor.upper_age_ms(source, frame.received_mono)
        if age is None:
            raise RiderError("stream_epoch_changed", 409)
        frame = replace(frame, age_floor_ms=max(frame.age_floor_ms, age))
        if source.sequence <= connection.sequence:
            connection.rejected += 1
            return False
        if frame.age_ms(self.clock()) >= MAX_FRAME_AGE_MS:
            connection.rejected += 1
            return False
        connection.sequence = source.sequence
        connection.frames[source.identity()] = frame
        connection.received += 1
        connection.error = None
        self._purge(connection)
        self._notify(connection)
        return True

    async def _read(self, connection):
        backoff = 0.1
        while not self.closed and not connection.closed:
            try:
                # async-for alone does not close its suspended async generator
                # when bootstrap/admission fails or its consumer is cancelled.
                # Release the one HTTP media slot before any next reader starts.
                async with aclosing(connection.client.mjpeg()) as incoming:
                    async for frame in incoming:
                        if connection.closed:
                            return
                        if connection.anchor is None:
                            # Only bootstrap once when start status preceded the
                            # first capture. Normal updates reuse profile GETs;
                            # there is no per-frame status poll.
                            pipeline = await connection.domain.pipeline(force=True)
                            self.update_anchor(pipeline)
                        if self._admit(connection, frame):
                            backoff = 0.1
                connection.error = "stream_disconnected"
            except asyncio.CancelledError:
                raise
            except RiderError as exc:
                logger.warning(
                    "Native ingest failed: %s status=%s cause=%s",
                    exc.code,
                    exc.status,
                    type(exc.__cause__).__name__ if exc.__cause__ else "none",
                )
                connection.error = exc.code
                connection.rejected += 1
                if exc.code == "stream_epoch_changed":
                    connection.frames.clear()
                    self._notify(connection)
                    return
            except Exception as exc:
                # An optional reader failure cannot leak stale data as ready.
                logger.warning(
                    "Native ingest failed: unexpected %s", type(exc).__name__
                )
                connection.error = "stream_unavailable"
            connection.frames.clear()
            self._notify(connection)
            if connection.closed:
                return
            connection.reconnects += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 2.0)

    async def _reap(self, connection):
        try:
            while not connection.closed:
                await asyncio.sleep(min(self.idle_s, 0.25))
                if (
                    connection.readers == 0
                    and self.clock() - connection.last_used >= self.idle_s
                ):
                    async with self.lock:
                        if connection.readers == 0:
                            await self._retire(connection)
                            return
        except asyncio.CancelledError:
            raise

    async def frame(
        self,
        connection,
        *,
        source: SourceReference | None = None,
        after=None,
        timeout=0.5,
    ):
        deadline = asyncio.get_running_loop().time() + timeout
        while not self.closed and not connection.closed:
            changed = connection.changed
            self._purge(connection)
            if source is not None:
                frame = connection.frames.get(source.identity())
            else:
                frame = next(reversed(connection.frames.values()), None)
                if frame is not None and frame.source.identity() == after:
                    frame = None
            if frame is not None:
                return frame
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RiderError(connection.error or "stream_frame_unavailable", 409)
            try:
                await asyncio.wait_for(changed.wait(), remaining)
            except TimeoutError as exc:
                raise RiderError(
                    connection.error or "stream_frame_unavailable", 409
                ) from exc
        raise RiderError("stream_unavailable", 503)

    async def canonical_frame(self, connection, frame):
        if connection.closed:
            raise RiderError("stream_unavailable", 503)
        identity = frame.source.identity()
        task = connection.canonical.get(identity) or connection.canonical_jobs.get(
            identity
        )
        if task is None:
            # Cache expiry must not hide an unfinished thread from admission or
            # teardown. Refuse additional work instead of queueing more sources.
            if len(connection.canonical_jobs) >= MAX_REFERENCES:
                raise RiderError("stream_busy", 429)
            task = asyncio.create_task(asyncio.to_thread(canonical_jpeg, frame))
            connection.canonical_jobs[identity] = task
            connection.canonical[identity] = task

            def completed(done):
                if connection.canonical_jobs.get(identity) is done:
                    del connection.canonical_jobs[identity]
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(completed)
            while len(connection.canonical) > MAX_REFERENCES:
                connection.canonical.popitem(last=False)
        return await asyncio.shield(task)

    def status(self, connection):
        self._purge(connection)
        frame = next(reversed(connection.frames.values()), None)
        return {
            "ready": frame is not None and not connection.closed,
            "latest_source": frame.source.model_dump(mode="json") if frame else None,
            "frame_age_ms": round(frame.age_ms(self.clock()), 2) if frame else None,
            "received": connection.received,
            "rejected": connection.rejected,
            "reconnects": connection.reconnects,
            "readers": connection.readers,
            "buffered_frames": len(connection.frames),
            "max_buffered_frames": MAX_REFERENCES,
            "error": connection.error,
        }

    async def _retire(self, connection):
        connection.closed = True
        connection.frames.clear()
        canonical = set(connection.canonical.values()) | set(
            connection.canonical_jobs.values()
        )
        connection.canonical.clear()
        self._notify(connection)
        tasks = [
            task
            for task in (connection.reader, connection.reaper)
            if task is not None and task is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        try:
            if tasks or canonical:
                # A cancelled profile/receiver request must not send another
                # cancellation into a reader already releasing its HTTP slot,
                # nor abandon canonical threads still owned by this generation.
                await finish_native_cleanup(
                    asyncio.gather(*tasks, *canonical, return_exceptions=True)
                )
        finally:
            if self.connection is connection:
                self.connection = None

    async def invalidate(self):
        async with self.lock:
            if self.connection is not None:
                await self._retire(self.connection)

    async def close(self):
        self.closed = True
        await self.invalidate()
