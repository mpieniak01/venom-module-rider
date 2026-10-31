"""Optional Venom router. Every operator path uses the same Pi domain service."""

from __future__ import annotations

import hmac
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response
from venom_core.core.module_data_policy import ensure_module_mutation_allowed

from ..compute import ComputeService
from ..config import IDENTIFIER, configured_devices
from ..errors import RiderError
from ..service import RESOURCE_NAMES, RiderService
from . import (
    analysis_routes,
    automatic_routes,
    compute_routes,
    conversation_routes,
    figure_routes,
    motion_evidence_routes,
    perception_routes,
    pipeline_routes,
    pulse_trial_routes,
    samples_routes,
    vision_routes,
)
from .routing import RiderRoute
from .schemas import (
    Command,
    ConnectionRefresh,
    GoalAction,
    ResourceAction,
    Selection,
    SettingsPatch,
)

UNIT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_@.-]{0,95}\.service")


@asynccontextmanager
async def lifespan(app):
    existing = getattr(app.state, "rider", None)
    selection_file = os.getenv(
        "RIDER_SELECTION_FILE", "data/modules/rider/panel-selection.json"
    )
    service = existing or RiderService(
        configured_devices(),
        selection_file=Path(selection_file) if selection_file else None,
    )
    app.state.rider = service
    from ..engines import VenomEngines

    engines: object = VenomEngines(app)
    if (
        os.getenv("RIDER_DEMO_ONLY", "").lower() in {"1", "true"}
        and service.devices
        and all(device.mode == "demo" for device in service.devices.values())
    ):
        from ..compute_demo import DemoEngines

        engines = DemoEngines(app)
    compute = getattr(app.state, "rider_compute", None) or ComputeService(engines)
    app.state.rider_compute = compute
    import asyncio
    import sqlite3

    from ..samples_registry import SamplesRegistry, default_root
    from ..stream_hub import NativeStreamHub
    from ..vision_loop import VisionLoop

    samples = getattr(app.state, "rider_samples", None)
    app.state.rider_samples_error = None
    if samples is None:
        try:
            samples = await asyncio.to_thread(SamplesRegistry, default_root())
        except (OSError, sqlite3.Error, RiderError):
            app.state.rider_samples_error = "samples_storage_unavailable"
    if samples is not None:
        samples.device_modes = {
            key: device.mode for key, device in service.devices.items()
        }
    app.state.rider_samples = samples
    service.samples = samples
    app.state.rider_stream = NativeStreamHub()
    service.media_hub = app.state.rider_stream
    app.state.rider_vision = VisionLoop(
        samples=samples, source_session=lambda: service.session
    )
    app.state.rider_analysis = None
    app.state.rider_analysis_error = None
    from ..perception_service import PerceptionService

    app.state.rider_models = PerceptionService(app.state.rider_vision)
    service.observation_preparer = (
        lambda session: app.state.rider_models.prepare_observation(service, session)
    )
    from ..model_resources import RiderModelResources

    app.state.rider_model_resources = RiderModelResources(app)
    default = os.getenv("RIDER_DEFAULT_DEVICE")
    if existing is None:
        await service.restore(default)
    from ..analysis_startup import AnalysisStartup

    analysis_startup = AnalysisStartup(app, samples) if samples is not None else None
    try:
        yield
    finally:
        provider = getattr(app.state, "rider_conversation", None)
        if provider:
            await provider.close()
        if analysis_startup is not None:
            await analysis_startup.close()
        if samples is not None:
            await samples.close()
        await app.state.rider_stream.close()
        await app.state.rider_models.close()
        await app.state.rider_vision.close()
        await compute.close()
        await service.close()


async def guard(request: Request):
    if compute_routes.device_path(request.url.path):
        _guard_compute_device(request)
        return
    token = os.getenv("RIDER_ACCESS_TOKEN")
    if token and not hmac.compare_digest(
        request.headers.get("Authorization", "").encode("utf-8"),
        ("Bearer " + token).encode("utf-8"),
    ):
        raise RiderError("access_denied", 403)
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    _guard_browser_headers(request)
    try:
        payload = await request.json() if request.method != "DELETE" else {}
    except ValueError as exc:
        raise RiderError("invalid_request", 400) from exc
    if not isinstance(payload, dict):
        raise RiderError("invalid_request", 400)
    # A safety STOP must work in read-only environments as well.
    if _read_only_stop(request.url.path, payload):
        # A scoped cancellation cannot grant authority or start output. Pi
        # still requires the exact operation/panel; shape validation follows.
        return
    if request.url.path.endswith("/face/frame"):
        return
    try:
        ensure_module_mutation_allowed(
            module_id="rider",
            operation_name=request.method.lower() + ":" + request.url.path,
        )
    except PermissionError as exc:
        raise RiderError("environment_read_only", 403) from exc


