"""Optional Venom router. Every operator path uses the same Pi domain service."""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute
from venom_core.core.module_data_policy import ensure_module_mutation_allowed

from ..config import IDENTIFIER, configured_devices
from ..errors import RiderError
from ..service import RESOURCE_NAMES, RiderService
from .schemas import Command, GoalAction, ResourceAction, Selection, SettingsPatch


class RiderRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except RiderError as exc:
                raise HTTPException(
                    exc.status, detail={"code": exc.code, "details": exc.details}
                ) from exc

        return handler


@asynccontextmanager
async def lifespan(app):
    existing = getattr(app.state, "rider", None)
    service = existing or RiderService(configured_devices())
    app.state.rider = service
    default = os.getenv("RIDER_DEFAULT_DEVICE")
    if default and existing is None:
        await service.select(default, service.session)
    try:
        yield
    finally:
        await service.close()


async def guard(request: Request):
    token = os.getenv("RIDER_ACCESS_TOKEN")
    if token and not hmac.compare_digest(
        request.headers.get("Authorization", ""), "Bearer " + token
    ):
        raise RiderError("access_denied", 403)
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
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
    try:
        payload = await request.json() if request.method != "DELETE" else {}
    except ValueError as exc:
        raise RiderError("invalid_request", 400) from exc
    if not isinstance(payload, dict):
        raise RiderError("invalid_request", 400)
    # A safety STOP must work in read-only environments as well.
    if request.url.path.endswith("/control") and payload.get("action") in {
        "stop",
        "disarm",
    }:
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
    return await service(request).status(section)


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
        or payload.arming_mode != "operator"
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
    return await service(request).mutate(
        "/api/resource/" + name,
        payload.model_dump(exclude_none=True),
        session,
    )


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
    if not unit.endswith(".service") or not IDENTIFIER.fullmatch(unit[:-8]):
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
