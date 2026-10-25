"""Recording reuses the already validated native hub, not per-frame control RPCs."""

import asyncio
import time

from .errors import RiderError
from .recording_metrics import RecordingMetrics
from .sample_sources import raw
from .stream_contract import source_headers


class StudyFrameSource:
    def __init__(self, domain):
        self.domain = domain
        self.connection = None
        self.context = None
        self.task = None
        self.identity = None
        self.binding = None
        self.session = getattr(domain, "session", None)
        self.client = None
        self.error = None
        self.retry_after = 0.0
        self.metrics = RecordingMetrics()

    async def start(self):
        if self.connection is not None or time.monotonic() < self.retry_after:
            return
        self.retry_after = time.monotonic() + 1
        hub = getattr(self.domain, "media_hub", None)
        if hub is None:
            return
        with self.metrics.track("pipeline_bootstrap_ms"):
            pipeline = await self.domain.pipeline()
        if (
            not pipeline.get("supported")
            or pipeline.get("state") not in {"active", "waiting_frame"}
            or (pipeline.get("active_profile") or pipeline.get("profile") or {}).get(
                "remote_transport"
            )
            != "mjpeg"
        ):
            return
        self.client = self.domain.transport()
        self.session = self.domain.session
        self.context = hub.lease(self.domain, pipeline)
        self.connection = await self.context.__aenter__()
        self.binding = self.connection.binding
        self.task = asyncio.create_task(self._refresh())

    async def _refresh(self):
        while self.connection is not None:
            await asyncio.sleep(0.5)
            try:
                with self.metrics.track("pipeline_refresh_ms"):
                    latest = await self.domain.pipeline()
                if not latest.get("supported") or latest.get("state") not in {
                    "active",
                    "waiting_frame",
                }:
                    self.error = "stream_epoch_changed"
                    return
                if (
                    self.domain.media_hub.binding(self.domain, latest)
                    != self.connection.binding
                ):
                    self.error = "stream_epoch_changed"
                    return
            except asyncio.CancelledError:
                raise
            except RiderError as error:
                # Media admission/age checks remain authoritative for each frame.
                # A slow passive status GET must not serialize every media read.
                source_changed = (
                    self.domain.session != self.session
                    or self.domain.transport() is not self.client
                    or self.connection is None
                    or self.connection.closed
                )
                if source_changed or error.code in {
                    "stream_epoch_changed",
                    "device_not_selected",
                }:
                    self.error = "stream_epoch_changed"
                    return
                self.metrics.record("pipeline_refresh_errors", 0)

    async def key(self):
        domain, connection = self.domain, self.connection
        if (
            connection is None
            or self.error
            or domain.session != self.session
            or domain.transport() is not self.client
            or connection.closed
        ):
            raise RiderError(self.error or "stream_epoch_changed", 409)
        hub = domain.media_hub
        with self.metrics.track("native_wait_ms"):
            frame = await hub.frame(connection, after=self.identity, timeout=0.5)
        with self.metrics.track("canonical_ms"):
            payload = await hub.canonical_frame(connection, frame)
        if (
            connection.closed
            or domain.session != self.session
            or domain.transport() is not self.client
        ):
            raise RiderError("stream_epoch_changed", 409)
        if frame.age_ms(hub.clock()) >= 500:
            raise RiderError("frame_stale", 409)
        headers = source_headers(frame, hub.clock())
        with self.metrics.track("source_ticket_ms"):
            key = raw(
                domain.samples,
                self.client.device.id,
                self.session,
                payload,
                "image/jpeg",
                headers,
                mode=self.client.device.mode,
            )
        if not key:
            raise RiderError("samples_frame_unavailable", 409)
        self.identity = frame.source.identity()
        return key

    async def close(self):
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        context, self.context = self.context, None
        self.connection = None
        if context is not None:
            await context.__aexit__(None, None, None)