router = APIRouter(
    prefix="/api/v1/rider",
    tags=["rider"],
    route_class=RiderRoute,
    dependencies=[Depends(guard)],
    lifespan=lifespan,
)


@router.get("/loop-timing")
async def loop_timing(request: Request):
    domain = request.app.state.rider
    return {**domain.timing.snapshot(), "reads": domain.read_metrics.snapshot()}


@router.put("/loop-timing")
async def update_loop_timing(request: Request):
    domain = request.app.state.rider
    domain.check_session(request.headers.get("X-Rider-Session"))
    try:
        body = await request.json()
    except ValueError as exc:
        raise RiderError("loop_timing_invalid", 422) from exc
    if not isinstance(body, dict) or set(body) != {"revision", "values"}:
        raise RiderError("loop_timing_invalid", 422)
    result = await domain.timing.update(body["revision"], body["values"])
    return {**result, "reads": domain.read_metrics.snapshot()}


router.include_router(compute_routes.router)
router.include_router(conversation_routes.router)
router.include_router(vision_routes.router)
router.include_router(perception_routes.router)
router.include_router(pipeline_routes.router)
router.include_router(samples_routes.router)
router.include_router(analysis_routes.router)
router.include_router(automatic_routes.router)
router.include_router(motion_evidence_routes.router)
router.include_router(pulse_trial_routes.router)
router.include_router(figure_routes.router)
Session = Annotated[str | None, Header(alias="X-Rider-Session")]


def service(request: Request) -> RiderService:
    return request.app.state.rider


def safe_name(name):
    if not IDENTIFIER.fullmatch(name):
        raise RiderError("invalid_name", 400)
    return name


def image_response(result):
    content, mime, headers = result
    return Response(content, media_type=mime, headers=headers)


@router.get("/config")
async def config(request: Request):
    return service(request).config()


@router.post("/config")
async def select(payload: Selection, request: Request, session: Session = None):
    return await service(request).select(payload.device_id, session)


@router.get("/status")
async def status(
    request: Request,
    section: Annotated[str, Query()] = "overview",
):
    result = await service(request).status(section)
    result["conversation"] = conversation_routes.create_provider(request.app).config()
    if section == "compute" and result["connection"]["configured"]:
        result["compute"] = await compute_routes.operator_status(request)
    return result


@router.post("/connection/refresh")
async def refresh_connection(
    payload: ConnectionRefresh, request: Request, session: Session = None
):
    return await service(request).refresh(session)


@router.post("/control")
async def control(payload: Command, request: Request, session: Session = None):
    body = payload.model_dump(exclude_none=True, exclude={"session"})
    if payload.action == "move" and (
        payload.device_settings is not True or payload.direction is None
    ):
        raise RiderError("device_settings_required", 400)
    if payload.action in {"arm", "clear_estop"} and (
        payload.operator_consent is not True
        or not payload.operator_plan
        or not (
            payload.arming_mode == "operator"
            and payload.owner == "manual"
            or payload.action == "arm"
            and payload.arming_mode == "timed"
            and payload.owner == "navigation"
            and payload.lease_s is not None
            and 0 < payload.lease_s <= 10
        )
    ):
        raise RiderError("operator_consent_required", 400)
    return await service(request).control(body, session or payload.session)


@router.post("/settings")
async def settings(payload: SettingsPatch, request: Request, session: Session = None):
    return await service(request).settings(
        payload.model_dump(exclude_none=True), session
    )


@router.get("/camera/{stream}")
async def camera(stream: str, request: Request):
    if stream == "venom":
        from .vision_preview import processed_camera

        return await processed_camera(request)
    return image_response(await service(request).camera(stream))


@router.post("/resource/{name}")
async def resource(
    name: str,
    payload: ResourceAction,
    request: Request,
    session: Session = None,
):
    if name not in RESOURCE_NAMES:
        raise RiderError("unknown_resource", 404)
    body = payload.model_dump(exclude_none=True)
    if name == "camera" and payload.action == "stop":
        return await service(request).stop_camera(body, session)
    return await service(request).mutate("/api/resource/" + name, body, session)


