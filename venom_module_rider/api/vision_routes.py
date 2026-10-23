"""Pi-authenticated binary frames; operators configure the authoritative Pi."""

import asyncio

from fastapi import APIRouter, Request
from pydantic import ValidationError

from ..errors import RiderError
from ..vision_contract import VisionFrame
from .compute_routes import authenticate, bounded_body
from .routing import RiderRoute
from .schemas import NavigationJourneyRequest, NavigationTrialRequest

router = APIRouter(route_class=RiderRoute)


@router.get("/compute/vision/status")
async def detector_status(request: Request):
    authenticate(request)
    models = getattr(request.app.state, "rider_models", None)
    perception = None
    if models is not None:
        domain = request.app.state.rider
        if domain.transport().device.id == authenticate(request):
            perception = (await domain.read("/api/navigation/config", 0.2))["data"].get(
                "perception"
            )
    result = request.app.state.rider_vision.status(perception)
    if request.query_params.get("stream") == "true":
        from .pipeline_routes import native_pipeline

        domain, hub = request.app.state.rider, request.app.state.rider_stream
        pipeline = await native_pipeline(
            domain, device=authenticate(request), preparing=True
        )
        if pipeline.get("state") == "failed":
            raise RiderError("stream_unavailable", 503)
        if pipeline.get("state") not in {"active", "waiting_frame"} or not pipeline.get(
            "camera_session"
        ):
            # A cold producer has no admissible source yet. Retire the previous
            # authority instead of returning its cached readiness or raising a
            # fatal HTTP error during the caller's bounded preparation window.
            await hub.invalidate()
            result["stream"] = {
                "ready": False,
                "latest_source": None,
                "frame_age_ms": None,
                "error": "stream_not_ready",
            }
            return result
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
    await validate_model_policy(request, device, frame)
    body = await bounded_body(request, 512 * 1024)
    from .pipeline_routes import snapshot_decision_allowed

    await snapshot_decision_allowed(request.app.state.rider, device)
    return await request.app.state.rider_vision.decide(device, frame, body)


@router.post("/compute/vision/decision-reference")
async def reference_decision(request: Request):
    from ..stream_contract import ReferenceDecision

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
    hub = request.app.state.rider_stream
    remaining = (descriptor.frame.deadline_ms - descriptor.frame.age_ms) / 1000
    remaining -= max(0.0, hub.clock() - admitted_at)
    if remaining <= 0:
        raise RiderError("vision_frame_expired", 409)
    try:
        # Source lookup and canonical JPEG preparation are inside the same
        # original budget as inference. Waiting here cannot age a frame out
        # and then enqueue another expensive model call.
        async with asyncio.timeout(remaining):
            return await _reference_decision(request, device, descriptor, admitted_at)
    except TimeoutError as exc:
        raise RiderError("vision_frame_expired", 409) from exc


