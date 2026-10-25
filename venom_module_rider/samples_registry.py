"""Bounded capture jobs in the existing module process; never a Pi archive."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import os
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import BinaryIO

from .errors import RiderError
from .sample_originals import OriginalReader
from .samples_contract import CollectionPolicy
from .samples_frame import FrameBuffer
from .samples_store import SampleStore, encode, identifier
from .session_video_reader import SessionVideoReader


def default_root():
    configured = os.getenv("RIDER_SAMPLE_ROOT")
    if configured:
        return Path(configured)
    base = Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    realm = (
        "demo-samples"
        if os.getenv("RIDER_DEMO_ONLY", "").lower() in {"1", "true"}
        else "samples"
    )
    return base / "venom/rider" / realm


class SamplesRegistry:
    def __init__(self, root: Path, *, store=None, frames=None, queue_slots=4):
        if type(queue_slots) is not int or not 1 <= queue_slots <= 16:
            raise ValueError("invalid writer queue")
        self.store = store or SampleStore(root)
        self.frames = frames or FrameBuffer()
        self.originals = OriginalReader(self.store)
        self.video_reader = SessionVideoReader(self.store)
        self.io = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rider-samples")
        self.slots = threading.BoundedSemaphore(queue_slots)
        self.queue_slots = queue_slots
        self.pending: OrderedDict[str, dict] = OrderedDict()
        self.lock = threading.Lock()
        self.operations = asyncio.Lock()
        self.closed = False
        self.session_requests: dict[str, str] = {}
        self.active: set[str] = set()
        self.writer_owners: set[str] = set()
        self.closing: set[str] = set()
        self.sequences: dict[str, dict] = {}
        self.deadlines: dict[str, asyncio.TimerHandle] = {}
        self.study_images: dict[str, dict] = {}
        self.study_videos: dict[str, dict] = {}
        self.lease: BinaryIO | None = None
        self.failures = 0
        from .automatic_capture import AutomaticCapture

        self.automatic = AutomaticCapture(self)
        from .sample_evidence import SampleEvidenceCollector

        self.evidence = SampleEvidenceCollector(self)
        from .motion_capture import MotionWriter

        self.motion_writer = MotionWriter(self)

    def _open(self):
        if self.closed:
            raise RiderError("samples_closed", 503)

    def _lease(self):
        if self.lease is not None:
            return
        path = self.store.root / "collector.lock"
        if path.is_symlink():
            raise RiderError("samples_root_invalid", 400)
        stream = path.open("a+b")
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            stream.close()
            raise RiderError("samples_registry_busy", 409) from exc
        self.lease = stream
        self.store.quota.ownership(True)

    def _release(self):
        if self.lease is not None:
            self.store.quota.ownership(False)
            fcntl.flock(self.lease, fcntl.LOCK_UN)
            self.lease.close()
            self.lease = None

    async def reserve_writer(self, owner):
        """Share the existing process lease and IO writer with derived jobs."""
        async with self.operations:
            self._open()
            newly_owned = self.lease is None
            self._lease()
            try:
                if newly_owned:
                    await self.read(self.store.recover_interrupted)
                    await self.read(self.automatic.store.recover)
                self.writer_owners.add(owner)
            except Exception:
                if not self.active and not self.writer_owners:
                    self._release()
                raise

    async def release_writer(self, owner):
        async with self.operations:
            self.writer_owners.discard(owner)
            if not self.active and not self.writer_owners:
                self._release()

    def _run_io(self, call):
        with self.store.quota.job():
            return call()

    def _submit(self, call):
        self._open()
        if not self.slots.acquire(blocking=False):
            raise RiderError("samples_queue_full", 429)
        try:
            future = self.io.submit(self._run_io, call)
        except Exception:
            self.slots.release()
            raise
        future.add_done_callback(lambda _: self.slots.release())
        return future

    async def read(self, method, *args, **kwargs):
        return await asyncio.wrap_future(self._submit(partial(method, *args, **kwargs)))

    async def original(self, sample_id):
        self._open()
        return await self.originals.get(sample_id)

    async def start(self, request_id, device_id, policy=None):
        values = CollectionPolicy.model_validate(policy or {}).model_dump()
        async with self.operations:
            self._open()
            if len(self.active) >= 4 and request_id not in self.session_requests:
                raise RiderError("samples_collection_limit", 429)
            newly_owned = self.lease is None
            self._lease()
            try:
                if newly_owned:
                    await self.read(self.store.recover_interrupted)
                    await self.read(self.automatic.store.recover)
                session = await self.read(
                    self.store.start, request_id, device_id, values
                )
                if session["state"] == "collecting":
                    if values["record_video"]:
                        try:
                            session["video"] = await self.read(
                                self.store.video.start, session["session_id"]
                            )
                        except Exception:
                            await self.read(
                                self.store.end, session["session_id"], incomplete=True
                            )
                            raise
                        self.study_videos.setdefault(
                            session["session_id"], {"device": device_id}
                        )
                    self.active.add(session["session_id"])
                    self.evidence.start(session)
                    if (
                        values["record_images"]
                        and values["kind"] == "manual"
                        and values["scene_group"].startswith("motion-study-v1:")
                    ):
                        self.study_images.setdefault(
                            session["session_id"],
                            {
                                "device": device_id,
                                "count": 0,
                                "last": None,
                                "limit": values["max_frames"],
                            },
                        )
                    self.session_requests[request_id] = session["session_id"]
                    if values["kind"] == "sequence":
                        self._sequence(session)
                    elif (
                        values["kind"] == "manual"
                        and session["session_id"] not in self.deadlines
                    ):
                        self.deadlines[session["session_id"]] = (
                            asyncio.get_running_loop().call_later(
                                self.store.limits.session_seconds,
                                lambda: self._expire_manual(session["session_id"]),
                            )
                        )
                return session
            except Exception:
                if not self.active and not self.writer_owners:
                    self._release()
                raise

    def _expire_manual(self, session_id):
        if self.closed or session_id in self.closing:
            return
        self.closing.add(session_id)
        task = asyncio.create_task(self.end(session_id, incomplete=True))
        task.add_done_callback(self._end_result)

    def _sequence(self, session):
        key = session["session_id"]
        if key in self.sequences:
            return
        policy = session["policy"]
        now = self.frames.clock()
        row = {
            "device": session["source_device_id"],
            "count": 0,
            "last": None,
            "start": now,
            "end": now + policy["duration_s"],
            "policy": policy,
            "incomplete": False,
            "timer": None,
        }
        self.sequences[key] = row
        row["timer"] = asyncio.get_running_loop().call_later(
            policy["duration_s"], self._finish_sequence, key
        )

    def _finish_sequence(self, session_id):
        row = self.sequences.get(session_id)
        if row is None or session_id in self.closing:
            return
        self.closing.add(session_id)
        tail_gap = (
            row["last"] is None
            or (self.frames.clock() - row["last"]) * 1000
            > row["policy"]["interval_ms"] * 3
        )
        task = asyncio.create_task(
            self.end(session_id, incomplete=row["incomplete"] or tail_gap)
        )
        task.add_done_callback(self._end_result)

    def _end_result(self, task):
        if not task.cancelled() and task.exception() is not None:
            self.failures += 1

    def _finish_evidence(self, session_id):
        try:
            self.evidence.finish(session_id)
        except Exception:
            # Optional evidence status cannot prevent core session cleanup.
            self.failures += 1

    async def end(self, session_id, *, incomplete=False):
        await self.automatic.stop("auto_collection_ended", session_id=session_id)
        async with self.operations:
            self._open()
            self.closing.add(session_id)
            self.study_images.pop(session_id, None)
            self.study_videos.pop(session_id, None)
            with self.evidence.lock:
                retired = self.evidence.sessions.get(session_id, {}).get(
                    "retired", False
                )
            incomplete = incomplete or retired
            self.evidence.retire("collection_ended", session_id)
            deadline = self.deadlines.pop(session_id, None)
            if deadline is not None:
                deadline.cancel()
            row = self.sequences.pop(session_id, None)
            if row and row["timer"] is not None:
                row["timer"].cancel()
                incomplete = incomplete or row["incomplete"]

            # One serialized close marker follows admitted writes even when all
            # capture slots are occupied. It is not another source-data job.
            def finish():
                self.store.video.finish(session_id, interrupted=incomplete)
                self._finish_evidence(session_id)
                return self.store.end(session_id, incomplete=incomplete)

            result = await asyncio.wrap_future(self.io.submit(self._run_io, finish))
            self.active.discard(session_id)
            self.closing.discard(session_id)
            if not self.active and not self.writer_owners:
                self._release()
            return result

    @staticmethod
    def _public(row):
        return {
            key: row.get(key)
            for key in ("id", "session_id", "state", "sample_id", "error", "saved_at")
        }

    def capture(self, session_id, request_id, frame_key, *, allow_historical=False):
        self._open()
        identifier(request_id)
        fingerprint = hashlib.sha256(
            encode([session_id, frame_key]).encode()
        ).hexdigest()
        with self.lock:
            old = self.pending.get(request_id)
            if old:
                if old["fingerprint"] != fingerprint:
                    raise RiderError("samples_request_conflict", 409)
                return self._public(old)
        # Let the store resolve a persisted idempotent request even after eviction.
        try:
            frame = self.frames.get(frame_key)
        except RiderError:
            frame = None
        writable = session_id in self.active and session_id not in self.closing
        admitted = self.store.wall()
        age = max(0, (self.frames.clock() - frame.received_mono) * 1000) if frame else 0
        row = {
            "id": request_id,
            "fingerprint": fingerprint,
            "session_id": session_id,
            "state": "queued",
            "sample_id": None,
            "error": None,
            "saved_at": None,
        }

        def persist():
            try:
                # Saved requests can be queried/retried after the collection ends.
                if not writable:
                    result = self.store.request(request_id)
                    if result["fingerprint"] != fingerprint:
                        raise RiderError("samples_request_conflict", 409)
                else:
                    result = self.store.capture(
                        session_id,
                        request_id,
                        frame_key,
                        frame,
                        admitted_at=admitted,
                        allow_historical=allow_historical,
                        receipt_age_ms=age,
                    )
            except Exception as exc:
                result = {
                    **row,
                    "state": "failed",
                    "error": exc.code
                    if isinstance(exc, RiderError)
                    else "samples_write_failed",
                }
            with self.lock:
                self.pending[request_id] = {**row, **result}
                while len(self.pending) > 256:
                    old_key = next(
                        (
                            key
                            for key, value in self.pending.items()
                            if value["state"] not in {"queued", "running"}
                        ),
                        None,
                    )
                    if old_key is None:
                        break
                    del self.pending[old_key]

        with self.lock:
            self.pending[request_id] = row
        try:
            self._submit(persist)
        except Exception:
            with self.lock:
                self.pending.pop(request_id, None)
            raise
        return self._public(row)

    async def request(self, request_id):
        with self.lock:
            row = self.pending.get(request_id)
            if row:
                return self._public(row)
        return self._public(await self.read(self.store.request, request_id))

    def observe_evidence(
        self,
        path,
        payload,
        device_id,
        source_session,
        received_mono=None,
        received_at=None,
    ):
        try:
            self.evidence.observe(
                path, payload, device_id, source_session, received_mono, received_at
            )
        except Exception:
            self.failures += 1

    def retire_evidence(self, reason="source_changed"):
        self.evidence.retire(reason)

    def offer(self, device_id, payload, mime, context=None):
        if self.closed:
            return None
        try:
            context = self.evidence.enrich(device_id, context)
        except Exception:
            self.failures += 1
            self.frames.rejected += 1
            return None
        key = self.frames.offer(device_id, payload, mime, context)
        if key is None or self.closed:
            return key
        now = self.frames.clock()
        for session_id, row in tuple(self.sequences.items()):
            if row["device"] != device_id or session_id in self.closing:
                continue
            if now >= row["end"] or row["count"] >= row["policy"]["max_frames"]:
                self._finish_sequence(session_id)
                continue
            last = row["last"]
            if last is not None and (now - last) * 1000 < row["policy"]["interval_ms"]:
                continue
            if (now - (last if last is not None else row["start"])) * 1000 > row[
                "policy"
            ]["interval_ms"] * 3:
                row["incomplete"] = True
            try:
                self.capture(session_id, f"sequence-{session_id}-{row['count']}", key)
                row["count"] += 1
                row["last"] = now
            except RiderError:
                row["incomplete"] = True
                self.failures += 1
        return key

    async def status(self):
        return {
            **await self.read(self.store.status),
            "buffer": self.frames.status(),
            "evidence": self.evidence.status(),
            "writer_queue_slots": self.queue_slots,
            "failures": self.failures,
            "active": list(self.active),
            "closing": list(self.closing),
            "sequences": {
                key: {
                    "frames": row["count"],
                    "incomplete": row["incomplete"],
                    "remaining_s": max(0, row["end"] - self.frames.clock()),
                }
                for key, row in self.sequences.items()
            },
        }

    def annotate_prediction(self, key, prediction):
        annotated = self.frames.annotate_prediction(key, prediction)
        if annotated:
            try:
                self.automatic.offer(key)
            except Exception:
                self.failures += 1
        return annotated

    async def close(self):
        await self.automatic.close()
        async with self.operations:
            if self.closed:
                return
            self.closed = True
            await self.originals.close()
            self.evidence.retire("registry_closed")
            for row in self.sequences.values():
                row["timer"].cancel()
            self.sequences.clear()
            self.study_images.clear()
            self.study_videos.clear()
            for timer in self.deadlines.values():
                timer.cancel()
            self.deadlines.clear()

            def finish():
                try:
                    if self.lease is not None:
                        for session_id in tuple(self.evidence.sessions):
                            self.store.video.finish(session_id, interrupted=True)
                            self._finish_evidence(session_id)
                        self.store.recover_interrupted()
                finally:
                    self.active.clear()
                    self._release()

            # One final cleanup behind at most queue_slots admitted operations.
            try:
                await asyncio.wrap_future(self.io.submit(self._run_io, finish))
            except Exception:
                self.failures += 1
            finally:
                self.io.shutdown(wait=False)
