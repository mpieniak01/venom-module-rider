"""Bounded offline work on the host, sharing data IO and yielding to live vision."""

from __future__ import annotations

import asyncio
import base64
import fcntl
import json
import os
import time
from contextlib import suppress
from pathlib import Path

from .analysis_priority import AnalysisPriority
from .analysis_profiles import ProfileCatalog, code_fingerprint
from .analysis_store import MAX_ARTIFACT_BYTES, AnalysisStore
from .errors import RiderError
from .samples_archive import canonical

MAX_MESSAGE = 16 * 1024**2
MAX_RSS = 2560 * 1024**2


class AnalysisManager:
    def __init__(self, registry, *, priority=None, catalog=None, host_tasks=None):
        self.registry = registry
        self.store = AnalysisStore(registry.store)
        self.priority = priority or AnalysisPriority()
        self.catalog = catalog or ProfileCatalog()
        self.host_tasks = host_tasks
        self.operations = asyncio.Lock()
        self.slot = asyncio.Semaphore(1)
        self.tasks = {}
        self.children = {}
        self.started = set()
        self.admissions = set()
        self.failures = 0
        self.resource_failures = set()
        self.recovered = False
        self.lease = None
        self.closed = False

    async def io(self, call, *args, **kwargs):
        # The single writer bounds RAW and derived writes together. Waiting here
        # never occupies the live control event loop or a second executor.
        while True:
            try:
                return await self.registry.read(call, *args, **kwargs)
            except RiderError as exc:
                if exc.code != "samples_queue_full":
                    raise
                await asyncio.sleep(0.02)

    def _own(self):
        if self.lease is not None:
            return False
        path = self.registry.store.root / "analysis-executor.lock"
        if path.is_symlink():
            raise RiderError("samples_root_invalid", 400)
        stream = path.open("a+b")
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            stream.close()
            raise RiderError("analysis_executor_busy", 409) from exc
        self.lease = stream
        return True

    async def initialize(self):
        """Mark lost executions at startup, without starting any new work."""
        async with self.operations:
            try:
                self._own()
                await self.registry.reserve_writer("analysis-recovery")
            except RiderError as exc:
                if exc.code not in {"analysis_executor_busy", "samples_registry_busy"}:
                    raise
                return False
            try:
                await self.io(self.store.recover)
                self.recovered = True
            finally:
                await self.registry.release_writer("analysis-recovery")
        return True

    async def submit(self, request_id, session_id, spec):
        if self.closed:
            raise RiderError("analysis_closed", 503)
        if len(self.admissions) >= 4:
            raise RiderError("analysis_queue_full", 429)
        handle = asyncio.create_task(self._submit(request_id, session_id, spec))
        self.admissions.add(handle)

        def observed(task):
            self.admissions.discard(task)
            if not task.cancelled():
                task.exception()  # HTTP cancellation must not orphan admission errors

        handle.add_done_callback(observed)
        return await asyncio.shield(handle)

    async def _submit(self, request_id, session_id, spec):
        async with self.operations:
            if self.closed:
                raise RiderError("analysis_closed", 503)
            # Recover the exact previous intent before checking current assets.
            try:
                old = await self.io(self.store.by_request, request_id)
            except RiderError as exc:
                if exc.code != "analysis_not_found":
                    raise
            else:
                previous = await self.io(self.store.get, old["id"])
                if (
                    previous["session_id"] != session_id
                    or previous["context"]["spec"] != spec
                ):
                    raise RiderError("analysis_request_conflict", 409)
                return old
            if len(self.tasks) >= 4:
                raise RiderError("analysis_queue_full", 429)
            if self.host_tasks is None:
                from .analysis_tasks import HostTasks

                try:
                    self.host_tasks = HostTasks()
                except RuntimeError as exc:
                    raise RiderError("analysis_host_unavailable", 503) from exc
            profile = self.catalog.resolve(spec["profile"])
            if spec["window"] not in profile.describe()["windows"]:
                raise RiderError("analysis_window_unsupported", 400)
            qualified = await self.io(profile.qualify)
            await self.registry.reserve_writer("analysis-submit")
            try:
                self._own()
                if not self.recovered:
                    await self.io(self.store.recover)
                    self.recovered = True
                row = await self.io(
                    self.store.create,
                    request_id,
                    session_id,
                    spec,
                    qualified,
                    await self.io(code_fingerprint),
                )
                identity = row["id"]
                await self.registry.reserve_writer("analysis-" + identity)
                try:
                    task_id = self.host_tasks.create(row)
                    await self.io(self.store.bind_task, identity, str(task_id))
                    handle = asyncio.create_task(self._execute(row, profile, task_id))
                    self.tasks[identity] = handle
                    handle.add_done_callback(
                        lambda task: self.tasks.pop(identity, None)
                    )
                except Exception:
                    await self.io(
                        self.store.finish,
                        identity,
                        "failed",
                        error="analysis_host_unavailable",
                    )
                    await self.registry.release_writer("analysis-" + identity)
                    raise
                return await self.io(self.store.get, identity, context=False)
            finally:
                await self.registry.release_writer("analysis-submit")

    async def cancel(self, identity):
        row = await self.io(self.store.cancel, identity)
        task = self.tasks.get(identity)
        if task is not None and identity in self.started:
            task.cancel()
        return {key: value for key, value in row.items() if key != "context"}

    async def _execute(self, row, profile, task_id):
        identity, state, error = row["id"], "failed", None
        self.started.add(identity)
        process = None
        started = time.monotonic()
        registered = False
        try:
            async with asyncio.timeout(900):
                async with self.slot:
                    while True:
                        if self.host_tasks.abandoned(task_id):
                            raise asyncio.CancelledError()
                        status = await self.io(self.store.get, identity, context=False)
                        if status["cancel_requested"]:
                            raise asyncio.CancelledError()
                        if self.priority.available() and await self.host_tasks.admit(
                            task_id, asyncio.current_task()
                        ):
                            registered = True
                            break
                        await self.io(
                            self.store.progress,
                            identity,
                            0,
                            "waiting_live"
                            if not self.priority.available()
                            else "waiting_capacity",
                        )
                        await asyncio.sleep(0.25)
                    row["queue_ms"] = max(
                        0, (self.store.samples.wall() - row["created"]) * 1000
                    )
                    if not await self.io(self.store.running, identity):
                        raise asyncio.CancelledError()
                    env = {
                        "PATH": str(profile.python.parent) + ":/usr/bin:/bin",
                        "HOME": str(Path.home()),
                        "LANG": "C.UTF-8",
                        "PYTHONPATH": str(self.catalog.module)
                        + (os.pathsep + str(profile.sdk) if profile.sdk else ""),
                        **(
                            {"CUDA_VISIBLE_DEVICES": os.environ["CUDA_VISIBLE_DEVICES"]}
                            if "CUDA_VISIBLE_DEVICES" in os.environ
                            else {}
                        ),
                        "HF_HUB_OFFLINE": "1",
                        "TRANSFORMERS_OFFLINE": "1",
                        "HF_HUB_DISABLE_TELEMETRY": "1",
                        "OMP_NUM_THREADS": str(row["context"]["spec"]["threads"]),
                        "MKL_NUM_THREADS": str(row["context"]["spec"]["threads"]),
                        "OPENBLAS_NUM_THREADS": str(row["context"]["spec"]["threads"]),
                        "TOKENIZERS_PARALLELISM": "false",
                        "PYTHONDONTWRITEBYTECODE": "1",
                    }
                    process = await asyncio.create_subprocess_exec(
                        str(profile.python),
                        "-u",
                        "-m",
                        "venom_module_rider.analysis_worker",
                        str(os.getpid()),
                        cwd=self.catalog.module,
                        env=env,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                        limit=MAX_MESSAGE + 1,
                    )
                    self.children[identity] = process
                    self.priority.attach(process)
                    job = {
                        "id": identity,
                        "root": str(self.registry.store.root),
                        "context": row["context"],
                        "profile": {
                            "backend": profile.backend,
                            "model": str(profile.model),
                            "resolution": profile.resolution,
                        },
                    }
                    await self._send(process, job)
                    monitor = asyncio.create_task(self._monitor(process))
                    try:
                        await self._protocol(process, row, started)
                        if await process.wait() != 0:
                            raise RiderError("analysis_worker_failed", 500)
                    finally:
                        monitor.cancel()
                        with suppress(asyncio.CancelledError):
                            await monitor
                    state = "succeeded"
        except asyncio.CancelledError:
            state, error = "cancelled", "analysis_cancelled"
        except TimeoutError:
            error = "analysis_timeout"
        except Exception as exc:
            error = (
                exc.code if isinstance(exc, RiderError) else "analysis_worker_failed"
            )
        finally:
            if process is not None:
                if self.priority.process is process:
                    self.priority.detach(process)
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.kill()
                await process.wait()
                self.children.pop(identity, None)
                if process.pid in self.resource_failures:
                    self.resource_failures.discard(process.pid)
                    state, error = "failed", "analysis_resource_limit"
            # Terminal means the OS process really stopped and all admitted IO
            # is ordered before this final durable transition.
            try:
                await self.io(self.store.finish, identity, state, error=error)
            except Exception:
                self.failures += 1
                state, error = "failed", "analysis_finalize_failed"
                with suppress(Exception):
                    await self.io(self.store.finish, identity, state, error=error)
            finally:
                try:
                    if registered or self.host_tasks is not None:
                        await self.host_tasks.finish(task_id, state, error)
                finally:
                    self.started.discard(identity)
                    await self.registry.release_writer("analysis-" + identity)

    async def _send(self, process, message):
        encoded = await self.io(canonical, message)
        if len(encoded) > MAX_MESSAGE:
            raise RiderError("analysis_protocol_size", 413)
        process.stdin.write(encoded + b"\n")
        await process.stdin.drain()

    async def _monitor(self, process):
        while process.returncode is None:
            try:
                lines = Path(f"/proc/{process.pid}/status").read_text().splitlines()
                rss = next(
                    int(line.split()[1]) * 1024
                    for line in lines
                    if line.startswith("VmRSS:")
                )
                free = next(
                    int(line.split()[1]) * 1024
                    for line in Path("/proc/meminfo").read_text().splitlines()
                    if line.startswith("MemAvailable:")
                )
                if rss > MAX_RSS or free < 512 * 1024**2:
                    self.resource_failures.add(process.pid)
                    process.kill()
                    return
                if self.priority.paused and self.priority.available():
                    self.priority.phase_compute(process)
            except (OSError, StopIteration):
                pass
            await asyncio.sleep(0.1)

    async def _protocol(self, process, row, started):
        count = 0
        while True:
            raw = await process.stdout.readline()
            if not raw or len(raw) > MAX_MESSAGE:
                raise RiderError("analysis_worker_protocol", 500)
            # Parse and decode large payloads on the SAME IO executor as captures.
            message = await self.io(json.loads, raw)
            kind = message.get("type")
            if kind == "compute":
                self.priority.phase_io(process)
                while not self.priority.phase_compute(process):
                    await self.io(self.store.progress, row["id"], count, "waiting_live")
                    await asyncio.sleep(0.1)
                phase = message.get("phase", "inference")
                if phase not in {"loading", "decoding", "inference", "geometry"}:
                    raise RiderError("analysis_worker_protocol", 500)
                await self.io(self.store.progress, row["id"], count, phase)
                await self._send(process, {"go": True})
                continue
            self.priority.phase_io(process)
            if kind == "error":
                await self._send(process, {})
                raise RiderError(message.get("error", "analysis_worker_failed"), 500)
            if kind == "frame":
                if message.get("done") != count + 1 or count >= row["total"]:
                    raise RiderError("analysis_worker_protocol", 500)
                sample = row["context"]["inputs"][count]
                result = message["row"]
                if (
                    result["sample_id"] != sample["sample_id"]
                    or result["raw_sha256"] != sample["raw"]["sha256"]
                ):
                    raise RiderError("analysis_worker_protocol", 500)
                await self.io(self.store.progress, row["id"], count, "saving")
                timing = await self.io(self._save_frame, row["id"], message)
                count += 1
                await self.io(self.store.progress, row["id"], count, "saving")
                await self._send(process, {"timings_ms": timing})
            elif kind == "complete":
                if count != row["total"]:
                    raise RiderError("analysis_report_incomplete", 409)
                report = message["report"]
                report["admission_to_report_ms"] = (time.monotonic() - started) * 1000
                report["queue_ms"] = row["queue_ms"]
                await self.io(
                    self.store.write,
                    row["id"],
                    "report.json",
                    await self.io(canonical, report),
                    "application/json",
                )
                await self._send(process, {})
                return
            else:
                raise RiderError("analysis_worker_protocol", 500)

    def _save_frame(self, identity, message):
        started = time.perf_counter()
        ipc_ms = max(0, (started - message["sent_mono"]) * 1000)
        artifacts = message.get("artifacts", [])
        if len(artifacts) > 3:
            raise RiderError("analysis_worker_protocol", 500)
        for item in artifacts:
            data = item["data"]
            if not isinstance(data, str) or len(data) > (
                MAX_ARTIFACT_BYTES * 4 // 3 + 8
            ):
                raise RiderError("analysis_artifact_size", 413)
            self.store.write(
                identity,
                item["name"],
                base64.b64decode(data, validate=True),
                item["mime"],
            )
        return {
            "persistence": (time.perf_counter() - started) * 1000,
            "local_ipc_and_queue": ipc_ms,
        }

    async def close(self):
        self.closed = True
        if self.admissions:
            await asyncio.gather(*list(self.admissions), return_exceptions=True)
        await asyncio.sleep(0)  # let newly scheduled handles enter their cleanup scope
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.lease is not None:
            fcntl.flock(self.lease, fcntl.LOCK_UN)
            self.lease.close()
            self.lease = None
