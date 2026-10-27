"""Own figure recipes use the shared domain service and Pi motion owner."""

import json
import re
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import Response
from pydantic import Field, StrictInt, model_validator

from ..errors import RiderError
from .routing import RiderRoute
from .schemas import Command

router = APIRouter(route_class=RiderRoute)
Session = Annotated[str | None, Header(alias="X-Rider-Session")]


class FigureRequest(Command):
    action: Literal["arm"] = "arm"
    owner: Literal["manual"] = "manual"
    arming_mode: Literal["operator"] = "operator"
    figure_id: Literal[
        "sway", "vertical", "forward_backward", "snake", "lift_rotate", "circular_sway"
    ]
    figure_version: Annotated[StrictInt, Field(ge=1, le=1)]
    figure_run_id: (
        Annotated[str, Field(strict=True, min_length=1, max_length=128)] | None
    ) = None

    @model_validator(mode="after")
    def scope(self):
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
            "figure_id",
            "figure_version",
            "figure_run_id",
        }
        if (
            self.operator_consent is not True
            or not self.operator_plan
            or not self.operator_plan.strip()
            or not self.panel_id
            or not self.request_id
            or self.settings_revision is None
            or set(self.model_dump(exclude_none=True)) - allowed
        ):
            raise ValueError("figure_panel_consent_required")
        return self


def identifier(value):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value):
        raise RiderError("figure_run_invalid", 400)
    return value


@router.get("/motion/figures")
async def catalog(request: Request):
    return await request.app.state.rider.read("/api/motion/figures", 0.5)


@router.post("/motion/figures/runs")
async def start(payload: FigureRequest, request: Request, session: Session = None):
    return await request.app.state.rider.control(
        payload.model_dump(exclude_none=True, exclude={"session"}),
        session or payload.session,
    )


@router.get("/motion/figures/runs/{run_id}")
async def status(run_id: str, request: Request):
    return await request.app.state.rider.read(
        "/api/motion/figures/runs/" + identifier(run_id), 0, force=True
    )


@router.post("/motion/figures/runs/{run_id}/cancel")
async def cancel(run_id: str, request: Request, session: Session = None):
    domain = request.app.state.rider
    domain.check_session(session)
    return await domain.transport().json(
        "/api/motion/figures/runs/" + identifier(run_id) + "/cancel",
        method="POST",
        body={},
        stop=True,
    )


@router.get("/motion/figures/runs/{run_id}/export")
async def export(run_id: str, request: Request):
    domain = request.app.state.rider
    row = await status(run_id, request)
    data = domain.data(row)
    return Response(
        json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="figure-'
            + identifier(run_id)
            + '.json"'
        },
    )
