"""Bounded in-memory inference, independent of manual control and browser lifecycle."""

from __future__ import annotations

import asyncio
import copy
import time
import uuid
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import Any

from .compute_contract import LIMITS, MAX_OUTPUT, JobRequest, validate_media
from .errors import RiderError

TERMINAL = {"succeeded", "failed", "cancelled", "expired", "superseded"}


@dataclass
class Lease:
    device_id: str
    device_session: str
    client_id: str
    client_instance: str
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    used_at: float = field(default_factory=time.monotonic)
    sequences: dict[tuple[str, str], int] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    voice_turns: dict[tuple, Any] = field(default_factory=dict)


@dataclass
class Job:
    request: JobRequest
    device_id: str
    payload: bytes
    mime: str
    revision: str
    received: float = field(default_factory=time.monotonic)
    received_at: float = field(default_factory=time.time)
    state: str = "queued"
    error: str | None = None
    finished_at: float | None = None
    finished: float | None = None
    started: float | None = None
    runtime: dict = field(default_factory=dict)
    quality: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)
    output: bytes | None = None
    output_mime: str | None = None
    transfer_bytes: int = 0
    conversation_turn: Any = None  # Host-owned; never serialized or client supplied.

    @property
    def deadline(self):
        return self.received + self.request.ttl_ms / 1000

    def observation(self) -> dict:
        elapsed = time.monotonic() - self.received
        return {
            **self.request.model_dump(exclude={"params", "ttl_ms", "input_age_ms"}),
            "device_id": self.device_id,
            "received_at": self.received_at,
            "finished_at": self.finished_at,
            "state": self.state,
            "error": self.error,
            "runtime": copy.deepcopy(self.runtime),
            "quality": {
                **copy.deepcopy(self.quality),
                "timestamp_source": self.request.timestamp_source,
            },
            "metrics": {
                "transfer_bytes": self.transfer_bytes,
                "queue_ms": round(
                    ((self.started or time.monotonic()) - self.received) * 1000, 1
                ),
                "inference_ms": round(
                    ((self.finished or time.monotonic()) - self.started) * 1000, 1
                )
                if self.started is not None
                else None,
                "source_age_ms": round(self.request.input_age_ms + elapsed * 1000, 1),
            },
            "data": copy.deepcopy(self.data),
            "output_available": self.output is not None and self.state == "succeeded",
        }


