"""Reconcile host model lifecycle with the authoritative Pi selection."""

from __future__ import annotations

import asyncio

from .analysis_profiles import ProfileCatalog
from .errors import RiderError
from .model_resources import model_operation
from .perception_catalog import SOURCES, selected_identity
from .perception_install import ModelInstaller
from .perception_runtime import ModelRuntime


class PerceptionService:
    def __init__(self, loop, *, catalog=None, installer=None):
        self.loop = loop
        self.catalog = catalog or ProfileCatalog().live()
        self.runtime = ModelRuntime(self.catalog)
        self.installer = installer or ModelInstaller(
            self.catalog, priority=loop.priority
        )
        self.lock = asyncio.Lock()
        self.switching = False
        loop.models = self

    def observe_policy(self, device, policy):
        """A newer Pi policy can change geometry without changing model weights."""
        previous = self.runtime.expected.get(device)
        if previous and policy["revision"] < previous["revision"]:
            return
        if (policy.get("perception") or {}).get("model_id"):
            self.runtime.expected[device] = selected_identity(policy)
        else:
            self.runtime.expected.pop(device, None)

    async def snapshot(self, domain):
        selection, selection_error = None, None
        perception = None
        try:
            policy = (await domain.read("/api/navigation/config", 0.2, force=True))[
                "data"
            ]
            selection = selected_identity(policy)
            perception = policy.get("perception")
            self.observe_policy(domain.transport().device.id, policy)
        except RiderError as exc:
            selection_error = exc.code
        rows = await asyncio.to_thread(
            lambda: [self.catalog.describe(identity) for identity in SOURCES]
        )
        runtime = self.runtime.status()
        if selection and not (perception or {}).get("model_id"):
            legacy = self.loop.status(perception)
            if legacy.get("loaded") is True:
                runtime = {
                    "state": "active",
                    "active_model_id": selection["model_id"],
                    "model_generation": 0,
                    "execution_provider": legacy.get("device", "cpu"),
                }
        for row in rows:
            row["loaded"] = (
                row["id"] == runtime["active_model_id"] and runtime["state"] == "active"
            )
            job = self.installer.status(row["id"])
            row["job"] = job
            if job and job["state"] in {
                "waiting",
                "downloading",
                "verifying",
                "cancelling",
            }:
                row.update(selectable=False)
                if job["state"] in {"downloading", "verifying"}:
                    row["installation"] = job["state"]
            elif job and job["state"] == "failed" and row["installation"] != "ready":
                row.update(installation="failed", reason=job["error"])
        pipeline = {}
        try:
            pipeline = await domain.pipeline()
        except RiderError as exc:
            selection_error = selection_error or exc.code
        provider = (pipeline.get("profile") or {}).get("analysis_location")
        local_models, local_error = [], None
        try:
            local = (await domain.read("/api/navigation/local-models", 2.0))["data"]
            local_models = local.get("models", [])
        except RiderError as exc:
            local_error = exc.code
        return {
            "models": rows,
            "selection": selection,
            "selection_error": selection_error,
            "switching": self.switching,
            "diagnostics": {**self.runtime.diagnostics(), "runtime": runtime},
            "providers": [
                {"id": "venom", "label": "Venom", "available": True},
                {
                    "id": "local",
                    "label": "Rider-Pi",
                    "available": "local"
                    in pipeline.get("capabilities", {}).get("analysis_locations", []),
                },
            ],
            "provider": provider,
            "local_engine": (pipeline.get("profile") or {}).get("local_engine"),
            "pipeline_revision": pipeline.get("revision"),
            "local_models": local_models,
            "local_models_error": local_error,
        }

    @model_operation
    async def select(self, domain, body, session):
        domain.check_session(session)
        if (
            not isinstance(body, dict)
            or set(body) != {"revision", "model_id"}
            or type(body["revision"]) is not int
            or body["revision"] < 0
            or not isinstance(body["model_id"], str)
        ):
            raise RiderError("model_selection_invalid", 422)
        if self.lock.locked():
            raise RiderError("model_busy", 409)
        async with self.lock:
            self.switching = True
            try:
                artifact = await asyncio.to_thread(self.catalog.ready, body["model_id"])
                pipeline = await domain.pipeline(force=True)
                if (pipeline.get("profile") or {}).get(
                    "analysis_location", "venom"
                ) != "venom":
                    raise RiderError("vision_remote_disabled", 409)
                device = domain.transport().device.id
                epoch = domain.epoch
                # Shield admitted native inference. A timeout does not free its memory.
                busy = [
                    task
                    for task in (self.loop.busy, self.loop.depth_busy)
                    if task is not None
                ]
                if busy:
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(
                                *(asyncio.shield(task) for task in busy),
                                return_exceptions=True,
                            ),
                            10,
                        )
                    except TimeoutError as exc:
                        raise RiderError("model_busy", 409) from exc
                if domain.epoch != epoch:
                    raise RiderError("stale_device_session", 409)
                expected = {
                    **body,
                    "artifact_sha256": artifact["sha256"],
                    "engine": self.catalog.definition(body["model_id"])["engine"],
                }
                try:
                    result = await domain.mutate(
                        "/api/navigation/model-selection", expected, session
                    )
                    payload = result["data"]
                    if (
                        not isinstance(payload, dict)
                        or payload.get("ok") is not True
                        or payload.get("persisted") is not True
                        or not isinstance(payload.get("config"), dict)
                    ):
                        raise RiderError("model_selection_invalid_response", 502)
                    config = payload["config"]
                except RiderError:
                    # The commit may have succeeded before an ACK was lost.
                    config = (
                        await domain.read("/api/navigation/config", 0.0, force=True)
                    )["data"]
                    selected = selected_identity(config)
                    if not (
                        config["revision"] == body["revision"] + 1
                        and selected["model_id"] == body["model_id"]
                        and selected["artifact_sha256"] == artifact["sha256"]
                        and config.get("mode") == "observe"
                        and config.get("verified") is False
                        and not config.get("calibration_id")
                        and not config.get("model_sha256")
                    ):
                        raise
                domain.check_session(session)
                selected = selected_identity(config)
                if (
                    selected["model_id"] != expected["model_id"]
                    or selected["artifact_sha256"] != expected["artifact_sha256"]
                ):
                    raise RiderError("model_selection_conflict", 409)
                self.runtime.expected[device] = selected
                # Retire all prior publications before the new generation can run.
                self.loop.previews.clear()
                self.loop.inference_cache.clear()
                self.loop.runs.clear()
                self.runtime.reset()
                self.loop.detector = type(self.loop.detector)()
                from .depth_detector import DepthDetector

                self.loop.depth_detector = DepthDetector()
                from .depth_detector import PerceptionSettings

                self.runtime.state = "loading"
                warmup = asyncio.create_task(
                    self.loop.workers.run(
                        self.runtime.warm,
                        PerceptionSettings.model_validate(config["perception"]),
                    )
                )
                if expected["engine"] == "depth_plane":
                    self.loop.depth_busy = warmup
                else:
                    self.loop.busy = warmup
                warmup.add_done_callback(self.loop._drain)
                return {"selection": selected, "config": config}
            finally:
                self.switching = False

    @model_operation
    async def prepare_observation(self, domain, session):
        """Explicit analysis may reload the current model without editing Pi policy."""
        domain.check_session(session)
        if self.lock.locked():
            raise RiderError("model_busy", 409)
        async with self.lock:
            pipeline = await domain.pipeline(force=True)
            if (pipeline.get("profile") or {}).get("analysis_location") != "venom":
                return
            policy = (await domain.read("/api/navigation/config", 0, force=True))[
                "data"
            ]
            perception = policy.get("perception") or {}
            if not perception.get("model_id"):
                return
            device, epoch = domain.transport().device.id, domain.epoch
            selected = selected_identity(policy)
            artifact = await asyncio.to_thread(self.catalog.ready, selected["model_id"])
            if artifact["sha256"] != selected["artifact_sha256"]:
                raise RiderError("model_hash_mismatch", 409)
            self.observe_policy(device, policy)
            if self.runtime.state == "active" and self.runtime.identity == (
                selected["model_id"],
                artifact["sha256"],
            ):
                return
            if any(
                task is not None and not task.done()
                for task in (self.loop.busy, self.loop.depth_busy)
            ):
                raise RiderError("model_busy", 409)
            self.switching = True
            try:
                from .depth_detector import PerceptionSettings

                self.runtime.state = "loading"
                warmup = asyncio.create_task(
                    self.loop.workers.run(
                        self.runtime.warm, PerceptionSettings.model_validate(perception)
                    )
                )
                if perception["engine"] == "depth_plane":
                    self.loop.depth_busy = warmup
                else:
                    self.loop.busy = warmup
                warmup.add_done_callback(self.loop._drain)
                # Cancellation cannot free a native model still loading in its thread.
                await asyncio.shield(warmup)
                domain.check_session(session)
                if domain.epoch != epoch or domain.transport().device.id != device:
                    raise RiderError("stale_device_session", 409)
                current = (await domain.read("/api/navigation/config", 0, force=True))[
                    "data"
                ]
                if selected_identity(current) != selected:
                    raise RiderError("model_selection_conflict", 409)
            finally:
                self.switching = False

    async def close(self):
        await self.installer.close()
