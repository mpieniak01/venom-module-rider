"""Pi-authenticated binary frames; operators configure the authoritative Pi."""

from fastapi import APIRouter, Request
from pydantic import ValidationError

from ..errors import RiderError
from ..vision_contract import VisionFrame
from .compute_routes import authenticate, bounded_body
from .routing import RiderRoute

router = APIRouter(route_class=RiderRoute)


@router.get("/compute/vision/status")
async def detector_status(request: Request):
    authenticate(request)
    return request.app.state.rider_vision.status()


@router.post("/compute/vision/decision")
async def decision(request: Request):
    device = authenticate(request)
    if request.headers.get("Content-Type", "").split(";")[0] not in {
        "image/jpeg",
        "image/png",
    }:
        raise RiderError("vision_input_format", 415)
    raw = request.headers.get("X-Rider-Vision", "")
    if len(raw.encode()) > 4096:
        raise RiderError("vision_invalid_frame", 400)
    try:
        frame = VisionFrame.model_validate_json(raw)
    except (ValueError, ValidationError) as exc:
        raise RiderError("vision_invalid_frame", 400) from exc
    body = await bounded_body(request, 512 * 1024)
    return await request.app.state.rider_vision.decide(device, frame, body)


@router.get("/navigation/config")
async def configuration(request: Request):
    domain = request.app.state.rider
    config = await domain.read("/api/navigation/config", 0.2, force=True)
    state = await domain.read("/api/navigation/status", 0.2, force=True)
    return {
        "status": state["data"],
        "config": config["data"],
        "detector": request.app.state.rider_vision.status(),
    }


@router.post("/navigation/config")
async def configure(request: Request):
    body = await request.json()
    return await request.app.state.rider.mutate(
        "/api/navigation/config", body, request.headers.get("X-Rider-Session")
    )
