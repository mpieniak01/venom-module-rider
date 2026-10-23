"""Operator and Pi catalog views share the same host-owned model service."""

from fastapi import APIRouter, Request

from ..errors import RiderError
from .compute_routes import authenticate
from .routing import RiderRoute

router = APIRouter(route_class=RiderRoute)


def manager(request):
    result = getattr(request.app.state, "rider_models", None)
    if result is None:
        raise RiderError("model_runtime_unavailable", 503)
    return result


def selected_pi(request):
    device = authenticate(request)
    domain = request.app.state.rider
    if domain.transport().device.id != device:
        raise RiderError("stream_device_not_selected", 409)
    return domain


@router.get("/perception/models")
async def models(request: Request):
    return await manager(request).snapshot(request.app.state.rider)


@router.post("/perception/models/{model_id}/install", status_code=202)
async def install(model_id: str, request: Request):
    request.app.state.rider.check_session(request.headers.get("X-Rider-Session"))
    return {"job": manager(request).installer.submit(model_id)}


@router.post("/perception/models/{model_id}/install/cancel")
async def cancel(model_id: str, request: Request):
    request.app.state.rider.check_session(request.headers.get("X-Rider-Session"))
    return {"job": manager(request).installer.cancel(model_id)}


@router.post("/perception/selection")
async def select(request: Request):
    return await manager(request).select(
        request.app.state.rider,
        await request.json(),
        request.headers.get("X-Rider-Session"),
    )


@router.get("/compute/vision/models")
async def pi_models(request: Request):
    return await manager(request).snapshot(selected_pi(request))


@router.post("/compute/vision/model")
async def pi_select(request: Request):
    domain = selected_pi(request)
    return await manager(request).select(domain, await request.json(), domain.session)
