"""Opt-in host-wide memory release; no capture or motion starts on reload."""

import asyncio

try:
    from venom_core.services.model_resources import (
        model_operation,
        register_model_resource,
    )
except ImportError:  # Older supported Venom cores have no global release UI.

    def model_operation(function):
        return function

    def register_model_resource(*_args):
        pass


class RiderModelResources:
    def __init__(self, app):
        self.app = app
        self.prepared = False
        register_model_resource(self, "vision", "release_model_resource")

    async def prepare_model_release(self):
        loop = self.app.state.rider_vision
        loop.resources_suspended = True
        domain = self.app.state.rider
        runtime = getattr(
            getattr(self.app.state, "rider_models", None), "runtime", None
        )
        loaded = (
            getattr(runtime, "detector", None) is not None
            or getattr(getattr(loop, "depth_detector", None), "model", None) is not None
            or getattr(getattr(loop, "detector", None), "session", None) is not None
            or getattr(loop, "busy", None) is not None
            or getattr(loop, "depth_busy", None) is not None
        )
        # Cold host vision owns no model or in-flight decisions. An unrelated
        # offline Pi must not prevent releasing the host's voice/LLM resources.
        if domain.device is not None and loaded:
            safety = (await domain.read("/api/control/state", 0, force=True))["data"]
            safety = safety.get("safety", safety)
            if safety.get("can_move") or safety.get("state") == "ARMED":
                await domain.control({"action": "disarm"}, domain.session)
                safety = (await domain.read("/api/control/state", 0, force=True))[
                    "data"
                ]
                safety = safety.get("safety", safety)
                if safety.get("can_move") or safety.get("state") == "ARMED":
                    raise RuntimeError("vision_release_disarm_unconfirmed")
            pipeline = await domain.pipeline(force=True)
            if pipeline.get("effective_stages", {}).get("capture"):
                for goal in ("s14_venom_navigation", "s15_venom_perception"):
                    await domain.goal(goal, {"enabled": False}, domain.session)
                await domain.stop_camera({"action": "stop"}, domain.session)
        analysis = getattr(self.app.state, "rider_analysis", None)
        if analysis is not None:
            tasks = list(analysis.tasks.items())
            for identity, _task in tasks:
                await analysis.cancel(identity)
            if tasks:
                await asyncio.gather(
                    *(task for _, task in tasks), return_exceptions=True
                )
        pending = [task for task in (loop.busy, loop.depth_busy) if task is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self.prepared = True

    def release_model_resource(self):
        if not self.prepared:
            return False
        loop = self.app.state.rider_vision
        self.app.state.rider_models.runtime.reset()
        loop.depth_detector.model = None
        loop.depth_detector.processor = None
        loop.depth_detector.device = None
        loop.detector.session = None
        loop.previews.clear()
        loop.inference_cache.clear()
        loop.runs.clear()
        self.prepared = False
        return True