async def _reference_decision(request, device, descriptor, admitted_at):
    from ..sample_sources import native_ticket
    from ..stream_contract import source_headers
    from .pipeline_routes import native_pipeline

    domain, hub, loop = (
        request.app.state.rider,
        request.app.state.rider_stream,
        request.app.state.rider_vision,
    )
    # Independent read-only checks share the original deadline. Serial Pi
    # roundtrips here previously consumed most of a short capture budget.
    phases = {}

    async def measured(name, operation):
        started = hub.clock()
        try:
            return await operation
        finally:
            phases[name] = round(max(0.0, hub.clock() - started) * 1000, 2)

    checks = [
        asyncio.create_task(
            measured(
                "pipeline_ms",
                native_pipeline(domain, device=device, source=descriptor.source),
            )
        ),
        asyncio.create_task(
            measured(
                "policy_ms", validate_model_policy(request, device, descriptor.frame)
            )
        ),
    ]
    try:
        pipeline, _ = await asyncio.gather(*checks)
    finally:
        # A rejected check cannot leave another request running beyond admission.
        for check in checks:
            if not check.done():
                check.cancel()
        await asyncio.gather(*checks, return_exceptions=True)
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
        source = await measured(
            "source_wait_ms",
            hub.frame(
                connection,
                source=descriptor.source,
                timeout=min(0.3, (frame.deadline_ms - frame.age_ms) / 1000),
            ),
        )
        canonical = await measured(
            "canonical_ms", hub.canonical_frame(connection, source)
        )
        if connection.closed or domain.transport().device.id != device:
            raise RiderError("stream_epoch_changed", 409)
        now = hub.clock()
        elapsed_ms = max(0.0, (now - admitted_at) * 1000)
        observed_age_ms = source.source.frame_age_ms + max(
            0.0, (now - source.received_mono) * 1000
        )
        # The status anchor includes up to a whole HTTP roundtrip of uncertainty.
        # Its upper bound protects previews/cache, but is not the Pi's exact TTL.
        # Match binary decisions: authenticated Pi age plus host processing, and
        # never ignore time this exact part already spent in the host buffer.
        # Pi still checks its own capture deadline before accepting/submitting.
        timing = {
            **phases,
            "pi_reported_age": round(frame.age_ms, 2),
            "host_before_inference": round(elapsed_ms, 2),
            "observed_age": round(observed_age_ms, 2),
            "upper_age": round(source.age_ms(now), 2),
        }
        frame = frame.model_copy(
            update={"age_ms": max(frame.age_ms + elapsed_ms, observed_age_ms)}
        )
        timing["deadline_age"] = round(frame.age_ms, 2)
        if frame.age_ms >= frame.deadline_ms:
            raise RiderError("vision_frame_expired", 409)
        result = await loop.decide(
            device,
            frame,
            canonical,
            input_ticket=native_ticket(
                domain, canonical, source_headers(source, hub.clock())
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
        return {**result, "source_timing_ms": timing}


def profile_presentation(config, detector):
    """Expose pair support without mutating a cached authoritative Pi snapshot."""
    result = dict(config)
    result["model_tuning_supported"] = (
        config.get("model_tuning_supported") is True
        and detector.get("semantic_model_policy_v1") is True
    )
    catalog = result.get("behavior_catalog")
    if isinstance(catalog, dict):
        result["behavior_catalog"] = {
            **catalog,
            "supported": catalog.get("supported") is True
            and detector.get("temporal_navigation_v1") is True,
        }
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
    detector = request.app.state.rider_vision.status(config["data"].get("perception"))
    return {
        "runtime": {
            **runtime,
            "known": isinstance(features, list),
            "drive_reason": drive_row.get("start_unavailable_reason"),
        },
        "goals": goals,
        "status": state["data"],
        "config": profile_presentation(config["data"], detector),
        "detector": detector,
    }


@router.post("/navigation/config")
async def configure(request: Request):
    body = await request.json()
    values = body.get("values") if isinstance(body, dict) else None
    if not isinstance(values, dict):
        raise RiderError("navigation_configuration_invalid", 400)
    detector = request.app.state.rider_vision.status()
    behavior = values.get("behavior")
    if (
        isinstance(behavior, dict)
        and behavior.get("profile_id") != "legacy"
        and detector.get("temporal_navigation_v1") is not True
    ):
        raise RiderError("temporal_navigation_update_required", 409)
    perception = values.get("perception")
    if (
        isinstance(perception, dict)
        and perception.get("semantic_models")
        and detector.get("semantic_model_policy_v1") is not True
    ):
        raise RiderError("navigation_model_tuning_update_required", 409)
    result = await request.app.state.rider.mutate(
        "/api/navigation/config", body, request.headers.get("X-Rider-Session")
    )
    data = result.get("data")
    if isinstance(data, dict) and isinstance(data.get("config"), dict):
        result = {
            **result,
            "data": {**data, "config": profile_presentation(data["config"], detector)},
        }
    return result


@router.get("/navigation/state")
async def navigation_state(request: Request):
    state = await request.app.state.rider.read(
        "/api/navigation/status", 0.2, force=True
    )
    config = await request.app.state.rider.read("/api/navigation/config", 0.2)
    detector = request.app.state.rider_vision.status(config["data"].get("perception"))
    return {
        "goals": await loop_goals(request.app.state.rider),
        "status": state["data"],
        "config": profile_presentation(config["data"], detector),
        "detector": detector,
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
        body.model_dump(exclude_none=True),
        request.headers.get("X-Rider-Session"),
    )


@router.post("/navigation/journey")
async def start_journey(request: Request, body: NavigationJourneyRequest):
    # One original operator intent. Pi validates capability, policy, generation,
    # calibration and executor readiness before granting journey authority.
    return await request.app.state.rider.mutate(
        "/api/navigation/journey",
        body.model_dump(),
        request.headers.get("X-Rider-Session"),
    )


async def validate_model_policy(request, device, frame):
    """A late or forged model identity cannot replace the Pi selection."""
    models = getattr(request.app.state, "rider_models", None)
    if models is None:
        return
    domain = request.app.state.rider
    if domain.transport().device.id != device:
        raise RiderError("stream_device_not_selected", 409)
    policy = (await domain.read("/api/navigation/config", 0.2))["data"]
    from ..navigation_profiles import DEFAULT_BEHAVIOR, validate_behavior
    from ..perception_catalog import selected_identity

    expected_behavior = validate_behavior(policy.get("behavior", DEFAULT_BEHAVIOR))
    if expected_behavior != frame.behavior.model_dump() or (
        expected_behavior["profile_id"] != "legacy"
        and policy.get("revision") != frame.config_revision
    ):
        raise RiderError("navigation_behavior_conflict", 409)
    expected_perception = policy.get("perception", {})
    if frame.perception.semantic_models or expected_perception.get("semantic_models"):
        from ..depth_detector import PerceptionSettings

        if (
            policy.get("revision") != frame.config_revision
            or PerceptionSettings.model_validate(expected_perception).model_dump()
            != frame.perception.model_dump()
        ):
            raise RiderError("navigation_model_tuning_conflict", 409)
    if frame.perception.model_id or policy.get("perception", {}).get("model_id"):
        selected = selected_identity(policy)
        if (
            selected["revision"] != frame.config_revision
            or selected["model_id"] != frame.perception.model_id
            or selected["artifact_sha256"] != frame.perception.artifact_sha256
        ):
            raise RiderError("model_selection_conflict", 409)
        models.observe_policy(device, policy)
