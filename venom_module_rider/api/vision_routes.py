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
    result = request.app.state.rider_vision.status()
    if request.query_params.get("stream") == "true":
        from .pipeline_routes import native_pipeline

        domain, hub = request.app.state.rider, request.app.state.rider_stream
        pipeline = await native_pipeline(domain, device=authenticate(request))
        async with hub.lease(domain, pipeline, keepalive=True) as connection:
            try:
                await hub.frame(connection, timeout=0.5)
            except RiderError:
                pass
            result["stream"] = hub.status(connection)
    return result


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


@router.post("/compute/vision/decision-reference")
async def reference_decision(request: Request):
    from ..stream_contract import ReferenceDecision
    from .pipeline_routes import native_pipeline, native_ticket, source_headers

    device = authenticate(request)
    admitted_at = request.app.state.rider_stream.clock()
    if request.headers.get("Content-Type", "").split(";")[0] != "application/json":
        raise RiderError("vision_input_format", 415)
    try:
        descriptor = ReferenceDecision.model_validate_json(
            await bounded_body(request, 16384)
        )
    except (ValueError, ValidationError) as exc:
        raise RiderError("vision_invalid_frame", 400) from exc
    domain, hub, loop = (
        request.app.state.rider,
        request.app.state.rider_stream,
        request.app.state.rider_vision,
    )
    pipeline = await native_pipeline(domain, device=device, source=descriptor.source)
    if (pipeline.get("active_profile") or {}).get("analysis_location") != "venom":
        raise RiderError("vision_remote_disabled", 409)
    if (
        descriptor.source.transport_epoch != pipeline["transport_epoch"]
        or descriptor.source.pipeline_revision != pipeline["revision"]
        or descriptor.source.camera_session != pipeline["camera_session"]
    ):
        raise RiderError("stream_epoch_changed", 409)
    frame = descriptor.frame
    if frame.age_ms >= frame.deadline_ms:
        raise RiderError("vision_frame_expired", 409)
    async with hub.lease(domain, pipeline, keepalive=True) as connection:
        source = await hub.frame(
            connection,
            source=descriptor.source,
            timeout=min(0.3, (frame.deadline_ms - frame.age_ms) / 1000),
        )
        canonical = await hub.canonical_frame(connection, source)
        frame = frame.model_copy(
            update={
                "age_ms": max(
                    frame.age_ms + (hub.clock() - admitted_at) * 1000,
                    source.age_ms(hub.clock()),
                )
            }
        )
        if frame.age_ms >= frame.deadline_ms:
            raise RiderError("vision_frame_expired", 409)
        result = await loop.decide(
            device,
            frame,
            canonical,
            input_ticket=native_ticket(
                domain, source, canonical, source_headers(source, hub.clock())
            ),
            source_key=(
                source.source.identity(),
                source.source.image_transform.model_dump_json(),
            ),
            source_reference=descriptor.source.model_dump(mode="json"),
        )
        if connection.closed:
            loop.previews.pop(device, None)
            raise RiderError("stream_epoch_changed", 409)
        return result


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
        "config": config["data"],
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
