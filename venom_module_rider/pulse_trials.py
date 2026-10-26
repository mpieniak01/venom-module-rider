"""Bounded archived pulse-series evidence; no permissions or actuator policy."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class TrialEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    kind: Literal[
        "pulse_started",
        "output_started",
        "pulse_zero",
        "gap_started",
        "completed",
        "cancelled",
        "failed",
    ]
    pulse_index: int = Field(ge=0, le=5)
    elapsed_ms: float = Field(ge=0, le=1e9)


class TrialEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    version: Literal[1]
    run_id: str = Field(min_length=1, max_length=128)
    profile_id: Literal["A", "B", "C"]
    profile_version: Literal[1]
    count: int = Field(ge=1, le=5)
    pulse_ms: int = Field(ge=100, le=500)
    gap_ms: int = Field(ge=0, le=100)
    direction: Literal["forward"]
    settings_revision: StrictInt
    generation: str = Field(min_length=1, max_length=128)
    group_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    common: dict[str, Any]
    phase: Literal["pulse", "gap", "completed", "cancelled", "failed"]
    reason: str = Field(min_length=1, max_length=128)
    pulse_index: int = Field(ge=0, le=5)
    completed_pulses: int = Field(ge=0, le=5)
    elapsed_ms: float = Field(ge=0, le=1e9)
    events: list[TrialEvent] = Field(max_length=32)
    physical_completion: Literal["unverified"]

    @model_validator(mode="before")
    @classmethod
    def versions(cls, value):
        if (
            not isinstance(value, dict)
            or type(value.get("version")) is not int
            or type(value.get("profile_version")) is not int
        ):
            raise ValueError("pulse_trial_version_invalid")
        return value

    @model_validator(mode="after")
    def consistent(self):
        expected = {"A": (1, 500, 0), "B": (2, 250, 100), "C": (5, 100, 100)}[
            self.profile_id
        ]
        if (
            (self.count, self.pulse_ms, self.gap_ms) != expected
            or self.completed_pulses > self.count
            or self.pulse_index > self.count
        ):
            raise ValueError("pulse_trial_recipe_mismatch")
        if self.phase == "completed" and self.completed_pulses != self.count:
            raise ValueError("pulse_trial_incomplete")
        times = [event.elapsed_ms for event in self.events]
        if times != sorted(times) or times and times[-1] > self.elapsed_ms:
            raise ValueError("pulse_trial_event_order")
        return self


def trial_evidence(value):
    return TrialEvidence.model_validate(value).model_dump(mode="json")


def series_timeline(rows, origin):
    """Pi-relative events anchored to first host receipt; not clock-sync proof."""
    series: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = (row.get("executor") or {}).get("pulse_trial")
        if value is None:
            continue
        run = trial_evidence(value)
        identity = run["run_id"]
        old = series.get(identity)
        if old is not None and (
            old["phase"] not in {"pulse", "gap"}
            or old["elapsed_ms"] > run["elapsed_ms"]
        ):
            continue
        anchor = (
            old["t"]
            if old is not None
            else row["received_mono"] - origin - run["elapsed_ms"] / 1000
        )
        series[identity] = {
            **run,
            "t": anchor,
            "clock": "pi-relative;host-reception-anchor",
            "clock_sync_uncertainty_ms": None,
        }
    return list(series.values())


async def retain_terminal(domain, payload):
    """Flush one bounded terminal receipt before the panel ends its recording."""
    registry = domain.samples
    if registry is None or domain.device is None:
        return
    cached = domain.read_observations.get("/api/control/state")
    if cached is None:
        return
    registry.evidence.observe(
        "/api/control/state",
        payload,
        domain.device.id,
        domain.session,
        cached[0],
        cached[1],
        retain=True,
    )
    await registry.read(lambda: None)
