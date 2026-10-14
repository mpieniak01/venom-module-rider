"""Independent admission and decision lane, bounded and default STOP."""

import asyncio
import time
from collections import OrderedDict

from .depth_detector import DepthDetector
from .errors import RiderError
from .floor_detector import FloorDetector


class VisionLoop:
    def __init__(self, detector=None, clock=time.monotonic):
        self.detector = detector or FloorDetector()
        self.depth_detector = DepthDetector()
        self.clock = clock
        self.busy = None
        self.depth_busy = None
        self.runs = OrderedDict()
        self.closed = False
        self.previews = OrderedDict()
        self.preview_changed = asyncio.Event()

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
            "protocol_version": 1,
            "max_frame_bytes": 512 * 1024,
            "max_deadline_ms": 500,
            "clear_frames_required": 3,
            "physical_clearance": "unverified",
        }

    async def decide(self, device_id, frame, payload, *, with_preview=False):
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
        if (self.depth_busy if depth_mode else self.busy) is not None:
            raise RiderError("vision_busy", 429)
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
        )
        if previous and (
            frame.sequence <= previous["sequence"]
            or frame.frame_id == previous["frame"]
            or previous["signature"] != signature
        ):
            raise RiderError("vision_replay_or_configuration_changed", 409)
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
        self.runs[identity] = state
        self.runs.move_to_end(identity)
        while len(self.runs) > 32:
            self.runs.popitem(last=False)
        selected = self.depth_detector if depth_mode else self.detector
        if depth_mode:
            task = asyncio.create_task(
                asyncio.to_thread(
                    self.depth_detector.detect,
                    payload,
                    frame.corridor,
                    frame.perception,
                )
            )
        else:
            task = asyncio.create_task(
                asyncio.to_thread(self.detector.detect, payload, frame.corridor)
            )
        if depth_mode:
            self.depth_busy = task
        else:
            self.busy = task
        try:
            result = await asyncio.wait_for(
                asyncio.shield(task), (frame.deadline_ms - frame.age_ms) / 1000
            )
        except (TimeoutError, ValueError, OSError, RuntimeError, ImportError):
            result = {
                "state": "unknown",
                "reason": "vision_inference_unavailable",
                "floor_fraction": 0.0,
            }
        finally:
            if task.done():
                self._drain(task)
            else:
                task.add_done_callback(self._drain)
        elapsed = (self.clock() - now) * 1000
        if elapsed + frame.age_ms >= frame.deadline_ms:
            result = {
                "state": "unknown",
                "reason": "vision_decision_expired",
                "floor_fraction": 0.0,
            }
        count = count + 1 if result["state"] == "clear" else 0
        state["count"] = count
        if self.closed:
            raise RiderError("module_closed", 503)
        model = selected.status().get("sha256", "")
        permitted = (
            not depth_mode
            and frame.mode == "drive"
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
        elif depth_mode and frame.mode == "drive":
            motion_reason = "depth_observation_only"
        elif frame.mode != "drive":
            motion_reason = "observe_only"
        elif not permitted:
            motion_reason = "calibration_not_validated"
        else:
            motion_reason = "clear_frames_ready"
        image = result.pop("_preview", None)
        if image is not None:
            self.previews[device_id] = (
                now - frame.age_ms / 1000,
                image,
                {
                    **result,
                    "frame_id": frame.frame_id,
                    "corridor": frame.corridor,
                    "config_revision": frame.config_revision,
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
        return {
            **result,
            **({"_preview": image} if with_preview and image is not None else {}),
            "action": action,
            "vision_action": vision_action,
            "motion_reason": motion_reason,
            "protocol_version": 1,
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
            "drive_supported": not depth_mode,
        }

    def preview(self, device_id, *, corridor=None, revision=None):
        row = self.previews.get(device_id)
        if row is None or self.clock() - row[0] > 0.5:
            return None
        if corridor is not None and row[2]["corridor"] != corridor:
            return None
        if revision is not None and row[2]["config_revision"] != revision:
            return None
        return row

    async def wait_for_preview(self, device_id, *, corridor, revision, timeout=0.5):
        deadline = asyncio.get_running_loop().time() + timeout
        while not self.closed:
            changed = self.preview_changed
            row = self.preview(device_id, corridor=corridor, revision=revision)
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

    def _drain(self, task):
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
        self.preview_changed.set()
        pending = [task for task in (self.busy, self.depth_busy) if task is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
