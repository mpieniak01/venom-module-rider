"""One cancellable host installation job, admitted by Venom's shared task queue."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from .errors import RiderError


class ModelInstaller:
    def __init__(self, catalog, *, priority=None, host_tasks=None):
        self.catalog = catalog
        self.priority = priority
        self.host_tasks = host_tasks
        self.jobs = {}
        self.handles = {}
        self.children = {}
        self.closed = False

    def status(self, model_id):
        job = self.jobs.get(model_id)
        return dict(job) if job else None

    def submit(self, model_id):
        row = self.catalog.describe(model_id)
        if row["installation"] == "needs_review":
            raise RiderError("model_needs_review", 409)
        if self.closed:
            raise RiderError("module_closed", 503)
        active = next(
            (
                job
                for job in self.jobs.values()
                if job["state"] in {"waiting", "downloading", "verifying"}
            ),
            None,
        )
        if active:
            if active["model_id"] == model_id:
                return dict(active)
            raise RiderError("model_install_busy", 429)
        job = {
            "id": uuid4().hex,
            "model_id": model_id,
            "state": "waiting",
            "progress": 0,
            "error": None,
        }
        self.jobs[model_id] = job
        handle = asyncio.create_task(self._run(job))
        self.handles[job["id"]] = handle
        return dict(job)

    def cancel(self, model_id):
        self.catalog.require(model_id)
        job = self.jobs.get(model_id)
        if job and job["state"] in {"waiting", "downloading", "verifying"}:
            job["state"] = "cancelling"
            self.handles[job["id"]].cancel()
        return dict(job) if job else None

    async def _run(self, job):
        host_id, tasks, child = None, None, None
        try:
            if self.host_tasks is None:
                from .analysis_tasks import HostTasks

                self.host_tasks = HostTasks()
            tasks = self.host_tasks
            host_id = tasks.create(
                {
                    "id": job["id"],
                    "session_id": "perception-models",
                    "kind": "perception-install",
                }
            )
            job["task_id"] = str(host_id)
            while not self.closed:
                available = self.priority is None or self.priority.available()
                if available and await tasks.admit(host_id, asyncio.current_task()):
                    break
                await asyncio.sleep(0.5)
            if self.closed:
                raise asyncio.CancelledError
            environment = dict(os.environ)
            environment.update(
                CUDA_VISIBLE_DEVICES="",
                HF_HUB_DISABLE_PROGRESS_BARS="1",
                TOKENIZERS_PARALLELISM="false",
                OMP_NUM_THREADS="2",
                MKL_NUM_THREADS="2",
                OPENBLAS_NUM_THREADS="2",
            )
            root = Path(__file__).resolve().parent.parent
            environment["PYTHONPATH"] = (
                str(root) + os.pathsep + environment.get("PYTHONPATH", "")
            )
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "venom_module_rider.perception_worker",
                job["model_id"],
                cwd=root,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=4096,
            )
            self.children[job["id"]] = child
            async with asyncio.timeout(1200):
                await self._observe(child, job)
            if child.returncode != 0 or job["state"] != "ready":
                raise RiderError("model_install_failed", 409)
        except asyncio.CancelledError:
            job.update(state="cancelled", error=None)
        except RiderError as exc:
            job.update(state="failed", error=exc.code)
        except Exception:
            job.update(state="failed", error="model_install_failed")
        finally:
            if child is not None and child.returncode is None:
                child.terminate()
                try:
                    await asyncio.wait_for(child.wait(), 5)
                except TimeoutError:
                    child.kill()
                    await child.wait()
            self.children.pop(job["id"], None)
            if tasks is not None and host_id is not None:
                await tasks.finish(
                    host_id,
                    "succeeded" if job["state"] == "ready" else "failed",
                    job["error"] or job["state"],
                )
            self.handles.pop(job["id"], None)

    async def _observe(self, child, job):
        assert child.stdout is not None
        while True:
            try:
                line = await asyncio.wait_for(child.stdout.readline(), 0.5)
            except TimeoutError:
                # The worker is bounded by the same RAM policy as offline replay.
                rss = Path(f"/proc/{child.pid}/status")
                if rss.exists():
                    for row in rss.read_text().splitlines():
                        if (
                            row.startswith("VmRSS:")
                            and int(row.split()[1]) > 2560 * 1024
                        ):
                            raise RiderError("model_resource_limit", 409) from None
                continue
            if not line:
                break
            message = json.loads(line)
            if message.get("state") not in {"downloading", "verifying", "ready"}:
                raise RiderError("model_install_protocol", 409)
            progress = message.get("progress")
            if type(progress) is not int or not 0 <= progress <= 100:
                raise RiderError("model_install_protocol", 409)
            job.update(state=message["state"], progress=progress)
        await child.wait()

    async def close(self):
        self.closed = True
        handles = list(self.handles.values())
        for handle in handles:
            handle.cancel()
        for handle in handles:
            with suppress(asyncio.CancelledError):
                await handle
