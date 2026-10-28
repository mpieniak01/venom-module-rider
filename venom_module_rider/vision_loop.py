"""Independent admission and decision lane, bounded and default STOP."""

import asyncio
import copy
import importlib.util
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from . import depth_trace
from .avoidance import plan
from .depth_detector import DepthDetector
from .errors import RiderError
from .floor_detector import FloorDetector
from .journey import planner_history, record_result
from .model_resources import model_operation
from .motion_limits import MAX_DECISION_MS
from .sector_trend import update as sector_trend
from .steering import plan as steering_plan
from .vision_overlay import preview_sectors
from .workers import VisionWorkers


@dataclass(slots=True)
class _DecisionRun:
    """State and history captured when one frame reserves its sequence."""

    device_id: Any
    frame: Any
    key: Any
    identity: Any
    previous: Any
    signature: Any
    planner: Any
    now: float
    count: int
    state: dict


@dataclass(slots=True)
class _DecisionSignals:
    """Calibrated intent refined by the existing planner and temporal veto."""

    model: Any
    permitted: bool
    action: str
    vision_action: str
    reason: Any


@dataclass(slots=True)
class _DecisionPublication:
    """Source-bound metadata shared by sample, preview and response publication."""

    elapsed: float
    image_sectors: Any
    sector_space: str
    journey: dict
    source_reference: Any
    diagnostic: bool
    input_ticket: Any
    capture_key: Any = None
    image: Any = None


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
        self.models = None
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
        self.temporal_runs = OrderedDict()
        self.closed = False
        self.previews = OrderedDict()
        self.preview_changed = asyncio.Event()
        self.snapshot_tasks = {}
        self.inference_cache = OrderedDict()
        self.diagnostic_inferences = set()
        self.production_waiters = {False: 0, True: 0}
        self.workers = VisionWorkers("rider-vision")

    @model_operation
    def _detect_floor(self, payload, frame):
        """Run semantic detection with policy settings and source-bound metadata."""
        from .navigation_profiles import parameters

        options = (
            {
                "evidence": parameters(
                    frame.behavior.model_dump(), frame.perception.model_dump()
                )
            }
            if frame.behavior.profile_id != "legacy"
            else {}
        )
        from .perception_tuning import has_semantic_tuning, semantic_parameters

        perception = frame.perception.model_dump()
        if frame.behavior.profile_id != "legacy" or has_semantic_tuning(perception):
            options["semantic"] = semantic_parameters(
                perception, frame.behavior.model_dump()
            )
        from .overlay_geometry import source_geometry

        return source_geometry(
            self.detector.detect(payload, frame.corridor, **options),
            payload,
            frame.corridor,
        )

    @model_operation
    def _detect_depth(self, payload, frame):
        from .navigation_profiles import parameters

        options = (
            {
                "evidence": parameters(
                    frame.behavior.model_dump(), frame.perception.model_dump()
                )
            }
            if frame.behavior.profile_id != "legacy"
            else {}
        )
        if frame.protocol_version == 3 and frame.avoidance.geometry.mode == "ground":
            return self.depth_detector.detect_ground(
                payload,
                frame.corridor,
                frame.perception,
                frame.avoidance.geometry,
                **options,
            )
        return self.depth_detector.detect(
            payload, frame.corridor, frame.perception, **options
        )

    @model_operation
    def _detect_model(self, payload, frame):
        """Run the selected adapter and retain geometry in source-image coordinates."""
        assert self.models is not None
        from .overlay_geometry import source_geometry

        return source_geometry(
            self.models.runtime.detect(payload, frame), payload, frame.corridor
        )

    def status(self, perception=None):
        """Advertise runtime state and supported versioned perception contracts."""
        selected = (
            self.depth_detector
            if perception and perception.get("engine") == "depth_plane"
            else self.detector
        )
        return {
            **(
                self.models.runtime.detector_status(perception)
                if self.models is not None and perception and perception.get("model_id")
                else selected.status()
            ),
            "busy": (self.depth_busy if selected is self.depth_detector else self.busy)
            is not None,
            "supported_protocol_versions": [1, 2, 3],
            "navigation_journey_version": 1,
            "temporal_navigation_v1": importlib.util.find_spec("cv2") is not None,
            "input_region_version": 1,
            "semantic_model_policy_v1": importlib.util.find_spec("cv2") is not None,
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
            frame.behavior.model_dump_json(),
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
                self._detect_model
                if self.models is not None and frame.perception.model_id
                else self._detect_depth
                if depth_mode
                else self._detect_floor,
                payload,
                frame,
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

    def _validate_decision_input(self, device_id, frame):
        """Validate input and production priority before inference admission."""
        if self.closed:
            raise RiderError("module_closed", 503)
        generation = self.models.runtime.generation if self.models else 0
        if self.models is not None:
            if self.models.switching:
                raise RiderError("model_busy", 409)
            self.models.runtime.validate(device_id, frame)
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
        return generation, depth_mode

    def _reserve_decision(self, device_id, frame, source_key, source_reference):
        """Reserve sequence and capture planner history before inference can suspend."""
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
            frame.behavior.model_dump_json(),
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
            expired_identity, _ = self.runs.popitem(last=False)
            self.temporal_runs.pop(expired_identity, None)
        if self.priority is not None:
            try:
                self.priority.live()
            except Exception:
                # Optional offline scheduling must never break live decisions.
                self.priority_errors += 1
        return _DecisionRun(
            device_id,
            frame,
            key,
            identity,
            previous,
            signature,
            prior_planner,
            now,
            count,
            state,
        )

    async def _await_decision_inference(self, task, frame):
        """Keep shielding, depth identity and sanitized timeout handling together."""
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
        return result

    def _validate_inferred_model(self, run, generation):
        """Reject a model switch before publishing any inference-derived state."""
        if self.models is not None and generation != self.models.runtime.generation:
            raise RiderError("model_selection_conflict", 409)
        if self.models is not None:
            self.models.runtime.validate(run.device_id, run.frame)

    def _trace_decision(self, run, result, payload, depth_mode):
        """Record diagnostics without letting storage grant or revoke clearance."""
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
                        "avoidance": run.frame.avoidance.model_dump(mode="json"),
                        "device_id": run.device_id,
                        "run_id": run.frame.run_id,
                        "frame_id": run.frame.frame_id,
                        "sequence": run.frame.sequence,
                        "config_revision": run.frame.config_revision,
                    },
                    run.frame.corridor,
                    run.frame.perception,
                )
                if trace_id:
                    result["depth_trace_id"] = trace_id
            except Exception:
                # Storage must neither grant nor revoke a visual decision.
                pass

    def _observe_decision(self, run, result, source_reference):
        """Advance the same source-bound temporal observer or retire legacy maps."""
        temporal = None
        if run.frame.behavior.profile_id != "legacy":
            from .navigation_policy import evaluate
            from .temporal_evidence import TemporalEvidence

            observer = self.temporal_runs.setdefault(run.identity, TemporalEvidence())
            temporal = evaluate(
                result,
                run.frame,
                observer,
                context={
                    "signature": run.signature,
                    "device_id": run.device_id,
                    "run_id": run.frame.run_id,
                },
                captured_s=run.now - run.frame.age_ms / 1000,
                source=source_reference,
            )
        else:
            result.pop("_navigation_maps", None)
        return temporal

    def _expire_inference(self, run, result, elapsed):
        """Expire inference output against the original admitted capture budget."""
        if elapsed + run.frame.age_ms >= run.frame.deadline_ms:
            if run.identity in self.temporal_runs:
                self.temporal_runs[run.identity].reset()
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
        return result

    def _calibrated_signals(self, run, result, selected, generation, depth_mode):
        """Describe calibrated intent without granting any new movement authority."""
        status = (
            self.models.runtime.detector_status(run.frame.perception.model_dump())
            if self.models is not None and run.frame.perception.model_id
            else selected.status()
        )
        model = status.get("sha256", "")
        result.update(
            model_id=run.frame.perception.model_id
            or (
                "depth-anything-v2-indoor-small"
                if depth_mode
                else "segformer-b0-ade20k"
            ),
            artifact_sha256=run.frame.perception.artifact_sha256,
            model_generation=generation,
        )
        permitted = (
            run.frame.mode == "drive"
            and run.frame.verified
            and (not run.frame.perception.model_id or depth_mode)
            and bool(run.frame.calibration_id)
            and model == run.frame.model_sha256
            and len(model) == 64
        )
        action = (
            "go"
            if permitted and run.count >= 3 and result["state"] == "clear"
            else "stop"
        )
        vision_action = (
            "go" if run.count >= 3 and result["state"] == "clear" else "stop"
        )
        motion_reason = _decision_motion_reason(run.frame, result, run.count, permitted)
        return _DecisionSignals(model, permitted, action, vision_action, motion_reason)

    def _update_decision_sectors(self, run, result, depth_mode):
        """Update sector history before taking the unmirrored preview snapshot."""
        if depth_mode and "sectors" in result:
            history = (
                run.previous.get("sector_history")
                if run.previous and run.now - run.previous["at"] <= 0.5
                else None
            )
            if run.frame.motion_phase != "ready":
                history = None
            result["sectors"], run.state["sector_history"] = sector_trend(
                result["sectors"], history
            )

    def _record_decision_sample(self, run, result, payload, signals, publication):
        """Capture and annotate this inference only after planner output is final."""
        publication.capture_key = None
        if self.samples is not None:
            from .sample_sources import vision

            publication.capture_key = vision(
                self.samples,
                run.device_id,
                self.source_session()
                if callable(self.source_session)
                else self.source_session,
                run.frame,
                payload,
                elapsed=publication.elapsed,
                input_ticket=publication.input_ticket,
                diagnostic=publication.diagnostic,
            )
            if publication.capture_key:
                self.samples.annotate_prediction(
                    publication.capture_key,
                    {
                        **{
                            key: value
                            for key, value in result.items()
                            if not key.startswith("_")
                        },
                        "model_sha256": signals.model,
                        "action": signals.action,
                        "vision_action": signals.vision_action,
                        "input_frame": run.frame.model_dump(mode="json"),
                        **publication.journey,
                        "perception_engine": run.frame.perception.engine,
                        "inference_ms": round(publication.elapsed, 2),
                        "valid_for_ms": min(
                            MAX_DECISION_MS,
                            max(
                                0,
                                int(
                                    run.frame.deadline_ms
                                    - run.frame.age_ms
                                    - publication.elapsed
                                ),
                            ),
                        ),
                        "diagnostic": publication.diagnostic,
                    },
                )

    def _publish_decision_preview(self, run, result, signals, publication):
        """Publish the captured source-space preview and wake existing waiters."""
        publication.image = result.pop("_preview", None)
        if publication.image is not None:
            self.previews[run.device_id] = (
                run.now - run.frame.age_ms / 1000,
                publication.image,
                {
                    **result,
                    "sectors": publication.image_sectors,
                    "sector_space": publication.sector_space,
                    "frame_id": run.frame.frame_id,
                    "run_id": run.frame.run_id,
                    "sequence": run.frame.sequence,
                    "capture_key": publication.capture_key,
                    "clear_count": run.count,
                    "vision_action": signals.vision_action,
                    "model_sha256": signals.model,
                    **(
                        {"source": publication.source_reference}
                        if publication.source_reference
                        else {}
                    ),
                    "age_source": (
                        "pi-send-plus-host-elapsed"
                        if publication.source_reference
                        else "host-received"
                        if publication.diagnostic
                        else "pi-reported-plus-host-elapsed"
                    ),
                    "corridor": run.frame.corridor,
                    "config_revision": run.frame.config_revision,
                    **publication.journey,
                    "observed_at": time.time()
                    - (run.frame.age_ms + publication.elapsed) / 1000,
                },
                round(publication.elapsed, 2),
            )
            self.previews.move_to_end(run.device_id)
            while len(self.previews) > 16:
                self.previews.popitem(last=False)
        else:
            self.previews.pop(run.device_id, None)
        changed, self.preview_changed = self.preview_changed, asyncio.Event()
        changed.set()

    def _decision_response(self, run, result, signals, publication, depth_mode):
        """Build the existing bounded response without exposing diagnostic arrays."""
        from .navigation_profiles import policy_digest

        response = {
            **result,
            **(
                {
                    "behavior": run.frame.behavior.model_dump(),
                    "effective_policy_sha256": policy_digest(
                        run.frame.behavior.model_dump(),
                        run.frame.perception.model_dump(),
                    ),
                }
                if run.frame.behavior.profile_id != "legacy"
                else {}
            ),
            **(
                {
                    "_preview": publication.image,
                    "_preview_analysis": {
                        "sectors": publication.image_sectors,
                        "sector_space": publication.sector_space,
                    },
                }
                if publication.diagnostic and publication.image is not None
                else {}
            ),
            **(
                {"source": publication.source_reference}
                if publication.source_reference
                else {}
            ),
            **publication.journey,
            "action": signals.action,
            "vision_action": signals.vision_action,
            "motion_reason": signals.reason,
            "protocol_version": run.frame.protocol_version,
            "run_id": run.frame.run_id,
            "frame_id": run.frame.frame_id,
            "sequence": run.frame.sequence,
            "config_revision": run.frame.config_revision,
            "valid_for_ms": min(
                MAX_DECISION_MS,
                max(
                    0,
                    int(run.frame.deadline_ms - run.frame.age_ms - publication.elapsed),
                ),
            ),
            "model_sha256": signals.model,
            "inference_ms": round(publication.elapsed, 2),
            "clear_count": run.count,
            "physical_clearance": "unverified",
            "perception_engine": run.frame.perception.engine,
            "drive_supported": not run.frame.perception.model_id or depth_mode,
        }

        return response

    def _complete_decision(self, run, response, publication):
        """Account for all post-inference work before allowing a positive TTL."""
        complete_ms = max(0.0, (self.clock() - run.now) * 1000)
        response["decision_elapsed_ms"] = round(complete_ms, 2)
        response["valid_for_ms"] = min(
            MAX_DECISION_MS,
            max(0, int(run.frame.deadline_ms - run.frame.age_ms - complete_ms)),
        )
        if response["valid_for_ms"] == 0:
            if run.identity in self.temporal_runs:
                self.temporal_runs[run.identity].reset()
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
            run.state["count"] = 0
            if "avoidance" in run.state:
                run.state["avoidance"].update(front=0, counts={"left": 0, "right": 0})
            if run.frame.journey is not None:
                run.state["journey_completed"] = False
            if "maneuver_clear_count" in response:
                response.update(
                    maneuver_clear_count=0, maneuver_phase="vision_decision_expired"
                )
            self.previews.pop(run.device_id, None)
            if publication.capture_key and self.samples is not None:
                self.samples.annotate_prediction(
                    publication.capture_key,
                    {
                        **{
                            key: value
                            for key, value in response.items()
                            if not key.startswith("_")
                        },
                        "input_frame": run.frame.model_dump(mode="json"),
                        "diagnostic": publication.diagnostic,
                    },
                )

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
        """Admit, assess and publish one source-bound decision within its TTL."""
        generation, depth_mode = self._validate_decision_input(device_id, frame)
        frame, cached_task = await self._admit_inference(
            device_id, frame, source_key, with_preview
        )
        run = self._reserve_decision(device_id, frame, source_key, source_reference)
        selected = self.depth_detector if depth_mode else self.detector
        task = cached_task or self._start_inference(
            run.key, frame, payload, diagnostic=with_preview
        )
        result = await self._await_decision_inference(task, frame)
        self._validate_inferred_model(run, generation)
        self._trace_decision(run, result, payload, depth_mode)
        # Full grids remain diagnostic; bounded responses retain selected paths only.
        result.pop("heading_candidates", None)
        temporal = self._observe_decision(run, result, source_reference)
        elapsed = (self.clock() - run.now) * 1000
        result = self._expire_inference(run, result, elapsed)
        if result["state"] == "clear":
            run.count = run.count + 1
        elif not (temporal and temporal["action"] == "slow" and run.count >= 3):
            run.count = 0
        run.state["count"] = run.count
        if self.closed:
            raise RiderError("module_closed", 503)
        signals = self._calibrated_signals(
            run, result, selected, generation, depth_mode
        )
        self._update_decision_sectors(run, result, depth_mode)
        # Preview sectors precede command-side mirroring and planner mutations.
        image_sectors, sector_space = _decision_preview_space(result)
        if frame.protocol_version == 2:
            _plan_decision_v2(run, result, signals, depth_mode)
        if frame.protocol_version == 3:
            _plan_decision_v3(run, result, signals, depth_mode)
        _apply_temporal_decision(run, result, signals, temporal)
        record_result(run.state, frame, signals.action, result)
        journey_result = (
            {"journey": frame.journey.model_dump(), "host_session": self.host_session}
            if frame.journey is not None
            else {}
        )
        publication = _DecisionPublication(
            elapsed,
            image_sectors,
            sector_space,
            journey_result,
            source_reference,
            with_preview,
            input_ticket,
        )
        self._record_decision_sample(run, result, payload, signals, publication)
        self._publish_decision_preview(run, result, signals, publication)
        response = self._decision_response(
            run, result, signals, publication, depth_mode
        )
        # Sampling and publication still spend the original capture budget.
        self._complete_decision(run, response, publication)
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


