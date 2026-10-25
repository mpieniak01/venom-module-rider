"""One bounded read-only sampler for explicitly active motion studies."""

from __future__ import annotations

import asyncio
import time

from .errors import RiderError
from .recording_metrics import RecordingMetrics
from .study_frame_source import StudyFrameSource

INTERVAL_S = 0.1
ERROR_BACKOFF_S = 1.0


def collecting(domain):
    registry = domain.samples
    if (
        registry is None
        or getattr(registry, "closed", False)
        or domain.device is None
        or domain.closed
    ):
        return False
    if not hasattr(registry, "evidence"):
        return False
    with registry.evidence.lock:
        return any(
            sid in registry.active
            and sid not in registry.closing
            and not state["retired"]
            and state["device"] == domain.device.id
            and str(state.get("purpose", "")).startswith("motion-study-v1:")
            for sid, state in registry.evidence.sessions.items()
        )


class StudySampler:
    """Never overlaps reads, retries commands, opens hardware or starts a camera."""

    def __init__(self, domain):
        self.domain = domain
        self.source = StudyFrameSource(domain)
        self.metrics = RecordingMetrics()
        self.task: asyncio.Task | None = None
        self.image_task: asyncio.Task | None = None
        self.image_reads = 0
        self.image_error: str | None = None
        self.reads = 0
        self.errors = 0
        self.last_error: str | None = None
        self.last_success: float | None = None

    def snapshot(self):
        return {
            "scope": "process",
            "active": self.task is not None
            and not self.task.done()
            and collecting(self.domain),
            "target_interval_ms": int(INTERVAL_S * 1000),
            "reads": self.reads,
            "image_reads": self.image_reads,
            "image_error": self.image_error,
            "image_stages_ms": self.metrics.snapshot(),
            "native_stages_ms": self.source.metrics.snapshot(),
            "video_writer_ms": self.domain.samples.store.video.metrics.snapshot()
            if getattr(self.domain, "samples", None)
            else {},
            "quota_stages_ms": self.domain.samples.store.quota.metrics.snapshot()
            if getattr(self.domain, "samples", None)
            else {},
            "errors": self.errors,
            "last_error": self.last_error,
            "last_success_age_s": None
            if self.last_success is None
            else max(0, time.monotonic() - self.last_success),
        }

    def ensure(self):
        if collecting(self.domain) and (
            self.task is None or self.task.done() or self.task.cancelling()
        ):
            self.task = asyncio.create_task(self.run())
        if (
            collecting(self.domain)
            and (
                getattr(getattr(self.domain, "samples", None), "study_images", {})
                or getattr(getattr(self.domain, "samples", None), "study_videos", {})
            )
            and (
                self.image_task is None
                or self.image_task.done()
                or self.image_task.cancelling()
            )
        ):
            self.image_task = asyncio.create_task(self.images())

    async def run(self):
        session = self.domain.session
        while session == self.domain.session and collecting(self.domain):
            started = time.monotonic()
            delay = INTERVAL_S
            try:
                # Refresh the HTTP snapshot for this recording; the ordinary
                # reader still coalesces inflight work and publishes its cache
                # for slower UI readers. This never forces a UART sensor read.
                await self.domain.read("/state", 0)
                self.reads += 1
                self.last_success = time.monotonic()
                self.last_error = None
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise
                # Connection refresh can cancel the shared transport task,
                # independently of this still-active recording task.
                self.errors += 1
                self.last_error = "study_sampling_read_cancelled"
            except Exception as exc:
                self.errors += 1
                self.last_error = (
                    exc.code if isinstance(exc, RiderError) else "study_sampling_failed"
                )
                # Priority motion invalidates an overlapping read epoch without
                # changing the device session. Do not turn that normal boundary
                # into a one-second hole in an otherwise active recording.
                delay = (
                    INTERVAL_S
                    if self.last_error == "stale_device_session"
                    and session == self.domain.session
                    else ERROR_BACKOFF_S
                )
            await asyncio.sleep(max(0.01, delay - (time.monotonic() - started)))

    async def image_tick(self):
        domain = self.domain
        registry = domain.samples
        session = domain.session
        pending = {
            sid: row
            for sid, row in registry.study_images.items()
            if sid in registry.active
            and sid not in registry.closing
            and row["device"] == domain.device.id
            and row["count"] < row["limit"]
            and not registry.evidence.sessions[sid]["retired"]
        }
        videos = {
            sid: row
            for sid, row in registry.study_videos.items()
            if sid in registry.active
            and sid not in registry.closing
            and row["device"] == domain.device.id
            and not registry.evidence.sessions[sid]["retired"]
        }
        if not pending and not videos:
            return
        if self.source.connection is not None:
            key = await self.source.key()
        else:
            # Read only: neither camera power nor robot control may be changed here.
            camera = domain.data(await domain.read("/api/resource/camera", 0.5))
            activity = camera.get("activity") or camera
            enabled = activity.get("on", activity.get("enabled"))
            state = str(activity.get("state") or camera.get("state")).lower()
            if enabled is False or (
                enabled is not True and state not in {"on", "active", "running"}
            ):
                for sid in videos:
                    await registry.read(
                        registry.store.video.note, sid, "samples_video_camera_off"
                    )
                return
            _, _, headers = await domain.camera("raw")
            key = headers.get("x-rider-capture-key")
            if not key:
                raise RiderError("samples_frame_unavailable", 409)
            latest = await domain.pipeline(force=True)
            if latest.get("supported"):
                expected = (
                    latest.get("camera_session"),
                    latest.get("transport_epoch"),
                    latest.get("revision"),
                )
                received = tuple(
                    headers.get(name)
                    for name in (
                        "x-rider-camera-session",
                        "x-rider-transport-epoch",
                        "x-rider-pipeline-revision",
                    )
                )
                if (
                    latest.get("state") not in {"active", "waiting_frame"}
                    or expected[0] is None
                    or any(value is None for value in expected[1:])
                    or received != tuple(str(value) for value in expected)
                ):
                    raise RiderError("stream_epoch_changed", 409)
        for sid, row in videos.items():
            if (
                domain.session == session
                and registry.study_videos.get(sid) is row
                and sid not in registry.closing
            ):
                submitted = time.perf_counter()

                captured = registry.frames.get(key)

                def append_video(sid=sid, submitted=submitted, captured=captured):
                    self.metrics.record(
                        "writer_queue_ms", (time.perf_counter() - submitted) * 1000
                    )
                    with self.metrics.track("writer_append_ms"):
                        return registry.store.video.append(sid, captured)

                result = await registry.read(append_video)
                if result and result["state"] in {"error", "limit"}:
                    registry.study_videos.pop(sid, None)
        for sid, row in pending.items():
            if (
                domain.session != session
                or registry.study_images.get(sid) is not row
                or registry.evidence.sessions[sid]["retired"]
                or row["last"] == key
            ):
                continue
            now = time.monotonic()
            if videos and now - row.get("last_saved_mono", -1000) < 1:
                continue
            row["last_saved_mono"] = now
            registry.capture(sid, f"study-image-{sid}-{row['count']}", key)
            row["count"] += 1
            row["last"] = key
        self.image_reads += 1

    async def video_error(self, reason):
        registry = getattr(self.domain, "samples", None)
        if registry is None:
            return
        for sid in tuple(getattr(registry, "study_videos", {})):
            if sid not in registry.active or sid in registry.closing:
                continue
            try:
                result = await registry.read(
                    registry.store.video.note,
                    sid,
                    reason,
                    terminal=reason == "stream_epoch_changed",
                )
                if result and result["state"] == "error":
                    registry.study_videos.pop(sid, None)
            except Exception:
                # An unavailable optional writer must never hold up control IO.
                pass

    async def images(self):
        session = self.domain.session
        while session == self.domain.session and collecting(self.domain):
            started = time.monotonic()
            try:
                with self.metrics.track("cycle_ms"):
                    await self.source.start()
                    await self.image_tick()
                self.image_error = None
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    await self.source.close()
                    raise
                self.image_error = "study_image_read_cancelled"
                await self.video_error(self.image_error)
            except Exception as exc:
                self.image_error = (
                    exc.code if isinstance(exc, RiderError) else "study_image_failed"
                )
                await self.video_error(self.image_error)
            # Video targets five RAW receipts/second; still samples remain one/second.
            videos = getattr(getattr(self.domain, "samples", None), "study_videos", {})
            interval = 0.2 if videos else 1.0
            await asyncio.sleep(max(0.05, interval - (time.monotonic() - started)))

        await self.source.close()

    def cancel(self):
        if self.task is not None:
            self.task.cancel()
        if self.image_task is not None:
            self.image_task.cancel()

    async def close(self):
        self.cancel()
        if self.task is not None:
            await asyncio.gather(self.task, return_exceptions=True)
        if self.image_task is not None:
            await asyncio.gather(self.image_task, return_exceptions=True)
