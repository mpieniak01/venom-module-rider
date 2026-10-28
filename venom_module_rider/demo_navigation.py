"""Recorded camera replay against Venom; actuation is an in-memory fixture only."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi.responses import Response

from .local_api import local_api_origin, local_api_tls_context
from .motion_limits import MAX_DECISION_MS, MAX_MOTION_MS, MAX_SPEED
from .vision_contract import VisionFrame

LOOP_GOALS = {"s14_venom_navigation": "drive", "s15_venom_perception": "observe"}
DEFAULT_POLICY: dict[str, Any] = {
    "protocol_version": 1,
    "revision": 0,
    "mode": "observe",
    "speed": 0.1,
    "decision_budget_ms": 300,
    "max_motion_ms": 150,
    "motion_limits": {
        "version": 1,
        "max_speed": MAX_SPEED,
        "max_motion_ms": MAX_MOTION_MS,
        "max_turn_speed": MAX_SPEED,
        "max_turn_ms": MAX_MOTION_MS,
        "max_decision_ms": MAX_DECISION_MS,
    },
    "corridor": [0.35, 0.80, 0.65, 0.95],
    "calibration_id": "",
    "model_sha256": "",
    "perception": {
        "engine": "segformer",
        "residual_limit": 0.06,
        "outlier_limit": 0.005,
        "slope_min": 2.0,
    },
    "verified": False,
}


def load_pi_lease(path: Path):
    """Load the actual, hardware-free Pi permit class from an explicit local file."""
    spec = importlib.util.spec_from_file_location("rider_replay_decision_lease", path)
    if spec is None or spec.loader is None:
        raise ValueError("decision_lease_source_missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DecisionLease


class ReplayNavigation:
    def __init__(
        self,
        payload: bytes,
        lease_factory,
        *,
        recorded_at=0.0,
        request=None,
        clock=time.monotonic,
        model_sha="",
        api_url="http://127.0.0.1:8800",
        token=None,
    ):
        try:
            api_url = local_api_origin(api_url)
        except ValueError:
            raise ValueError("replay_requires_loopback") from None
        self.payload, self.lease_factory = payload, lease_factory
        self.recorded_at, self.clock = recorded_at, clock
        self.policy: dict[str, Any] = dict(DEFAULT_POLICY)
        self.policy["corridor"] = list(DEFAULT_POLICY["corridor"])
        self.model_sha, self.api_url = model_sha, api_url
        self.token = (
            token if token is not None else os.getenv("RIDER_COMPUTE_TOKEN", "")
        )
        self.request = request or self._request
        self.state: Any = None
        self.task: asyncio.Task | None = None
        self.watchdog: asyncio.Task | None = None
        self.lease: Any = None
        self.status: dict[str, Any] = {
            "running": False,
            "stale": False,
            "reason": "replay_idle",
        }
        self.events: list[dict[str, Any]] = []
        self.frame_number = 0
        self.drop_link = False
        self.http = httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            verify=local_api_tls_context() if api_url.startswith("https://") else True,
        )
        self.motion_generation: str | None = None
        self.trial_requests: list[str] = []
        self.operator_mode = False
        self.operator_state = "idle"
        self.operator_reason = "not_started"
        self.operation_id = None
        self.operation_origin_generation = None
        self.operation_origin_sequence = -1

    def operator_status(self):
        return {
            "version": 1,
            "state": self.operator_state,
            "operation_id": self.operation_id,
            "generation": self.state.generation,
            "reason": self.operator_reason,
        }

    def supersedes_operation(self, body):
        return (
            self.operator_mode
            and self.state.state == "ARMED"
            and self.state.owner == "navigation"
            and self.operator_state in {"qualifying", "active"}
            and isinstance(self.operation_id, str)
            and body.get("supersedes_operation_id") == self.operation_id
            and body.get("generation")
            in {self.state.generation, self.operation_origin_generation}
            and body.get("panel_id") == self.state.panel_id
            and self.state.panel_id is not None
            and body.get("source") == "manual"
            and body.get("execution_context") == "panel"
            and (
                body.get("action") == "navigation_pause"
                or (body.get("action") == "move" and body.get("intervene") is True)
            )
        )

    def operator_command(self, body):
        """In-memory copy of the negotiated Pi boundary; never calls hardware."""
        if not self.operator_mode or not self.status.get("running"):
            raise ValueError("operator_control_not_available")
        if "supersedes_operation_id" in body and not self.supersedes_operation(body):
            raise ValueError("navigation_operation_stale")
        if not body.get("panel_id"):
            raise ValueError("operator_panel_required")
        if body["panel_id"] != self.state.panel_id:
            raise ValueError("panel_owner_mismatch")
        safety = self.state.safety()
        if safety["state"] != "ARMED":
            raise ValueError("motion_disarmed")
        if body.get("settings_revision") != self.state.controls["revision"]:
            raise ValueError("manual_settings_stale")
        resume = body.get("action") == "navigation_resume"
        if resume and (
            self.state.arming_mode != "timed" or self.state.arming_deadline is None
        ):
            raise ValueError("operator_timed_consent_required")
        if resume and self.operator_state in {"active", "qualifying"}:
            raise ValueError("navigation_already_active")
        self.operation_origin_generation = self.state.generation if resume else None
        self.operation_origin_sequence = body["seq"] if resume else -1
        self.state.deadline = 0.0
        self.state.generation = uuid.uuid4().hex
        self.state.owner = "navigation" if resume else "manual"
        # Preserve the original timed deadline; this operation never ARM/renews.
        self.operator_state = "qualifying" if resume else "paused"
        self.operator_reason = (
            "clear_frames_pending" if resume else "operator_intervention"
        )
        self.operation_id = body.get("request_id")
        self.status.update(
            paused=not resume,
            simulated_moving=False,
            observed_at=time.time(),
            generation=self.state.generation,
            operator_control_version=1,
        )

    def pause_operator(self, reason):
        self.operator_state = "paused"
        self.operator_reason = reason
        self.status.update(paused=True, simulated_moving=False, stop_reason=reason)

    def attach(self, state):
        self.state = state
        state.goals.update(dict.fromkeys(LOOP_GOALS, False))

    def rows(self):
        return [
            {
                "name": name,
                "can_move": mode == "drive",
                "motion_owner": "navigation" if mode == "drive" else None,
                "control_owner": "navigation" if mode == "drive" else None,
                "installed": True,
                "start_available": True,
                "resources": ["camera", "frame_feed"],
                "members": [],
                "option_schema": {},
            }
            for name, mode in LOOP_GOALS.items()
        ]

    def configure(self, body):
        if body.get("revision") != self.policy["revision"]:
            raise ValueError("navigation_configuration_conflict")
        if any(self.state.goals[name] for name in LOOP_GOALS):
            raise ValueError("navigation_stop_goals_first")
        values = body.get("values", {})
        if not isinstance(values, dict) or set(values) - (
            set(DEFAULT_POLICY) - {"revision", "protocol_version"}
        ):
            raise ValueError("navigation_invalid_configuration")
        candidate = {**self.policy, **values}
        VisionFrame.model_validate_json(
            json.dumps(
                {
                    "protocol_version": 1,
                    "run_id": "validate",
                    "frame_id": "validate",
                    "sequence": 1,
                    "config_revision": 0,
                    "age_ms": 0.0,
                    "deadline_ms": candidate["decision_budget_ms"],
                    **{
                        key: candidate[key]
                        for key in (
                            "mode",
                            "corridor",
                            "verified",
                            "calibration_id",
                            "model_sha256",
                            "perception",
                        )
                    },
                }
            )
        )
        if not (
            type(candidate["speed"]) in {int, float}
            and 0.01 <= candidate["speed"] <= MAX_SPEED
            and type(candidate["max_motion_ms"]) is int
            and 50 <= candidate["max_motion_ms"] <= MAX_MOTION_MS
        ):
            raise ValueError("navigation_invalid_configuration")
        self.policy = {**candidate, "revision": self.policy["revision"] + 1}
        return self.policy

    def image(self):
        self.frame_number += 1
        return Response(
            self.payload,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store",
                "X-Rider-Source": "replay",
                "X-Rider-Recorded-At": str(self.recorded_at),
                "X-Rider-Frame-Id": "replay-" + str(self.frame_number),
                "X-Rider-File-Observed-At": str(time.time()),
                "X-Rider-Stale": "false",
            },
        )

    def halt(self, reason):
        if self.lease:
            self.lease.halt(reason)
        if self.task:
            self.task.cancel()
        if self.watchdog:
            self.watchdog.cancel()
        for name in LOOP_GOALS:
            self.state.goals[name] = False
        self.state.camera_on = self.state.goals[
            "s2_camera_preview"
        ] or self.state.goals.get("s12_venom_vision", False)
        self.status.update(
            running=False,
            stale=False,
            latched=True,
            stop_reason=reason,
            simulated_moving=False,
            observed_at=time.time(),
        )

    async def close(self):
        self.halt("replay_closed")
        tasks = [task for task in (self.task, self.watchdog) if task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.http.aclose()

    async def _request(self, meta, payload):
        response = await self.http.post(
            self.api_url + "/api/v1/rider/compute/vision/decision",
            content=payload,
            headers={
                "Content-Type": "image/jpeg",
                "X-Rider-Device": "demo",
                **({"Authorization": "Bearer " + self.token} if self.token else {}),
                "X-Rider-Vision": json.dumps(meta),
            },
            timeout=max(1.0, self.policy["decision_budget_ms"] / 1000)
            if meta.get("mode") == "observe"
            else self.policy["decision_budget_ms"] / 1000,
        )
        response.raise_for_status()
        return response.json()

    def drive(self, speed, duration):
        safety = self.state.safety()
        if (
            safety["owner"] != "navigation"
            or safety["state"] != "ARMED"
            or self.state.generation != self.motion_generation
        ):
            raise ValueError("navigation_authority_lost")
        self.state.deadline = self.clock() + duration if speed != 0 else 0.0
        self.events.append(
            {
                "action": "go" if speed != 0 else "pause",
                "duration": duration,
                "speed": speed,
                "physical_completion": "not_applicable",
            }
        )
        self.events = self.events[-64:]

    def brake(self):
        # An old navigation generation can never brake newer manual authority.
        if (
            self.state.owner == "navigation"
            and self.state.generation == self.motion_generation
        ):
            self.state.deadline = 0.0
            self.state.state, self.state.owner = "DISARMED", None
            self.state.generation = uuid.uuid4().hex
            self.state.seq, self.state.arming_deadline = -1, None
        self.events.append({"action": "stop", "physical_completion": "not_applicable"})
        self.events = self.events[-64:]

    async def start_trial(self, body):
        """The demo Pi implements the same one-request contract in memory."""
        from .api.schemas import NavigationTrialRequest

        body = NavigationTrialRequest.model_validate(body).model_dump()
        if not -0.1 <= time.time() - body["ts"] <= body["ttl_ms"] / 1000:
            raise ValueError("stale_or_invalid_operator_intent")
        safety = self.state.safety()
        if safety["generation"] != body["generation"] or safety["stale"]:
            raise ValueError("navigation_trial_authority_changed")
        if self.policy["revision"] != body["config_revision"]:
            raise ValueError("navigation_configuration_conflict")
        if safety["manual_settings"]["revision"] != body["settings_revision"]:
            raise ValueError("manual_settings_stale")
        if body["request_id"] in self.trial_requests:
            raise ValueError("navigation_trial_replay")
        if not self.policy["verified"] or not self.policy["calibration_id"]:
            raise ValueError("navigation_validation_required")
        self.trial_requests = [*self.trial_requests[-63:], body["request_id"]]
        self.operator_mode = body.get("operator_control_version") == 1
        self.halt("operator_trial_prepare")
        self.state.goals["s2_camera_preview"] = True
        self.state.camera_on = True
        config = self.configure(
            {"revision": body["config_revision"], "values": {"mode": "drive"}}
        )
        if self.state.state == "ARMED":
            self.state.control(
                {"action": "stop", "expected_generation": self.state.generation}
            )
        reply = self.state.control(
            {
                "action": "arm",
                "source": "manual",
                "owner": "navigation",
                "request_id": body["request_id"] + "-arm",
                "generation": self.state.generation,
                "seq": self.state.seq + 1,
                "ts": time.time(),
                "ttl_ms": 500,
                "operator_consent": True,
                "operator_plan": body["operator_plan"],
                "execution_context": "panel",
                "arming_mode": "timed",
                "lease_s": 10,
                **(
                    {"panel_id": body["panel_id"], "operator_control_version": 1}
                    if self.operator_mode
                    else {}
                ),
            }
        )
        if reply.status_code != 200:
            raise ValueError("navigation_trial_arm_unconfirmed")
        armed_generation = self.state.generation
        try:
            await self.start("s14_venom_navigation")
        except Exception:
            self.state.control(
                {"action": "stop", "expected_generation": armed_generation}
            )
            raise
        self.status.update(
            generation=self.state.generation, operator_request_id=body["request_id"]
        )
        return {
            "ok": True,
            "request_id": body["request_id"],
            "config": config,
            "status": dict(self.status),
            "authority": self.state.safety(),
        }

    async def start(self, name):
        mode = LOOP_GOALS[name]
        if mode == "drive":
            safety = self.state.safety()
            if safety["owner"] != "navigation" or safety["state"] != "ARMED":
                raise ValueError("navigation_authority_lost")
            if (
                self.policy["verified"] is not True
                or self.policy["model_sha256"] != self.model_sha
            ):
                raise ValueError("navigation_calibration_required")
        self.halt("replay_replaced")
        policy = {**self.policy, "mode": mode}
        run = uuid.uuid4().hex
        self.motion_generation = self.state.generation
        self.lease = self.lease_factory(
            policy,
            run,
            self.drive,
            self.brake,
            self.clock,
            pause=lambda duration: self.drive(0.0, duration),
        )
        self.state.goals[name] = True
        self.state.camera_on = True
        self.status = {
            "running": True,
            "stale": False,
            "mode": mode,
            "run_id": run,
            "latched": False,
            "config_revision": policy["revision"],
            "observed_at": time.time(),
            "stop_reason": "waiting_for_decision",
            "replay": {
                "recorded_at": self.recorded_at,
                "sha256": hashlib.sha256(self.payload).hexdigest(),
                "actuation": False,
            },
        }
        if self.operator_mode:
            self.operator_state = "qualifying"
            self.operator_reason = "clear_frames_pending"
            self.status["operator_control_version"] = 1
            self.task = asyncio.create_task(self.run_operator(policy, run))
        else:
            self.task = asyncio.create_task(self.run(policy, run))
        if mode == "drive":
            self.watchdog = asyncio.create_task(self.watch())

    async def watch(self):
        while self.status["running"]:
            if self.operator_mode and self.operator_state == "paused":
                await asyncio.sleep(0.01)
                continue
            if not self.lease.tick():
                if self.operator_mode:
                    self.operator_state = "paused"
                    self.operator_reason = self.lease.reason
                    self.status.update(paused=True, simulated_moving=False)
                    await asyncio.sleep(0.01)
                    continue
                self.status.update(
                    running=False,
                    latched=True,
                    stop_reason=self.lease.reason,
                    simulated_moving=False,
                    observed_at=time.time(),
                )
                return
            await asyncio.sleep(0.01)

    async def run_operator(self, policy, run):
        """Keep one replay producer alive while motion authorization changes."""
        sequence = 0
        previous_drive = True
        try:
            while self.status["running"]:
                safety = self.state.safety()
                generation = safety["generation"]
                drive = (
                    self.operator_state in {"qualifying", "active"}
                    and safety["state"] == "ARMED"
                    and safety["owner"] == "navigation"
                )
                if not drive and self.operator_state in {"qualifying", "active"}:
                    self.pause_operator("motion_disarmed")
                if not drive and self.operator_state in {"qualifying", "active"}:
                    self.pause_operator("motion_disarmed")
                if generation != self.motion_generation or drive != previous_drive:
                    previous_drive = drive
                    run = uuid.uuid4().hex
                    self.motion_generation = generation
                    self.lease = self.lease_factory(
                        policy,
                        run,
                        self.drive,
                        self.brake,
                        self.clock,
                        pause=lambda duration: self.drive(0.0, duration),
                    )
                    self.status.update(run_id=run, generation=generation)
                sequence += 1
                capture = self.clock()
                frame_id = hashlib.sha256(
                    self.payload + str(sequence).encode()
                ).hexdigest()
                meta = VisionFrame(
                    protocol_version=1,
                    run_id=run,
                    frame_id=frame_id,
                    sequence=sequence,
                    config_revision=policy["revision"],
                    age_ms=0.0,
                    deadline_ms=policy["decision_budget_ms"],
                    mode="drive" if drive else "observe",
                    corridor=tuple(policy["corridor"]),
                    **{
                        key: policy[key]
                        for key in (
                            "verified",
                            "calibration_id",
                            "model_sha256",
                            "perception",
                        )
                    },
                )
                try:
                    if self.drop_link:
                        raise TimeoutError("replay_link_dropped")
                    decision = await self.request(meta.model_dump(), self.payload)
                except Exception:
                    if drive:
                        self.lease.halt("replay_transport_failed")
                    self.operator_state = "paused"
                    self.operator_reason = "replay_transport_failed"
                    self.status.update(paused=True, simulated_moving=False)
                    await asyncio.sleep(0.2)
                    continue
                # An old asynchronous result cannot seize new manual authority.
                if generation != self.state.generation:
                    continue
                accepted = (
                    self.lease.accept(decision, frame_id, sequence, capture)
                    if drive
                    else False
                )
                if drive and not self.lease.latched and accepted:
                    self.operator_state = "active"
                    self.operator_reason = "clear_frames_ready"
                elif drive and self.lease.latched:
                    self.operator_state = "paused"
                    self.operator_reason = self.lease.reason
                self.status.update(
                    decision=decision,
                    observed_at=time.time(),
                    paused=self.operator_state == "paused",
                    simulated_moving=accepted,
                    stop_reason=self.operator_reason,
                )
                await asyncio.sleep(0.03 if drive else 0.2)
        except asyncio.CancelledError:
            raise

    async def run(self, policy, run):
        sequence = 0
        try:
            while self.status["running"]:
                sequence += 1
                capture = self.clock()
                frame_id = hashlib.sha256(
                    self.payload + str(sequence).encode()
                ).hexdigest()
                frame = VisionFrame.model_validate_json(
                    json.dumps(
                        {
                            "protocol_version": 1,
                            "run_id": run,
                            "frame_id": frame_id,
                            "sequence": sequence,
                            "config_revision": policy["revision"],
                            "age_ms": 0.0,
                            "deadline_ms": policy["decision_budget_ms"],
                            **{
                                key: policy[key]
                                for key in (
                                    "mode",
                                    "corridor",
                                    "verified",
                                    "calibration_id",
                                    "model_sha256",
                                    "perception",
                                )
                            },
                        }
                    )
                )
                if self.drop_link:
                    await asyncio.sleep(policy["decision_budget_ms"] / 1000)
                    raise TimeoutError("replay_link_dropped")
                try:
                    decision = await self.request(frame.model_dump(), self.payload)
                except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                    retryable = not isinstance(
                        exc, httpx.HTTPStatusError
                    ) or exc.response.status_code in {408, 429, 500, 502, 503, 504}
                    if policy["mode"] != "observe" or not retryable:
                        raise
                    self.status.update(
                        decision={
                            "state": "unknown",
                            "action": "stop",
                            "vision_action": "stop",
                            "reason": "vision_inference_unavailable",
                            "clear_count": 0,
                        },
                        observed_at=time.time(),
                        simulated_moving=False,
                    )
                    await asyncio.sleep(0.2)
                    continue
                accepted = (
                    self.lease.accept(decision, frame_id, sequence, capture)
                    if policy["mode"] == "drive"
                    else False
                )
                self.status.update(
                    decision=decision,
                    observed_at=time.time(),
                    latched=self.lease.latched,
                    stop_reason=self.lease.reason,
                    simulated_moving=accepted,
                    paused=policy["mode"] == "drive"
                    and not accepted
                    and not self.lease.latched,
                    permit_remaining_ms=max(
                        0.0,
                        ((self.lease.deadline or self.clock()) - self.clock()) * 1000,
                    ),
                )
                if self.lease.latched:
                    self.status["running"] = False
                await asyncio.sleep(0.03 if policy["mode"] == "drive" else 0.2)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.lease.halt("replay_transport_failed")
            self.status.update(
                running=False,
                latched=True,
                stop_reason=self.lease.reason,
                simulated_moving=False,
                observed_at=time.time(),
            )