def _decision_motion_reason(frame, result, count, permitted):
    """Apply the original priority of image, count, mode and calibration reasons."""
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
    return motion_reason


def _decision_preview_space(result):
    """Snapshot original sectors before command-side mirroring changes them."""
    image_sectors = preview_sectors(result)
    sector_space = (
        "projected_paths"
        if result.get("geometry") == "ground_capsule"
        else "image"
        if image_sectors
        else "unavailable"
    )
    return image_sectors, sector_space


def _plan_decision_v2(run, result, signals, depth_mode):
    """Apply the existing bounded turn planner only for protocol version two."""
    if run.frame.avoidance.enabled and depth_mode:
        prior = dict(run.previous.get("avoidance") or {}) if run.previous else {}
        if run.previous and run.now - run.previous["at"] > 0.5:
            prior.update(counts={"left": 0, "right": 0}, front=0, paused=False)
        signals.action, signals.reason, planner, sectors = plan(
            result,
            prior,
            run.frame.avoidance,
            run.frame.motion_phase,
            signals.permitted,
        )
        run.state["avoidance"] = planner
        result.update(
            sectors=sectors,
            maneuver_clear_count=3 if signals.action.startswith("turn_") else 0,
            maneuver_phase=signals.reason,
            turn_count=planner["turns"],
        )
    else:
        signals.action, signals.reason = "stop", "avoidance_not_enabled"


