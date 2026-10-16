"""Explicit automatic collection, using the existing sample/domain services."""

from typing import Annotated

from fastapi import APIRouter, Header, Request

from ..errors import RiderError
from ..event_policy import EventProfile
from ..samples_contract import RecordID, RequestID
from .routing import RiderRoute
from .samples_routes import registry
from .schemas import StrictModel

router = APIRouter(prefix="/samples/automatic", route_class=RiderRoute)


class EnableAutomatic(StrictModel):
    session_id: RecordID
    request_id: RequestID
    profile: EventProfile = EventProfile()


@router.get("")
async def status(request: Request):
    return registry(request).automatic.status()


@router.get("/runs/{run_id}")
async def history(request: Request, run_id: str):
    domain = registry(request)
    return await domain.read(domain.automatic.store.page, run_id)


@router.post("")
async def start(
    request: Request,
    body: EnableAutomatic,
    session: Annotated[str | None, Header(alias="X-Rider-Session")] = None,
):
    source = request.app.state.rider
    source.check_session(session)
    if source.device is None:
        raise RiderError("device_not_selected", 409)
    return await registry(request).automatic.start(
        body.session_id,
        body.request_id,
        source.device.id,
        source.session,
        body.profile.model_dump(),
    )


@router.post("/stop")
async def stop(request: Request):
    # This stops data collection, never a hardware command or a new image read.
    return await registry(request).automatic.stop()
