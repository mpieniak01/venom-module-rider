"""Pi-authenticated binary frames; operators configure the authoritative Pi."""

from fastapi import APIRouter, Request
from pydantic import ValidationError

from ..errors import RiderError
from ..vision_contract import VisionFrame
from .compute_routes import authenticate, bounded_body
from .routing import RiderRoute
from .schemas import NavigationTrialRequest

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
    try:
        config = await domain.read("/api/navigation/config", 0.2, force=True)
    except RiderError as exc:
        if exc.status == 404:
            raise RiderError("navigation_api_unavailable", 503) from exc
        raise
    state = await domain.read("/api/navigation/status", 0.2, force=True)
    goals = await loop_goals(domain)
    catalog = await domain.read("/api/logic/catalog", 1.0, force=True)
    features = catalog["data"].get("features")
    rows = features if isinstance(features, list) else []
    runtime = {
        key: any(
            row.get("name") == name
            and row.get("installed") is True
            and row.get("start_available") is not False
            for row in rows
            if isinstance(row, dict)
        )
        for key, name in (
            ("observe", "s15_venom_perception"),
            ("drive", "s14_venom_navigation"),
        )
    }
    drive_row = next(
        (
            row
            for row in rows
            if isinstance(row, dict) and row.get("name") == "s14_venom_navigation"
        ),
        {},
    )
    return {
        "runtime": {
            **runtime,
            "known": isinstance(features, list),
            "drive_reason": drive_row.get("start_unavailable_reason"),
        },
        "goals": goals,
        "status": state["data"],
        "config": config["data"],
        "detector": request.app.state.rider_vision.status(
            config["data"].get("perception")
        ),
    }


@router.post("/navigation/config")
async def configure(request: Request):
    body = await request.json()
    return await request.app.state.rider.mutate(
        "/api/navigation/config", body, request.headers.get("X-Rider-Session")
    )


@router.get("/navigation/state")
async def navigation_state(request: Request):
    state = await request.app.state.rider.read(
        "/api/navigation/status", 0.2, force=True
    )
    config = await request.app.state.rider.read("/api/navigation/config", 0.2)
    return {
        "goals": await loop_goals(request.app.state.rider),
        "status": state["data"],
        "detector": request.app.state.rider_vision.status(
            config["data"].get("perception")
        ),
    }


async def loop_goals(domain, *, force=True):
    response = await domain.read("/api/logic/state", 0.2, force=force)
    state = response["data"]
    state = state.get("state", state)
    features = state.get("features", {}) if isinstance(state, dict) else {}
    if isinstance(features, list):
        rows = {row.get("name"): row for row in features if isinstance(row, dict)}
    elif isinstance(features, dict):
        rows = features
    else:
        return {}
    result = {}
    for mode, name in (
        ("observe", "s15_venom_perception"),
        ("drive", "s14_venom_navigation"),
    ):
        row = rows.get(name)
        if not isinstance(row, dict) or row.get("stale") is True:
            continue
        requested = row.get("requested", row.get("enabled"))
        if isinstance(requested, bool):
            result[mode] = requested
    return result


@router.post("/navigation/trial")
async def start_trial(request: Request, body: NavigationTrialRequest):
    # Preserve the original Pi-clock intent. Pi owns preparation, arming and
    # cancellation; a browser or host cannot redate an older start request.
    return await request.app.state.rider.mutate(
        "/api/navigation/trial",
        body.model_dump(),
        request.headers.get("X-Rider-Session"),
    )