def _plan_decision_v3(run, result, signals, depth_mode):
    """Apply the existing pose-aware planner without resetting captured history."""
    prior = run.planner
    if run.previous and run.now - run.previous["at"] > 0.5:
        prior.update(counts={"left": 0, "right": 0}, front=0)
    signals.action, signals.reason, planner, sectors, paths, terminal = steering_plan(
        result,
        prior,
        run.frame.avoidance,
        run.frame.motion_phase,
        signals.permitted and run.frame.avoidance.enabled and depth_mode,
    )
    run.state["avoidance"] = planner
    result.update(
        sectors=sectors,
        paths=paths,
        terminal_stop=terminal,
        maneuver_clear_count=3 if signals.action.startswith("steer_") else 0,
        maneuver_phase=signals.reason,
        turn_count=planner["turns"],
        stalled_corrections=planner.get("stalled", 0),
        progress_delta=planner.get("progress_delta"),
        pose_resets=planner.get("pose_resets", 0),
    )


def _apply_temporal_decision(run, result, signals, temporal):
    """Retain temporal STOP precedence and the qualified side-path exception."""
    if temporal is None:
        return
    if result.get("reason") == "vision_decision_expired":
        signals.action, signals.vision_action = "stop", "stop"
        return
    signals.vision_action = temporal["action"] if run.count >= 3 else "stop"
    if (
        signals.vision_action == "slow"
        and signals.permitted
        and run.frame.motion_phase == "ready"
        and not result.get("terminal_stop")
    ):
        signals.action, signals.reason = "slow", temporal["reason"]
    elif temporal["action"] == "stop":
        steering_allowed = (
            signals.action.startswith(("turn_", "steer_"))
            and temporal.get("state") == "obstacle"
            and temporal.get("near_guard_clear") is True
            and temporal.get("evidence_frames", 0) >= 3
            and temporal.get("spatial", {}).get("valid") is True
            and temporal.get("spatial", {}).get("hard_stop") is not True
        )
        if not steering_allowed:
            signals.action, signals.reason = "stop", temporal["reason"]
