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


@router.post("/perception/prepare")
async def prepare(request: Request):
    """Explicit Analysis loads the selection and recovers a failed observation."""
    await manager(request).prepare_observation(
        request.app.state.rider, request.headers.get("X-Rider-Session")
    )
    from .vision_routes import loop_goals

    domain = request.app.state.rider
    session = request.headers.get("X-Rider-Session")
    status = (await domain.read("/api/navigation/status", 0, force=True))["data"]
    goals = await loop_goals(domain)
    if (
        goals.get("observe") is True
        and goals.get("drive") is False
        and status.get("mode") == "observe"
        and status.get("running") is False
    ):
        # A failed worker can leave its requested goal ON. Only this explicit
        # operator action retires it and starts a new observe-only producer.
        await domain.goal("s15_venom_perception", {"enabled": False}, session)
        await domain.goal("s15_venom_perception", {"enabled": True}, session)
    return {"ok": True}


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
