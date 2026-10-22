"""One bounded read-only sampler for explicitly active motion studies."""

from __future__ import annotations

import asyncio
import time

from .errors import RiderError

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
            and getattr(getattr(self.domain, "samples", None), "study_images", {})
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
        if not pending:
            return
        # Read only: neither camera power nor robot control may be changed here.
        camera = domain.data(await domain.read("/api/resource/camera", 0.5))
        activity = camera.get("activity") or camera
        enabled = activity.get("on", activity.get("enabled"))
        state = str(activity.get("state") or camera.get("state")).lower()
        if enabled is False or (
            enabled is not True and state not in {"on", "active", "running"}
        ):
            return
        _, _, headers = await domain.camera("raw")
        key = headers.get("x-rider-capture-key")
        if not key:
            raise RiderError("samples_frame_unavailable", 409)
        for sid, row in pending.items():
            if (
                domain.session != session
                or registry.study_images.get(sid) is not row
                or registry.evidence.sessions[sid]["retired"]
                or row["last"] == key
            ):
                continue
            registry.capture(sid, f"study-image-{sid}-{row['count']}", key)
            row["count"] += 1
            row["last"] = key
        self.image_reads += 1

    async def images(self):
        session = self.domain.session
        while session == self.domain.session and collecting(self.domain):
            started = time.monotonic()
            try:
                await self.image_tick()
                self.image_error = None
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise
                self.image_error = "study_image_read_cancelled"
            except Exception as exc:
                self.image_error = (
                    exc.code if isinstance(exc, RiderError) else "study_image_failed"
                )
            # One bounded source read per second, independent of UI tab/polling.
            await asyncio.sleep(max(0.05, 1.0 - (time.monotonic() - started)))

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