class ComputeService:
    def __init__(self, engines: Any):
        self.engines = engines
        self.executor_session = uuid.uuid4().hex
        self.leases: dict[tuple[str, str], Lease] = {}
        self.retired_boots: dict[str, set[str]] = {}
        self.jobs: dict[str, Job] = {}
        self.pending: deque[str] = deque()
        self.worker: asyncio.Task | None = None
        self.inference: asyncio.Task | None = None
        self.running: str | None = None
        self.closed = False
        self._cleanups: set[asyncio.Task] = set()
        self.counters = {
            "submitted": 0,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
            "expired": 0,
            "superseded": 0,
            "rejected": 0,
        }

    async def capabilities(self):
        self._available()
        state = await self.engines.capabilities()
        return {
            **state,
            "protocol_version": 1,
            "executor_id": "venom",
            "executor_session": self.executor_session,
            "server_time": time.time(),
            "limits": LIMITS.copy(),
        }

    def _available(self):
        if self.closed:
            raise RiderError("compute_closed", 503)

    def open_session(
        self,
        device_id: str,
        device_session: str,
        client_id: str = "cli",
        client_instance: str = "legacy",
    ):
        self._available()
        self._prune()
        if device_session in self.retired_boots.get(device_id, set()):
            raise RiderError("compute_stale_session", 409)
        for old in list(self.leases.values()):
            if old.device_id == device_id and old.device_session != device_session:
                retired = self.retired_boots.setdefault(device_id, set())
                if len(retired) >= 32:
                    raise RiderError("compute_session_limit", 429)
                retired.add(old.device_session)
                self.close_session(device_id, old.session_id)
        previous = self.leases.get((device_id, client_id))
        if previous and previous.client_instance != client_instance:
            self.close_session(device_id, previous.session_id)
            previous = None
        lease = previous or Lease(device_id, device_session, client_id, client_instance)
        lease.used_at = time.monotonic()
        self.leases[(device_id, client_id)] = lease
        return {
            "protocol_version": 1,
            "executor_id": "venom",
            "executor_session": self.executor_session,
            "device_id": device_id,
            "device_session": device_session,
            "session_id": lease.session_id,
            "server_time": time.time(),
            "client_id": client_id,
            "client_instance": client_instance,
            "limits": LIMITS.copy(),
        }

    def lease(self, device_id: str, session_id: str) -> Lease:
        self._available()
        lease = next(
            (
                item
                for item in self.leases.values()
                if item.device_id == device_id and item.session_id == session_id
            ),
            None,
        )
        if not lease or lease.session_id != session_id:
            raise RiderError("compute_stale_session", 409)
        lease.used_at = time.monotonic()
        return lease

    def _finish(self, job: Job, state: str, error: str | None = None):
        if job.state in TERMINAL:
            return
        job.state, job.error = state, error
        job.finished_at, job.finished = time.time(), time.monotonic()
        job.payload = b""
        self.counters[state] += 1
        turn, job.conversation_turn = job.conversation_turn, None
        if (
            turn is not None
            and state == "succeeded"
            and job.request.task == "audio.transcribe"
        ):
            lease = self.leases.get((job.device_id, "voice"))
            transcript = job.data.get("text")
            if (
                lease
                and lease.session_id == job.request.session_id
                and isinstance(transcript, str)
                and transcript.strip()
            ):
                lease.voice_turns[self._voice_key(job.request)] = replace(
                    turn, transcript=transcript.strip()
                )
                turn = None
        if turn is not None:
            self._release_turn(turn)

    @staticmethod
    def _voice_key(request):
        return (
            request.input_id,
            request.sequence,
            request.captured_at,
            request.timestamp_source,
        )

    def _release_turn(self, turn):
        cleanup = asyncio.create_task(self.engines.finish_conversation(turn))
        self._cleanups.add(cleanup)
        cleanup.add_done_callback(self._cleanup_done)

    def _cleanup_done(self, task):
        self._cleanups.discard(task)
        if not task.cancelled():
            task.exception()

    def cancel(self, device_id: str, job_id: str):
        job = self.owned_job(device_id, job_id)
        self._cancel_job(job, "cancelled", "compute_cancelled")
        return job.observation()

    def _cancel_job(self, job: Job, state: str, error: str):
        if job.request.task == "audio.transcribe":
            lease = self.leases.get((job.device_id, "voice"))
            if lease and lease.session_id == job.request.session_id:
                turn = lease.voice_turns.pop(self._voice_key(job.request), None)
                if turn is not None:
                    self._release_turn(turn)
        if job.state == "succeeded":
            job.state, job.error = state, error
            job.data = {}
            self.counters[state] += 1
        self._finish(job, state, error)
        job.output = None
        if self.running == job.request.job_id and self.inference:
            self.inference.cancel()
        with suppress(ValueError):
            self.pending.remove(job.request.job_id)

    def close_session(self, device_id: str, session_id: str):
        lease = self.lease(device_id, session_id)
        for turn in lease.voice_turns.values():
            self._release_turn(turn)
        lease.voice_turns.clear()
        for job in list(self.jobs.values()):
            if job.device_id == device_id and job.request.session_id == session_id:
                self._cancel_job(job, "cancelled", "compute_session_closed")
                self.jobs.pop(job.request.job_id, None)
        self.leases.pop((device_id, lease.client_id), None)
        return {"protocol_version": 1, "closed": True}

    def _prune(self):
        now = time.monotonic()
        for lease in self.leases.values():
            for key, turn in list(lease.voice_turns.items()):
                if now >= turn.context.deadline:
                    lease.voice_turns.pop(key)
                    self._release_turn(turn)
        for job in self.jobs.values():
            if now > job.deadline and (
                job.state not in TERMINAL or job.state == "succeeded"
            ):
                self._cancel_job(job, "expired", "compute_deadline")
        terminal = sorted(
            (job for job in self.jobs.values() if job.state in TERMINAL),
            key=lambda item: item.finished or 0,
        )
        for i, job in enumerate(terminal):
            if now - (job.finished or now) > 60 or i < len(terminal) - 32:
                self.jobs.pop(job.request.job_id, None)

    async def submit(
        self, device_id: str, request: JobRequest, payload: bytes, mime: str
    ):
        self._available()
        self._prune()
        received, received_at = time.monotonic(), time.time()
        turn = None
        try:
            if request.task in {"audio.transcribe", "conversation.respond"}:
                lease = self.lease(device_id, request.session_id)
                if (
                    request.task == "conversation.respond"
                    and lease.client_id != "voice"
                ):
                    raise RiderError("conversation_actor_invalid", 403)
                if (
                    request.executor_session != self.executor_session
                    or request.device_session != lease.device_session
                ):
                    raise RiderError("compute_stale_session", 409)
                validate_media(request, payload, mime)
                prepare = getattr(self.engines, "prepare_conversation", None)
                if prepare is None and request.task == "conversation.respond":
                    raise RiderError("conversation_disabled", 503)
                if prepare is not None and lease.client_id == "voice":
                    if (
                        request.task == "audio.transcribe"
                        and len(lease.voice_turns) >= 16
                    ):
                        raise RiderError("conversation_busy", 429)
                    captured_turn = (
                        lease.voice_turns.pop(self._voice_key(request), None)
                        if request.task == "conversation.respond"
                        else None
                    )
                    turn = captured_turn
                    turn = await prepare(
                        payload,
                        {
                            **request.model_dump(),
                            "device_id": device_id,
                            "client_id": lease.client_id,
                            "received_monotonic": received,
                            "deadline_monotonic": received + request.ttl_ms / 1000,
                            "conversation_turn": captured_turn,
                        },
                        request.params.language,
                    )
            result = await self._submit(
                device_id, request, payload, mime, received, received_at, turn
            )
            turn = None  # The accepted job owns cleanup from here.
            return result
        except RiderError:
            raise
        except Exception as exc:
            raise RiderError(
                getattr(exc, "code", "conversation_request_failed"),
                getattr(exc, "status_code", 502),
            ) from exc
        finally:
            if turn is not None:
                await self.engines.finish_conversation(turn)

    async def _submit(
        self, device_id, request, payload, mime, received, received_at, turn
    ):
        self._available()
        self._prune()
        lease = self.lease(device_id, request.session_id)
        if request.task == "conversation.respond" and lease.client_id != "voice":
            raise RiderError("conversation_actor_invalid", 403)
        if (
            request.executor_session != self.executor_session
            or request.device_session != lease.device_session
        ):
            raise RiderError("compute_stale_session", 409)
        if request.job_id in self.jobs or request.job_id in lease.seen:
            raise RiderError("compute_duplicate_job", 409)
        key = (request.task, request.stream_id)
        if request.sequence <= lease.sequences.get(key, -1):
            raise RiderError("compute_out_of_order", 409)
        if (
            len(lease.seen) >= 4096
            or len(lease.sequences) >= 32
            and key not in lease.sequences
        ):
            raise RiderError("compute_session_limit", 429)
        validate_media(request, payload, mime)
        urgent = turn is not None and turn.urgent
        if urgent:
            capabilities = {
                "config_revision": await self.engines.revision(),
                "tasks": {request.task: {"available": True}},
            }
        else:
            capabilities = await self.engines.capabilities()
        capability = capabilities.get("tasks", {}).get(request.task, {})
        if capability.get("available") is not True:
            raise RiderError(
                "compute_engine_unavailable",
                503,
                {"task": request.task, "reason": capability.get("reason")},
            )
        # Re-check after the asynchronous capability probe and discard new work
        # if OFF/restart changed the lease while the probe was in flight.
        if self.lease(device_id, request.session_id) is not lease:
            raise RiderError("compute_stale_session", 409)
        if time.monotonic() - received >= request.ttl_ms / 1000:
            raise RiderError("compute_deadline", 408)
        if request.job_id in self.jobs or request.job_id in lease.seen:
            raise RiderError("compute_duplicate_job", 409)
        if request.sequence <= lease.sequences.get(key, -1):
            raise RiderError("compute_out_of_order", 409)
        if (
            len(lease.seen) >= 4096
            or len(lease.sequences) >= 32
            and key not in lease.sequences
        ):
            raise RiderError("compute_session_limit", 429)
        if request.task == "vision.describe":
            for job in list(self.jobs.values()):
                if (
                    job.device_id == device_id
                    and job.request.task == request.task
                    and job.request.stream_id == request.stream_id
                    and job.state not in TERMINAL
                ):
                    self._cancel_job(job, "superseded", "compute_newer_frame")
        if not urgent and len(self.pending) >= LIMITS["max_pending"]:
            self.counters["rejected"] += 1
            raise RiderError("compute_saturated", 429)
        job = Job(
            request,
            device_id,
            payload,
            mime,
            capabilities["config_revision"],
            received=received,
            received_at=received_at,
            runtime=capability.get("runtime", {}).copy(),
            quality=capability.get("quality", {}).copy(),
            transfer_bytes=len(payload),
            conversation_turn=turn,
        )
        self.jobs[request.job_id] = job
        lease.seen.add(request.job_id)
        lease.sequences[key] = request.sequence
        self.counters["submitted"] += 1
        if urgent:
            # STOP uses the host's safety provider, not the occupied inference slot.
            job.state, job.started = "running", time.monotonic()
            try:
                result = await asyncio.wait_for(
                    self.engines.urgent_conversation(
                        payload,
                        {
                            "conversation_turn": turn,
                            "client_id": lease.client_id,
                        },
                        request.params.language,
                    ),
                    timeout=max(0, job.deadline - time.monotonic()),
                )
                job.data, job.runtime, job.quality = (
                    result.data,
                    result.runtime,
                    result.quality,
                )
                self._finish(job, "succeeded")
            except TimeoutError:
                self._finish(job, "expired", "compute_deadline")
            except asyncio.CancelledError:
                self._finish(job, "cancelled", "compute_cancelled")
                raise
            except Exception as exc:
                self._finish(
                    job, "failed", getattr(exc, "code", "compute_engine_error")
                )
            return job.observation()
        self.pending.append(request.job_id)
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._run())
        return job.observation()

    def owned_job(self, device_id: str, job_id: str) -> Job:
        self._available()
        self._prune()
        job = self.jobs.get(job_id)
        if not job or job.device_id != device_id:
            raise RiderError("compute_job_missing", 404)
        self.lease(device_id, job.request.session_id)
        return job

    async def observe(self, device_id: str, job_id: str):
        job = self.owned_job(device_id, job_id)
        revision = await self.engines.revision()
        self.owned_job(device_id, job_id)
        if job.revision != revision:
            self._cancel_job(job, "expired", "compute_runtime_changed")
            job.state, job.error = "expired", "compute_runtime_changed"
            job.data, job.output = {}, None
        return job.observation()

    async def output(self, device_id: str, job_id: str):
        await self.observe(device_id, job_id)
        job = self.owned_job(device_id, job_id)
        if job.state != "succeeded" or job.output is None:
            raise RiderError("compute_output_unavailable", 409)
        return job.output, job.output_mime or "audio/wav"

    async def _run(self):
        while self.pending and not self.closed:
            self._prune()
            if not self.pending:
                break
            job = self.jobs[self.pending.popleft()]
            self.running = job.request.job_id
            try:
                if job.revision != await self.engines.revision():
                    self._finish(job, "expired", "compute_runtime_changed")
                    continue
                # OFF, restart or a newer frame can arrive while revision()
                # resolves the active adapter. Never revive a cancelled job.
                if (
                    self.closed
                    or job.state in TERMINAL
                    or self.jobs.get(job.request.job_id) is not job
                ):
                    continue
                if time.monotonic() >= job.deadline:
                    self._finish(job, "expired", "compute_deadline")
                    continue
                job.state, job.started = "running", time.monotonic()
                metadata = job.request.model_dump()
                metadata.update(device_id=job.device_id, content_type=job.mime)
                if job.request.task == "conversation.respond":
                    lease = self.lease(job.device_id, job.request.session_id)
                    metadata.update(
                        client_id=lease.client_id,
                        deadline_monotonic=job.deadline,
                        conversation_revision=job.runtime.get("policy_revision"),
                        conversation_turn=job.conversation_turn,
                    )
                self.inference = asyncio.create_task(
                    self.engines.execute(
                        job.request.task,
                        job.request.params.model_dump(),
                        job.payload,
                        metadata,
                    )
                )
                done, _ = await asyncio.wait(
                    {self.inference}, timeout=max(0, job.deadline - time.monotonic())
                )
                if not done:
                    self._cancel_job(job, "expired", "compute_deadline")
                # Shared synchronous inference cannot be killed. Drain it before
                # occupying the next slot, while STOP remains entirely separate.
                result = await self.inference
                if job.state in TERMINAL:
                    continue
                if job.revision != await self.engines.revision():
                    self._finish(job, "expired", "compute_runtime_changed")
                    continue
                if result.binary is not None and len(result.binary) > MAX_OUTPUT:
                    self._finish(job, "failed", "compute_output_size")
                    continue
                job.data, job.runtime, job.quality = (
                    result.data,
                    result.runtime,
                    result.quality,
                )
                job.output, job.output_mime = result.binary, result.content_type
                self._finish(job, "succeeded")
            except asyncio.CancelledError:
                self._finish(job, "cancelled", "compute_cancelled")
            except Exception as exc:
                self._finish(
                    job, "failed", getattr(exc, "code", "compute_engine_error")
                )
            finally:
                job.payload = b""
                drain = getattr(self.engines, "drain", None)
                if drain:
                    await drain()
                self.running, self.inference = None, None

    async def status(self, device_id: str | None = None):
        self._prune()
        capabilities = await self.capabilities()
        self._prune()
        for job in self.jobs.values():
            if job.revision != capabilities["config_revision"]:
                self._cancel_job(job, "expired", "compute_runtime_changed")
        return {
            **capabilities,
            "queue": {"running": bool(self.running), "pending": len(self.pending)},
            "metrics": self.counters.copy(),
            "jobs": [
                job.observation()
                for job in list(self.jobs.values())[-10:]
                if device_id is None or job.device_id == device_id
            ],
        }

    async def close(self):
        for lease in self.leases.values():
            for turn in lease.voice_turns.values():
                self._release_turn(turn)
            lease.voice_turns.clear()
        for job in self.jobs.values():
            self._cancel_job(job, "cancelled", "compute_closed")
        self.closed = True
        if self.worker:
            with suppress(asyncio.CancelledError):
                await self.worker
        self.jobs.clear()
        self.leases.clear()
        if self._cleanups:
            await asyncio.gather(*self._cleanups, return_exceptions=True)
        close = getattr(self.engines, "close", None)
        if close:
            await close()
