"""Independent admission and decision lane, bounded and default STOP."""

import asyncio
import time
from collections import OrderedDict

from .errors import RiderError
from .floor_detector import FloorDetector


class VisionLoop:
    def __init__(self, detector=None, clock=time.monotonic):
        self.detector = detector or FloorDetector()
        self.clock = clock
        self.busy = None
        self.runs = OrderedDict()
        self.closed = False

    def status(self):
        return {
            **self.detector.status(),
            "busy": self.busy is not None,
            "protocol_version": 1,
            "max_frame_bytes": 512 * 1024,
            "max_deadline_ms": 500,
            "clear_frames_required": 3,
            "physical_clearance": "unverified",
        }

    async def decide(self, device_id, frame, payload):
        if self.closed:
            raise RiderError("module_closed", 503)
        if not frame.valid_corridor() or frame.age_ms >= frame.deadline_ms:
            raise RiderError("vision_frame_expired", 409)
        if self.busy is not None:
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
        }
        self.runs[identity] = state
        self.runs.move_to_end(identity)
        while len(self.runs) > 32:
            self.runs.popitem(last=False)
        task = asyncio.create_task(
            asyncio.to_thread(self.detector.detect, payload, frame.corridor)
        )
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
                self.busy = None
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
        model = self.detector.status().get("sha256", "")
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
        return {
            **result,
            "action": action,
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
        }

    def _drain(self, task):
        if not task.cancelled():
            task.exception()
        if self.busy is task:
            self.busy = None

    async def close(self):
        self.closed = True
        self.runs.clear()
        if self.busy is not None:
            await asyncio.gather(self.busy, return_exceptions=True)
