"""In-process operator simulation. No sockets, SDK, UART or hardware imports."""

import math
import time
import uuid
from collections import deque

import httpx

from .client import PiClient


def client(device, states):
    from .demo import DemoPi, create_app

    state = states.setdefault(device.id, None)
    if state is None:
        state = DemoPi(simulate=True)
        states[device.id] = state
    return PiClient(device, transport=httpx.ASGITransport(app=create_app(state)))


class DemoSimulation:
    def __init__(self):
        self.linear = self.yaw = 0.0
        self.x = self.y = self.heading = 0.0
        self.observed = time.monotonic()
        self.operation = None
        self.origin = None
        self.cancelled: deque[tuple[str, str, str]] = deque(maxlen=64)

    def update(self, deadline):
        now = time.monotonic()
        dt = max(0.0, min(now, deadline) - self.observed) if deadline else 0.0
        self.x += math.cos(self.heading) * self.linear * dt
        self.y += math.sin(self.heading) * self.linear * dt
        self.heading -= self.yaw * dt
        self.observed = now
        if now >= deadline:
            self.linear = self.yaw = 0.0

    def stop(self):
        self.linear = self.yaw = 0.0

    def snapshot(self, state):
        return {
            "simulated": True,
            "physical_completion": "not_applicable",
            "operator_intervention_version": 1,
            "operator_height_intervention_version": 1,
            "operator_posture_intervention_version": 1,
            "operator_control": {
                "version": 1,
                "state": "paused" if state.state == "ARMED" else "idle",
                "generation": state.generation,
                "operation_id": self.operation,
            },
            "output": {"lx": self.linear, "az": self.yaw},
            "target": {"lx": self.linear, "az": self.yaw},
            "simulation": {
                "x": self.x,
                "y": self.y,
                "heading": self.heading,
                "units": "normalized_command_time",
                "physical": False,
            },
        }

    def release_origin(self, state, body):
        return (
            body.get("operator_intervention_version") == 1
            and body.get("mode") == "release"
            and body.get("supersedes_operation_id") == self.operation
            and body.get("generation") == self.origin
            and body.get("panel_id") == state.panel_id
            and state.state == "ARMED"
        )

    def takeover(self, state, body):
        if "operator_intervention_version" not in body:
            return False
        from pydantic import ValidationError

        from .api.schemas import Command

        try:
            Command.model_validate(body)
        except ValidationError as exc:
            raise ValueError("operator_intervention_invalid") from exc
        if body.get("mode") == "release":
            identity = (
                body["panel_id"],
                body["generation"],
                body["supersedes_operation_id"],
            )
            matching = self.release_origin(state, body)
            if (
                matching
                or body["generation"] == state.generation
                and state.state == "DISARMED"
            ):
                self.cancelled.append(identity)
                self.stop()
                state.deadline = 0.0
                return True
            raise ValueError("operator_intervention_cancel_scope")
        if state.state == "ESTOP":
            raise ValueError("estop_latched")
        if state.stale or body.get("settings_revision") != state.controls["revision"]:
            raise ValueError("manual_settings_stale")
        if body["action"] == "height":
            limits = state.controls["parameter_schema"]["actuator_fields"]["height"]
            if not limits["minimum"] <= body["height"] <= limits["maximum"]:
                raise ValueError("invalid_height")
        if (body["panel_id"], body["generation"], body["request_id"]) in self.cancelled:
            raise ValueError("operator_intervention_cancelled")
        self.origin = body["generation"]
        self.stop()
        state.deadline = 0.0
        state.state, state.owner, state.panel_id = "ARMED", "manual", body["panel_id"]
        state.arming_mode, state.arming_deadline = "operator", None
        state.generation = uuid.uuid4().hex
        self.operation = body["request_id"]
        return True

    def drive(self, state, body):
        if body.get("operator_intervention_version") != 1:
            self.operation = None
        if body.get("mode") == "release":
            self.stop()
            return
        item = state.controls["directions"][body["direction"]]
        speed = item["speed"]
        self.linear = (
            speed
            if body["direction"] == "forward"
            else -speed
            if body["direction"] == "backward"
            else 0.0
        )
        self.yaw = (
            -speed
            if body["direction"] == "left"
            else speed
            if body["direction"] == "right"
            else 0.0
        )
