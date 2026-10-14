"""Recorded camera replay against Venom; actuation is an in-memory fixture only."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi.responses import Response

from .vision_contract import VisionFrame

LOOP_GOALS = {"s14_venom_navigation": "drive", "s15_venom_perception": "observe"}
DEFAULT_POLICY: dict[str, Any] = {
    "protocol_version": 1,
    "revision": 0,
    "mode": "observe",
    "speed": 0.1,
    "decision_budget_ms": 300,
    "max_motion_ms": 150,
    "corridor": [0.35, 0.65, 0.65, 0.95],
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
        token="rider-demo-only-token",
    ):
        if urlsplit(api_url).hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("replay_requires_loopback")
        self.payload, self.lease_factory = payload, lease_factory
        self.recorded_at, self.clock = recorded_at, clock
        self.policy: dict[str, Any] = dict(DEFAULT_POLICY)
        self.policy["corridor"] = list(DEFAULT_POLICY["corridor"])
        self.model_sha, self.api_url, self.token = model_sha, api_url, token
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
        self.http = httpx.AsyncClient(trust_env=False, follow_redirects=False)
        self.motion_generation: str | None = None

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
            and 0.01 <= candidate["speed"] <= 0.15
            and type(candidate["max_motion_ms"]) is int
            and 50 <= candidate["max_motion_ms"] <= 200
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
                "Authorization": "Bearer " + self.token,
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
        self.state.deadline = self.clock() + duration
        self.events.append(
            {
                "action": "go",
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
        self.lease = self.lease_factory(policy, run, self.drive, self.brake, self.clock)
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
        self.task = asyncio.create_task(self.run(policy, run))
        if mode == "drive":
            self.watchdog = asyncio.create_task(self.watch())

    async def watch(self):
        while self.status["running"]:
            if not self.lease.tick():
                self.status.update(
                    running=False,
                    latched=True,
                    stop_reason=self.lease.reason,
                    simulated_moving=False,
                    observed_at=time.time(),
                )
                return
            await asyncio.sleep(0.01)

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
