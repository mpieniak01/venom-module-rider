"""Optional bounded selection on already completed live results. No Pi IO."""

from __future__ import annotations

import asyncio
import io
import math
import uuid
from functools import partial

from PIL import Image, ImageStat

from .errors import RiderError
from .event_policy import EventProfile, EventSelector, Observation
from .events_store import EventsStore


def scene_hash(payload):
    with Image.open(io.BytesIO(payload)) as image:
        if image.width * image.height > 4_000_000:
            raise RiderError("auto_image_invalid", 400)
        gray = image.convert("L").resize((9, 8))
    if ImageStat.Stat(gray).stddev[0] < 3:
        return None
    values = list(gray.tobytes())
    value = 0
    for y in range(8):
        for x in range(8):
            value = (value << 1) | int(values[y * 9 + x] > values[y * 9 + x + 1])
    return value


class AutomaticCapture:
    def __init__(self, registry):
        self.registry = registry
        self.store = EventsStore(registry.store)
        self.current = None
        self.last = None
        self.generation = 0
        self.pending = False
        self.transitions = asyncio.Lock()
        self.tasks: set[asyncio.Task] = set()
        self.closed = False

    def _track(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)

        def done(task):
            self.tasks.discard(task)
            if not task.cancelled() and task.exception() is not None:
                self.registry.failures += 1

        task.add_done_callback(done)
        return task

    async def start(self, session_id, request_id, device, source_session, profile):
        # A lost HTTP response does not create a second enable epoch.
        return await asyncio.shield(
            self._track(
                self._start(session_id, request_id, device, source_session, profile)
            )
        )

    async def _start(self, session_id, request_id, device, source_session, profile):
        async with self.transitions:
            if self.closed:
                raise RiderError("samples_closed", 503)
            values = EventProfile.model_validate(profile).model_dump()
            if self.current is not None:
                if self.current["request_id"] == request_id:
                    selector = self.current["selector"]
                    if (
                        self.current["session_id"] != session_id
                        or self.current["profile"] != values
                        or (selector.device, selector.source_session)
                        != (device, source_session)
                    ):
                        raise RiderError("samples_request_conflict", 409)
                    return self.status()
                raise RiderError("auto_already_on", 409)
            if (
                session_id not in self.registry.active
                or session_id in self.registry.closing
            ):
                raise RiderError("auto_collection_required", 409)
            generation = self.generation
            await self.registry.reserve_writer("automatic")
            try:
                await self.registry.read(self.store.recover)
                run_id = uuid.uuid4().hex
                record = await self.registry.read(
                    self.store.create,
                    run_id,
                    request_id,
                    session_id,
                    device,
                    source_session,
                    values,
                )
                if record["id"] != run_id:
                    return await self.registry.read(self.store.page, record["id"])
                if generation != self.generation or self.closed:
                    await self.registry.read(
                        self.store.finish, run_id, "auto_cancelled"
                    )
                    return {"id": run_id, "state": "off", "reason": "auto_cancelled"}
                now = self.registry.frames.clock()
                row = {
                    "id": run_id,
                    "session_id": session_id,
                    "request_id": request_id,
                    "profile": values,
                    "generation": generation,
                    "state": "on",
                    "reason": None,
                    "saved": 0,
                    "failed": 0,
                    "skipped": 0,
                    "dropped": 0,
                    "last_accepted": now,
                    "timer": None,
                    "selector": EventSelector(device, source_session, values, now),
                }
                self.current = self.last = row
                self._timer(row)
                return self.status()
            finally:
                if self.current is None:
                    await self.registry.release_writer("automatic")

    def _timer(self, row):
        if self.current is not row:
            return
        now = self.registry.frames.clock()
        if now - row["selector"].started >= row["profile"]["duration_s"]:
            self.invalidate("auto_duration_limit")
        elif (now - row["last_accepted"]) * 1000 > row["profile"]["max_gap_ms"]:
            self.invalidate("auto_feed_lost")
        else:
            row["timer"] = asyncio.get_running_loop().call_later(0.25, self._timer, row)

    def invalidate(self, reason="auto_operator_stop", *, session_id=None):
        row = self.current
        if (
            row is not None
            and session_id is not None
            and row["session_id"] != session_id
        ):
            return
        self.generation += 1
        self.current = None
        if row is not None:
            row["state"], row["reason"] = "off", reason
            if row["timer"] is not None:
                row["timer"].cancel()
            # Control STOP never waits for a blocked disk. Explicit data Stop
            # awaits this same ordered marker before reporting completion.
            self._track(self._finish(row))

    async def _finish(self, row):
        async with self.transitions:
            try:
                await asyncio.wrap_future(
                    self.registry.io.submit(
                        partial(self.store.finish, row["id"], row["reason"])
                    )
                )
            except Exception:
                row["reason"] = "auto_storage_unavailable"
                self.registry.failures += 1
            await self.registry.release_writer("automatic")

    async def stop(self, reason="auto_operator_stop", *, session_id=None):
        self.invalidate(reason, session_id=session_id)
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        return self.status()

    def status(self):
        row = self.last
        if row is None:
            return {"state": "off", "reason": None, "events": [], "counts": {}}
        selector = row["selector"]
        return {
            key: row[key]
            for key in (
                "id",
                "session_id",
                "state",
                "reason",
                "profile",
                "saved",
                "failed",
                "skipped",
                "dropped",
            )
        } | {
            "attempts": selector.attempts,
            "rejected": selector.rejected,
            "deduplicated": selector.deduplicated,
            "suppressed": selector.suppressed,
            "binding": selector.binding,
        }

    def offer(self, key):
        row = self.current
        if row is None or self.closed:
            return
        if self.pending:
            row["dropped"] += 1
            return
        try:
            frame = self.registry.frames.get(key)
        except RiderError:
            self.invalidate("auto_frame_missing")
            return
        received = self.registry.frames.clock()
        context = frame.context()
        prediction = context.get("prediction") or {}
        # Remaining lifetime comes from the original decision, not writer time.
        budget = prediction.get("valid_for_ms")
        if (
            not isinstance(budget, (int, float))
            or isinstance(budget, bool)
            or not math.isfinite(budget)
            or budget <= 0
        ):
            self.invalidate("auto_source_stale")
            return
        self.pending = True
        loop = asyncio.get_running_loop()
        try:
            self.registry._submit(
                partial(
                    self._process,
                    row,
                    frame,
                    received,
                    received + min(200, budget) / 1000,
                )
            ).add_done_callback(lambda _: loop.call_soon_threadsafe(self._processed))
        except RiderError:
            self.pending = False
            row["dropped"] += 1

    def _processed(self):
        self.pending = False
        row = self.current
        if row is not None:
            selector = row["selector"]
            if (
                selector.attempts >= selector.profile.max_samples
                and not selector.stopped
            ):
                selector.stop("auto_sample_limit")
            if selector.stopped:
                self.invalidate(selector.stopped)

    def _process(self, row, frame, received, expires):
        try:
            if self.current is not row:
                return
            now = self.registry.frames.clock()
            context = frame.context()
            prediction = context.get("prediction") or {}
            descriptor = prediction.get("input_frame") or {}
            state = prediction.get("state")
            if any(
                context.get(key) != descriptor.get(key)
                for key in ("run_id", "frame_id", "sequence")
            ):
                row["selector"].stop("auto_source_changed")
                return
            source = Observation(
                frame.device_id,
                context.get("host_device_session"),
                descriptor.get("run_id", ""),
                descriptor.get("config_revision", -1),
                prediction.get("model_sha256", ""),
                prediction.get("perception_engine", ""),
                descriptor.get("frame_id", ""),
                descriptor.get("sequence", -1),
                frame.sha256,
                received,
                expires,
                state if state in {"clear", "obstacle", "unknown"} else "unknown",
                str(prediction.get("reason") or "unknown"),
                scene_hash(frame.payload),
                bool(
                    context.get("stale")
                    or context.get("recorded_at_header")
                    or context.get("source_session") is None
                    or prediction.get("diagnostic")
                ),
            )
            selector = row["selector"]
            before = selector.last_sequence
            event = selector.evaluate(source, now)
            if selector.last_sequence != before:
                row["last_accepted"] = now
            if event is None or self.current is not row:
                return
            # Deadline is checked again after decoding/selection, never extended.
            if self.registry.frames.clock() > expires:
                selector.stop("auto_source_stale")
                return
            result = self.store.persist(
                row["id"],
                row["session_id"],
                event,
                frame,
                max(0, (now - frame.received_mono) * 1000),
                row["profile"],
            )
            row[result] += 1
            if result == "failed":
                selector.stop("auto_write_failed")
        except Exception as exc:
            row["failed"] += 1
            row["selector"].stop(
                exc.code if isinstance(exc, RiderError) else "auto_write_failed"
            )

    async def close(self):
        self.closed = True
        await self.stop("auto_shutdown")
