"""Reconcile host model lifecycle with the authoritative Pi selection."""

from __future__ import annotations

import asyncio

from .analysis_profiles import ProfileCatalog
from .errors import RiderError
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

    async def snapshot(self, domain):
        policy = (await domain.read("/api/navigation/config", 0.2, force=True))["data"]
        selection = selected_identity(policy)
        rows = await asyncio.to_thread(
            lambda: [self.catalog.describe(identity) for identity in SOURCES]
        )
        runtime = self.runtime.status()
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
        pipeline = await domain.pipeline()
        provider = (pipeline.get("profile") or {}).get("analysis_location", "venom")
        local_models, local_error = [], None
        try:
            local = (await domain.read("/api/navigation/local-models", 2.0))["data"]
            local_models = local.get("models", [])
        except RiderError as exc:
            local_error = exc.code
        return {
            "models": rows,
            "selection": selection,
            "switching": self.switching,
            "diagnostics": self.runtime.diagnostics(),
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
                    config = result["data"]
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
                return {"selection": selected, "config": config}
            finally:
                self.switching = False

    async def close(self):
        await self.installer.close()
