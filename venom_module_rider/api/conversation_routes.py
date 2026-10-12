"""The operator enables conversation access separately from global arming."""

from fastapi import APIRouter, Header, Request
from pydantic import StrictBool, StrictInt

from ..conversation import create_provider
from .routing import RiderRoute
from .schemas import StrictModel

router = APIRouter(route_class=RiderRoute)


class Configuration(StrictModel):
    enabled: StrictBool
    revision: StrictInt


@router.get("/conversation/config")
async def config(request: Request):
    return create_provider(request.app).config()


@router.post("/conversation/config")
async def configure(
    payload: Configuration,
    request: Request,
    x_rider_session: str | None = Header(default=None),
):
    return create_provider(request.app).configure(
        payload.enabled, payload.revision, x_rider_session
    )
