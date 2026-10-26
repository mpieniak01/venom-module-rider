"""Pulse trial presentation uses the same domain/control service as manual motion."""

from typing import Annotated, Literal

from fastapi import APIRouter, Header, Request
from pydantic import Field, StrictInt, model_validator

from ..errors import RiderError
from .routing import RiderRoute
from .schemas import Command

router = APIRouter(route_class=RiderRoute)
Session = Annotated[str | None, Header(alias="X-Rider-Session")]


class TrialStart(Command):
    action: Literal["arm"] = "arm"
    owner: Literal["manual"] = "manual"
    arming_mode: Literal["operator"] = "operator"
    trial_profile_id: Literal["A", "B", "C"]
    trial_version: Annotated[StrictInt, Field(ge=1, le=1)]
    trial_context: Annotated[str, Field(strict=True, max_length=80)] = ""

    @model_validator(mode="after")
    def consent(self):
        if (
            self.operator_consent is not True
            or not self.operator_plan
            or not self.operator_plan.strip()
            or not self.panel_id
            or not self.panel_id.strip()
            or self.settings_revision is None
        ):
            raise ValueError("pulse_trial_consent_required")
        allowed = {
            "action",
            "panel_id",
            "session",
            "request_id",
            "source",
            "execution_context",
            "generation",
            "seq",
            "ts",
            "ttl_ms",
            "settings_revision",
            "operator_consent",
            "operator_plan",
            "owner",
            "arming_mode",
            "trial_profile_id",
            "trial_version",
            "trial_context",
        }
        if set(self.model_dump(exclude_none=True)) - allowed:
            raise ValueError("pulse_trial_ambiguous")
        return self


@router.get("/motion/trials/profiles")
async def profiles(request: Request):
    return await request.app.state.rider.read("/api/motion/trials/profiles", 0.5)


@router.post("/motion/trials")
async def start(payload: TrialStart, request: Request, session: Session = None):
    return await request.app.state.rider.control(
        payload.model_dump(exclude_none=True, exclude={"session"}),
        session or payload.session,
    )


@router.get("/motion/trials/{request_id}")
async def status(request_id: str, request: Request):
    if (
        not request_id
        or len(request_id) > 128
        or any(
            char
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for char in request_id
        )
    ):
        raise RiderError("pulse_trial_request_invalid", 400)
    domain = request.app.state.rider
    result = await domain.read("/api/motion/trials/" + request_id, 0, force=True)
    # Retain the source's complete terminal series in the existing evidence writer.
    if result["data"].get("trial", {}).get("phase") in {
        "completed",
        "failed",
        "cancelled",
    }:
        from ..pulse_trials import retain_terminal

        control = await domain.read("/api/control/state", 0, force=True)
        await retain_terminal(domain, control)
    return result
