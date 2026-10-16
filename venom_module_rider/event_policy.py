"""Bounded event selection on existing results; no model, IO or robot commands."""

from __future__ import annotations

import math
import re
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, StrictFloat, StrictInt, field_validator

from .api.schemas import StrictModel


class EventProfile(StrictModel):
    events: Annotated[
        list[Literal["new_obstacle", "state_change", "uncertainty", "new_scene"]],
        Field(min_length=1, max_length=4),
    ] = ["new_obstacle", "uncertainty", "new_scene"]
    stable_frames: Annotated[StrictInt, Field(ge=1, le=20)] = 3
    stable_ms: Annotated[StrictInt, Field(ge=0, le=5000)] = 250
    cooldown_ms: Annotated[StrictInt, Field(ge=500, le=60000)] = 3000
    scene_distance_bits: Annotated[StrictInt, Field(ge=1, le=64)] = 10
    burst_frames: Annotated[StrictInt, Field(ge=1, le=5)] = 1
    burst_interval_ms: Annotated[StrictInt, Field(ge=150, le=2000)] = 200
    max_samples: Annotated[StrictInt, Field(ge=1, le=150)] = 50
    duration_s: Annotated[StrictFloat, Field(ge=1, le=1800)] = 300.0
    max_gap_ms: Annotated[StrictInt, Field(ge=500, le=5000)] = 1500

    @field_validator("events")
    @classmethod
    def unique_events(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("auto_events_duplicate")
        return value


@dataclass(frozen=True)
class Observation:
    device: str
    source_session: str
    run: str
    revision: int
    model: str
    engine: str
    frame_id: str
    sequence: int
    sha256: str
    at: float
    expires_at: float
    state: str
    reason: str
    scene_hash: int | None
    historical: bool = False


@dataclass(frozen=True)
class Event:
    event_id: str
    reasons: tuple[str, ...]
    trigger: Observation
    source: Observation
    burst_index: int


class EventSelector:
    """One explicit enable epoch. The writer supplies exact fresh observations."""

    def __init__(self, device, source_session, profile, now):
        self.device, self.source_session = device, source_session
        self.profile = EventProfile.model_validate(profile).model_copy(deep=True)
        self.started = self.last_seen = now
        self.binding = None
        self.last_sequence = -1
        self.last_frame = None
        self.pending = None
        self.pending_since = now
        self.stable_count = 0
        self.stable_state = None
        self.last_scene = None
        self.last_event = float("-inf")
        self.last_sample = float("-inf")
        self.last_sha = None
        self.burst = None
        self.burst_left = 0
        self.attempts = 0
        self.seen: deque[tuple[str, int | None]] = deque(maxlen=64)
        self.stopped = None
        self.rejected = self.deduplicated = self.suppressed = 0

    def stop(self, reason):
        self.stopped = reason
        self.burst = None
        self.burst_left = 0

    def tick(self, now):
        if not math.isfinite(now) or now < self.started:
            self.stop("auto_clock_invalid")
        elif now - self.started >= self.profile.duration_s:
            self.stop("auto_duration_limit")
        elif (now - self.last_seen) * 1000 > self.profile.max_gap_ms:
            self.stop("auto_feed_lost")
        elif self.attempts >= self.profile.max_samples:
            self.stop("auto_sample_limit")
        return self.stopped

    @staticmethod
    def distance(first, second):
        return (first ^ second).bit_count()

    def evaluate(self, observation, now):
        if self.tick(now):
            return None
        item = observation
        if item.device != self.device or item.source_session != self.source_session:
            self.rejected += 1
            return None
        if (
            item.historical
            or not math.isfinite(item.at)
            or not math.isfinite(item.expires_at)
            or item.at > now
            or now > item.expires_at
        ):
            self.stop("auto_source_stale")
            return None
        if (
            any(
                type(value) is not str or not value
                for value in (
                    item.state,
                    item.run,
                    item.frame_id,
                    item.model,
                    item.sha256,
                    item.engine,
                )
            )
            or item.state not in {"clear", "obstacle", "unknown"}
            or not item.run
            or not item.frame_id
            or type(item.sequence) is not int
            or item.sequence < 0
            or type(item.revision) is not int
            or item.revision < 0
            or re.fullmatch("[a-f0-9]{64}", item.model) is None
            or re.fullmatch("[a-f0-9]{64}", item.sha256) is None
            or not item.engine
            or item.scene_hash is not None
            and (type(item.scene_hash) is not int or not 0 <= item.scene_hash < 2**64)
        ):
            self.rejected += 1
            return None
        binding = (item.run, item.revision, item.model, item.engine)
        if self.binding is None:
            self.binding = binding
        elif self.binding != binding:
            self.stop("auto_source_changed")
            return None
        if item.sequence <= self.last_sequence or item.frame_id == self.last_frame:
            self.rejected += 1
            return None
        self.last_sequence, self.last_frame = item.sequence, item.frame_id
        self.last_seen = now
        if (
            self.burst_left
            and (now - self.last_sample) * 1000 >= self.profile.burst_interval_ms
        ):
            if item.sha256 == self.last_sha:
                self.deduplicated += 1
                return None
            trigger = self.burst
            if trigger is None:
                self.stop("auto_state_invalid")
                return None
            self.burst_left -= 1
            self.attempts += 1
            self.last_sample, self.last_sha = now, item.sha256
            return Event(
                trigger.event_id,
                trigger.reasons,
                trigger.trigger,
                item,
                self.profile.burst_frames - self.burst_left - 1,
            )
        candidate = (item.state, item.scene_hash)
        compatible = (
            self.pending is not None
            and self.pending[0] == item.state
            and (
                item.scene_hash is None
                and self.pending[1] is None
                or item.scene_hash is not None
                and self.pending[1] is not None
                and self.distance(item.scene_hash, self.pending[1]) <= 3
            )
        )
        if not compatible:
            self.pending, self.pending_since, self.stable_count = candidate, now, 1
        else:
            self.stable_count += 1
        if (
            self.stable_count < self.profile.stable_frames
            or (now - self.pending_since) * 1000 < self.profile.stable_ms
        ):
            return None
        reasons = []
        if item.state == "obstacle" and self.stable_state != "obstacle":
            reasons.append("new_obstacle")
        if item.state == "unknown" and self.stable_state != "unknown":
            reasons.append("uncertainty")
        if self.stable_state is not None and item.state != self.stable_state:
            reasons.append("state_change")
        if item.scene_hash is not None and (
            self.last_scene is None
            or self.distance(item.scene_hash, self.last_scene)
            >= self.profile.scene_distance_bits
        ):
            reasons.append("new_scene")
        self.stable_state = item.state
        reasons = [reason for reason in reasons if reason in self.profile.events]
        if not reasons:
            return None
        if (now - self.last_event) * 1000 < self.profile.cooldown_ms:
            self.suppressed += 1
            return None
        if item.sha256 == self.last_sha or any(
            state == item.state
            and (
                scene is None
                and item.scene_hash is None
                or scene is not None
                and item.scene_hash is not None
                and self.distance(scene, item.scene_hash)
                < self.profile.scene_distance_bits
            )
            for state, scene in self.seen
        ):
            self.deduplicated += 1
            return None
        self.seen.append((item.state, item.scene_hash))
        if item.scene_hash is not None:
            self.last_scene = item.scene_hash
        self.last_event = self.last_sample = now
        self.last_sha = item.sha256
        self.attempts += 1
        event = Event(uuid.uuid4().hex, tuple(reasons), item, item, 0)
        self.burst, self.burst_left = event, self.profile.burst_frames - 1
        return event
