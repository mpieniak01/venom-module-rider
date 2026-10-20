"""Independent admission and decision lane, bounded and default STOP."""

import asyncio
import copy
import time
from collections import OrderedDict
from uuid import uuid4

from . import depth_trace
from .avoidance import plan
from .depth_detector import DepthDetector
from .errors import RiderError
from .floor_detector import FloorDetector
from .journey import planner_history, record_result
from .sector_trend import update as sector_trend
from .steering import plan as steering_plan
from .vision_overlay import preview_sectors
from .workers import VisionWorkers


class VisionLoop:
    def __init__(
        self,
        detector=None,
        clock=time.monotonic,
        *,
        samples=None,
        source_session=None,
        priority=None,
    ):
        self.host_session = uuid4().hex
        self.priority = priority
        self.priority_errors = 0
        self.samples = samples
        self.source_session = source_session
        self.detector = detector or FloorDetector()
        self.depth_detector = DepthDetector()
        self.clock = clock
        self.busy = None
        self.depth_busy = None
        self.runs = OrderedDict()
        self.closed = False
        self.previews = OrderedDict()
        self.preview_changed = asyncio.Event()
        self.snapshot_tasks = {}
        self.inference_cache = OrderedDict()
        self.diagnostic_inferences = set()
        self.production_waiters = {False: 0, True: 0}
        self.workers = VisionWorkers("rider-vision")

    def _detect_depth(self, payload, frame):
        if frame.protocol_version == 3 and frame.avoidance.geometry.mode == "ground":
            return self.depth_detector.detect_ground(
                payload, frame.corridor, frame.perception, frame.avoidance.geometry
            )
        return self.depth_detector.detect(payload, frame.corridor, frame.perception)

    def status(self, perception=None):
        selected = (
            self.depth_detector
            if perception and perception.get("engine") == "depth_plane"
            else self.detector
        )
        return {
            **selected.status(),
            "busy": (self.depth_busy if selected is self.depth_detector else self.busy)
            is not None,
            "supported_protocol_versions": [1, 2, 3],
            "navigation_journey_version": 1,
            "ground_geometry_supported": callable(
                getattr(self.depth_detector, "detect_ground", None)
            ),
            "protocol_version": 1,
            "max_frame_bytes": 512 * 1024,
            "offline_priority_errors": self.priority_errors,
            "max_deadline_ms": 500,
            "clear_frames_required": 3,
            "physical_clearance": "unverified",
        }

    def _inference_key(self, device_id, frame, source_key):
        if source_key is None:
            return None
        return (
            device_id,
            source_key,
            frame.config_revision,
            frame.corridor,
            frame.perception.model_dump_json(),
            (
                frame.avoidance.geometry.model_dump_json()
                if frame.protocol_version == 3
                else None
            ),
        )

    def _cached_inference(self, key):
        now = self.clock()
        for identity, (expires, _task) in tuple(self.inference_cache.items()):
            if now >= expires:
                self.inference_cache.pop(identity, None)
        row = self.inference_cache.get(key) if key is not None else None
        return row[1] if row is not None else None

    async def _admit_inference(self, device_id, frame, source_key, diagnostic):
        depth = frame.perception.engine == "depth_plane"
        key = self._inference_key(device_id, frame, source_key)
        cached = self._cached_inference(key)
        busy = self.depth_busy if depth else self.busy
        if cached is not None:
            return frame, cached
        if self.production_waiters[depth]:
            raise RiderError("vision_busy", 429)
        if busy is None:
            return frame, None
        if diagnostic or busy not in self.diagnostic_inferences:
            raise RiderError("vision_busy", 429)
        # A Pi handoff may finish the one already-running preview, but never
        # queues captures or lets another preview jump ahead. Waiting consumes
        # this request's original capture budget, just like model processing.
        self.production_waiters[depth] += 1
        started = self.clock()
        try:
            try:
                await asyncio.wait_for(
                    asyncio.shield(busy), (frame.deadline_ms - frame.age_ms) / 1000
                )
            except TimeoutError as exc:
                raise RiderError("vision_frame_expired", 409) from exc
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failed diagnostic does not poison the next current source.
                pass
        finally:
            self.production_waiters[depth] -= 1
        frame = frame.model_copy(
            update={
                "age_ms": frame.age_ms + max(0.0, self.clock() - started) * 1000,
            }
        )
        if busy.done():
            self._drain(busy)
        if self.closed:
            raise RiderError("module_closed", 503)
        if frame.age_ms >= frame.deadline_ms:
            raise RiderError("vision_frame_expired", 409)
        if (self.depth_busy if depth else self.busy) is not None:
            raise RiderError("vision_busy", 429)
        return frame, self._cached_inference(key)

    def _start_inference(self, key, frame, payload, *, diagnostic=False):
        depth_mode = frame.perception.engine == "depth_plane"
        task = asyncio.create_task(
            self.workers.run(
                self._detect_depth if depth_mode else self.detector.detect,
                payload,
                frame if depth_mode else frame.corridor,
            )
        )
        if depth_mode:
            self.depth_busy = task
        else:
            self.busy = task
        if diagnostic:
            self.diagnostic_inferences.add(task)
        task.add_done_callback(self._drain)
        if key is not None:
            # The first receipt fixes expiry. Reusing a result cannot renew it.
            self.inference_cache[key] = (
                self.clock() + (frame.deadline_ms - frame.age_ms) / 1000,
                task,
            )
            self.inference_cache.move_to_end(key)
            while len(self.inference_cache) > 8:
                self.inference_cache.popitem(last=False)
        return task

    async def decide(
        self,
        device_id,
        frame,
        payload,
        *,
        with_preview=False,
        input_ticket=None,
        source_key=None,
        source_reference=None,
    ):
        if self.closed:
            raise RiderError("module_closed", 503)
        if not frame.valid_corridor() or frame.age_ms >= frame.deadline_ms:
            raise RiderError("vision_frame_expired", 409)
        depth_mode = frame.perception.engine == "depth_plane"
        # The built-in simulator cannot reserve an engine recently used by Pi.
        if device_id == "demo" and any(
            identity[0] != "demo"
            and row.get("engine") == frame.perception.engine
            and self.clock() - row["at"] <= 0.5
            for identity, row in self.runs.items()
        ):
            raise RiderError("vision_production_priority", 429)
        frame, cached_task = await self._admit_inference(
            device_id, frame, source_key, with_preview
        )
        key = self._inference_key(device_id, frame, source_key)
        identity = (device_id, frame.run_id)
        previous = self.runs.get(identity)
        signature = (
            frame.config_revision,
            frame.corridor,
            frame.mode,
            frame.verified,
            frame.calibration_id,
            frame.model_sha256,
            frame.perception.model_dump_json(),
            frame.protocol_version,
            frame.avoidance.model_dump_json(),
            frame.journey.id if frame.journey else None,
            (
                source_reference.get("camera_session"),
                source_reference.get("transport_epoch"),
                source_reference.get("pipeline_revision"),
            )
            if source_reference
            else None,
        )
        if previous and (
            frame.sequence <= previous["sequence"]
            or frame.frame_id == previous["frame"]
            or previous["signature"] != signature
        ):
            raise RiderError("vision_replay_or_configuration_changed", 409)
        prior_planner = planner_history(frame, previous)
        now = self.clock()
        count = previous["count"] if previous and now - previous["at"] <= 0.5 else 0
        # Reserve sequence before await: duplicates cannot race admission.
        state = {
            "sequence": frame.sequence,
            "frame": frame.frame_id,
            "signature": signature,
            "count": 0,
            "at": now,
            "engine": frame.perception.engine,
        }
        if frame.journey is not None:
            # An interrupted inference cannot leave an incomplete authority
            # record or silently restore a maneuver budget on the next request.
            state.update(
                journey_epoch=frame.journey.maneuver_epoch,
                journey_terminal=True,
                journey_completed=False,
            )
        self.runs[identity] = state
        self.runs.move_to_end(identity)
        while len(self.runs) > 32:
            self.runs.popitem(last=False)
        if self.priority is not None:
            try:
                self.priority.live()
            except Exception:
                # Optional offline scheduling must never break live decisions.
                self.priority_errors += 1
        selected = self.depth_detector if depth_mode else self.detector
        task = cached_task or self._start_inference(
            key, frame, payload, diagnostic=with_preview
        )
        try:
            detected = await asyncio.wait_for(
                asyncio.shield(task), (frame.deadline_ms - frame.age_ms) / 1000
            )
            # Diagnostic depth arrays are immutable inference outputs; preserve
            # their exact identity while isolating mutable planner dictionaries.
            trace = detected.get("_trace_depth")
            result = copy.deepcopy(
                detected, memo={id(trace): trace} if trace is not None else {}
            )
        except TimeoutError:
            result = {
                "state": "unknown",
                "reason": "vision_inference_timeout",
                "inference_error": "vision_inference_timeout",
                "floor_fraction": 0.0,
            }
        except Exception:
            # Never expose model exception text (paths or private inputs).
            result = {
                "state": "unknown",
                "reason": "vision_inference_unavailable",
                "inference_error": "vision_model_failure",
                "floor_fraction": 0.0,
            }
        finally:
            if task.done():
                self._drain(task)
        trace_depth = result.pop("_trace_depth", None)
        if depth_mode and trace_depth is not None:
            # Diagnostic only: exact input of this inference, never another RAW.
            # Queueing/copy time remains inside the original decision budget.
            try:
                trace_id = depth_trace.submit(
                    payload,
                    trace_depth,
                    {
                        **{
                            key: value
                            for key, value in result.items()
                            if not key.startswith("_")
                        },
                        "stage": "depth_assessment",
                        "avoidance": frame.avoidance.model_dump(mode="json"),
                        "device_id": device_id,
                        "run_id": frame.run_id,
                        "frame_id": frame.frame_id,
                        "sequence": frame.sequence,
                        "config_revision": frame.config_revision,
                    },
                    frame.corridor,
                    frame.perception,
                )
                if trace_id:
                    result["depth_trace_id"] = trace_id
            except Exception:
                # Storage must neither grant nor revoke a visual decision.
                pass
        # Full heading grid stays in the depth trace, not Pi's bounded response
        # or the preview HTTP header. Keep only selected paths and gap extents.
        result.pop("heading_candidates", None)
        elapsed = (self.clock() - now) * 1000
        if elapsed + frame.age_ms >= frame.deadline_ms:
            result = {
                "state": "unknown",
                "reason": "vision_decision_expired",
                "floor_fraction": 0.0,
                **(
                    {"inference_error": result["inference_error"]}
                    if "inference_error" in result
                    else {}
                ),
            }
        count = count + 1 if result["state"] == "clear" else 0
        state["count"] = count
        if self.closed:
            raise RiderError("module_closed", 503)
        model = selected.status().get("sha256", "")
        permitted = (
            frame.mode == "drive"
            and frame.verified
            and bool(frame.calibration_id)
            and model == frame.model_sha256
            and len(model) == 64
        )
        action = (
            "go" if permitted and count >= 3 and result["state"] == "clear" else "stop"
        )
        vision_action = "go" if count >= 3 and result["state"] == "clear" else "stop"
        if result["state"] != "clear":
            motion_reason = result["reason"]
        elif count < 3:
            motion_reason = "clear_frames_pending"
        elif frame.mode != "drive":
            motion_reason = "observe_only"
        elif not permitted:
            motion_reason = "calibration_not_validated"
        else:
            motion_reason = "clear_frames_ready"
        if depth_mode and "sectors" in result:
            history = (
                previous.get("sector_history")
                if previous and now - previous["at"] <= 0.5
                else None
            )
            if frame.motion_phase != "ready":
                history = None
            result["sectors"], state["sector_history"] = sector_trend(
                result["sectors"], history
            )
        # JPEG annotations use source-space geometry, before command-side mirroring.
        image_sectors = preview_sectors(result)
        sector_space = (
            "projected_paths"
            if result.get("geometry") == "ground_capsule"
            else "image"
            if image_sectors
            else "unavailable"
        )
        if frame.protocol_version == 2:
            if frame.avoidance.enabled and depth_mode:
                prior = dict(previous.get("avoidance") or {}) if previous else {}
                if previous and now - previous["at"] > 0.5:
                    prior.update(counts={"left": 0, "right": 0}, front=0, paused=False)
                action, motion_reason, planner, sectors = plan(
                    result,
                    prior,
                    frame.avoidance,
                    frame.motion_phase,
                    permitted,
                )
                state["avoidance"] = planner
                result.update(
                    sectors=sectors,
                    maneuver_clear_count=3 if action.startswith("turn_") else 0,
                    maneuver_phase=motion_reason,
                    turn_count=planner["turns"],
                )
            else:
                action, motion_reason = "stop", "avoidance_not_enabled"
        if frame.protocol_version == 3:
            prior = prior_planner
            if previous and now - previous["at"] > 0.5:
                prior.update(counts={"left": 0, "right": 0}, front=0)
            action, motion_reason, planner, sectors, paths, terminal = steering_plan(
                result,
                prior,
                frame.avoidance,
                frame.motion_phase,
                permitted and frame.avoidance.enabled and depth_mode,
            )
            state["avoidance"] = planner
            result.update(
                sectors=sectors,
                paths=paths,
                terminal_stop=terminal,
                maneuver_clear_count=3 if action.startswith("steer_") else 0,
                maneuver_phase=motion_reason,
                turn_count=planner["turns"],
                stalled_corrections=planner.get("stalled", 0),
                progress_delta=planner.get("progress_delta"),
                pose_resets=planner.get("pose_resets", 0),
            )
        record_result(state, frame, action, result)
        journey_result = (
            {"journey": frame.journey.model_dump(), "host_session": self.host_session}
            if frame.journey is not None
            else {}
        )
        capture_key = None
        if self.samples is not None:
            from .sample_sources import vision

            capture_key = vision(
                self.samples,
                device_id,
                self.source_session()
                if callable(self.source_session)
                else self.source_session,
                frame,
                payload,
                elapsed=elapsed,
                input_ticket=input_ticket,
                diagnostic=with_preview,
            )
            if capture_key:
                self.samples.annotate_prediction(
                    capture_key,
                    {
                        **{
                            key: value
                            for key, value in result.items()
                            if not key.startswith("_")
                        },
                        "model_sha256": model,
                        "action": action,
                        "vision_action": vision_action,
                        "input_frame": frame.model_dump(mode="json"),
                        **journey_result,
                        "perception_engine": frame.perception.engine,
                        "inference_ms": round(elapsed, 2),
                        "valid_for_ms": min(
                            200, max(0, int(frame.deadline_ms - frame.age_ms - elapsed))
                        ),
                        "diagnostic": with_preview,
                    },
                )
        image = result.pop("_preview", None)
        if image is not None:
            self.previews[device_id] = (
                now - frame.age_ms / 1000,
                image,
                {
                    **result,
                    "sectors": image_sectors,
                    "sector_space": sector_space,
                    "frame_id": frame.frame_id,
                    "run_id": frame.run_id,
                    "sequence": frame.sequence,
                    "capture_key": capture_key,
                    "clear_count": count,
                    "vision_action": vision_action,
                    "model_sha256": model,
                    **({"source": source_reference} if source_reference else {}),
                    "age_source": (
                        "pi-send-plus-host-elapsed"
                        if source_reference
                        else "host-received"
                        if with_preview
                        else "pi-reported-plus-host-elapsed"
                    ),
                    "corridor": frame.corridor,
                    "config_revision": frame.config_revision,
                    **journey_result,
                    "observed_at": time.time() - (frame.age_ms + elapsed) / 1000,
                },
                round(elapsed, 2),
            )
            self.previews.move_to_end(device_id)
            while len(self.previews) > 16:
                self.previews.popitem(last=False)
        else:
            self.previews.pop(device_id, None)
        changed, self.preview_changed = self.preview_changed, asyncio.Event()
        changed.set()
        response = {
            **result,
            **(
                {
                    "_preview": image,
                    "_preview_analysis": {
                        "sectors": image_sectors,
                        "sector_space": sector_space,
                    },
                }
                if with_preview and image is not None
                else {}
            ),
            **({"source": source_reference} if source_reference else {}),
            **journey_result,
            "action": action,
            "vision_action": vision_action,
            "motion_reason": motion_reason,
            "protocol_version": frame.protocol_version,
            "run_id": frame.run_id,
            "frame_id": frame.frame_id,
            "sequence": frame.sequence,
            "config_revision": frame.config_revision,
            "valid_for_ms": min(
                200, max(0, int(frame.deadline_ms - frame.age_ms - elapsed))
            ),
            "model_sha256": model,
            "inference_ms": round(elapsed, 2),
            "clear_count": count,
            "physical_clearance": "unverified",
            "perception_engine": frame.perception.engine,
            "drive_supported": True,
        }

        # Planning, prediction annotation and preview publication also spend
        # the original capture budget. Never return an earlier GO/TTL after
        # this post-inference work has exhausted it.
        complete_ms = max(0.0, (self.clock() - now) * 1000)
        response["decision_elapsed_ms"] = round(complete_ms, 2)
        response["valid_for_ms"] = min(
            200, max(0, int(frame.deadline_ms - frame.age_ms - complete_ms))
        )
        if response["valid_for_ms"] == 0:
            response.update(
                state="unknown",
                reason="vision_decision_expired",
                action="stop",
                vision_action="stop",
                motion_reason="vision_decision_expired",
                clear_count=0,
                sectors={},
                paths={},
            )
            response.pop("_preview", None)
            response.pop("_preview_analysis", None)
            state["count"] = 0
            if "avoidance" in state:
                state["avoidance"].update(front=0, counts={"left": 0, "right": 0})
            if frame.journey is not None:
                state["journey_completed"] = False
            if "maneuver_clear_count" in response:
                response.update(
                    maneuver_clear_count=0, maneuver_phase="vision_decision_expired"
                )
            self.previews.pop(device_id, None)
            if capture_key and self.samples is not None:
                self.samples.annotate_prediction(
                    capture_key,
                    {
                        **{
                            key: value
                            for key, value in response.items()
                            if not key.startswith("_")
                        },
                        "input_frame": frame.model_dump(mode="json"),
                        "diagnostic": with_preview,
                    },
                )
        return response

    def preview(self, device_id, *, corridor=None, revision=None, run_id=None):
        row = self.previews.get(device_id)
        if row is None or self.clock() - row[0] > 0.5:
            return None
        if run_id is not None and row[2]["run_id"] != run_id:
            return None
        # A newly admitted run cannot display a result from the preceding run.
        latest = next(
            (
                identity[1]
                for identity in reversed(self.runs)
                if identity[0] == device_id
            ),
            None,
        )
        if latest != row[2]["run_id"]:
            return None
        if corridor is not None and row[2]["corridor"] != corridor:
            return None
        if revision is not None and row[2]["config_revision"] != revision:
            return None
        return row

    async def wait_for_preview(
        self, device_id, *, corridor, revision, run_id=None, timeout=0.5
    ):
        deadline = asyncio.get_running_loop().time() + timeout
        while not self.closed:
            changed = self.preview_changed
            row = self.preview(
                device_id, corridor=corridor, revision=revision, run_id=run_id
            )
            if row is not None:
                return row
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(changed.wait(), remaining)
            except TimeoutError:
                return None
        return None

    async def share_snapshot(self, key, produce):
        """Share one diagnostic task; cancelled readers do not cancel it."""
        if self.closed:
            raise RiderError("module_closed", 503)
        task = self.snapshot_tasks.get(key)
        if task is None:
            if len(self.snapshot_tasks) >= 16:
                raise RiderError("vision_busy", 429)
            task = asyncio.create_task(produce())
            self.snapshot_tasks[key] = task
            task.add_done_callback(lambda done: self._drain_snapshot(key, done))
        return await asyncio.shield(task)

    def _drain_snapshot(self, key, task):
        if self.snapshot_tasks.get(key) is task:
            self.snapshot_tasks.pop(key)
        if not task.cancelled():
            task.exception()

    def _drain(self, task):
        self.diagnostic_inferences.discard(task)
        if not task.cancelled():
            task.exception()
        if self.busy is task:
            self.busy = None
        if self.depth_busy is task:
            self.depth_busy = None

    async def close(self):
        self.closed = True
        self.runs.clear()
        self.previews.clear()
        self.inference_cache.clear()
        self.preview_changed.set()
        snapshots = list(self.snapshot_tasks.values())
        self.snapshot_tasks.clear()
        for task in snapshots:
            task.cancel()
        pending = [task for task in (self.busy, self.depth_busy) if task is not None]
        try:
            if pending or snapshots:
                await asyncio.gather(*pending, *snapshots, return_exceptions=True)
        finally:
            self.workers.close()