@router.post("/goal/{name}")
async def goal(
    name: str, payload: GoalAction, request: Request, session: Session = None
):
    return await service(request).goal(
        safe_name(name), payload.model_dump(exclude_none=True), session
    )


@router.get("/logs/{unit}")
async def logs(
    unit: str,
    request: Request,
    lines: Annotated[int, Query(ge=1, le=200)] = 100,
):
    if not UNIT.fullmatch(unit):
        raise RiderError("invalid_name", 400)
    result = await service(request).read(
        f"/api/services/{unit}/logs?lines={lines}", 2.0
    )
    return result["data"]


@router.get("/diagnostics")
async def diagnostics(request: Request):
    result = await service(request).read("/api/services/graph", 5.0)
    return result["data"]


@router.get("/face/library")
async def face_library(request: Request):
    result = await service(request).read("/api/face-studio/gestures", 5.0)
    return result["data"]


@router.post("/face/library")
async def save_gesture(payload: dict, request: Request, session: Session = None):
    return await service(request).mutate("/api/face-studio/gestures", payload, session)


@router.delete("/face/library/{key}")
async def delete_gesture(key: str, request: Request, session: Session = None):
    return await service(request).mutate(
        "/api/face-studio/gestures/" + safe_name(key),
        None,
        session,
        method="DELETE",
    )


@router.post("/face/frame")
async def preview_face(payload: dict, request: Request, session: Session = None):
    domain = service(request)
    domain.check_session(session)
    return image_response(
        await domain.transport().image("/api/face-studio/frame", body=payload)
    )


@router.get("/face/runtime")
async def face_runtime(request: Request):
    result = await service(request).read("/api/face-studio/runtime", 5.0)
    return result["data"]


@router.post("/face/runtime")
async def face_settings(payload: dict, request: Request, session: Session = None):
    return await service(request).mutate("/api/face-studio/runtime", payload, session)


@router.post("/face/play")
async def face_play(payload: dict, request: Request, session: Session = None):
    # Explicit LCD operation, never performed by GET or page mounting.
    return await service(request).mutate(
        "/api/face-studio/runtime/play", payload, session
    )


@router.post("/face/stop")
async def face_stop(request: Request, session: Session = None):
    return await service(request).mutate("/api/face-studio/runtime/stop", {}, session)


@router.post("/face/event")
async def face_event(payload: dict, request: Request, session: Session = None):
    return await service(request).mutate(
        "/api/face-studio/runtime/event", payload, session
    )


@router.get("/face/runtime/frame")
async def face_frame(request: Request):
    return image_response(
        await service(request).transport().image("/api/face-studio/runtime/frame")
    )


def _guard_compute_device(request: Request):
    """Authenticate device compute before the original origin and mutation checks."""
    compute_routes.authenticate(request)
    if request.headers.get("Origin") or request.headers.get("Sec-Fetch-Site"):
        raise RiderError("origin_denied", 403)
    if request.method in {"GET", "DELETE"}:
        return
    try:
        ensure_module_mutation_allowed(
            module_id="rider", operation_name="compute:" + request.method.lower()
        )
    except PermissionError as exc:
        raise RiderError("environment_read_only", 403) from exc


def _guard_browser_headers(request: Request):
    """Validate mutation bounds and browser origin before decoding its body."""
    content_length = request.headers.get("Content-Length", "")
    if (
        content_length and (not content_length.isdigit() or int(content_length) > 65536)
    ) or (request.method != "DELETE" and not content_length):
        raise RiderError("request_too_large", 413)
    origin = request.headers.get("Origin")
    allowed = {
        item.strip() for item in os.getenv("RIDER_ALLOWED_ORIGINS", "").split(",")
    }
    if origin and origin not in allowed:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or (
            (parsed.scheme, parsed.netloc) != (request.url.scheme, request.url.netloc)
        ):
            raise RiderError("origin_denied", 403)
    if request.headers.get("Sec-Fetch-Site") == "cross-site" and not origin:
        raise RiderError("origin_denied", 403)


def _read_only_stop(path, payload):
    """Recognize only the existing STOP, disarm and scoped release exceptions."""
    return path.endswith("/control") and (
        payload.get("action") in {"stop", "disarm"}
        or payload.get("action") == "move"
        and payload.get("mode") == "release"
        and payload.get("intervene") is True
        and type(payload.get("operator_intervention_version")) is int
        and payload["operator_intervention_version"] == 1
    )
