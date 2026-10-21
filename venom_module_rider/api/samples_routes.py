"""Persistent samples are independent of Pi connectivity and actuator commands."""

from fastapi import APIRouter, Query, Request, Response

from ..errors import RiderError
from ..samples_contract import (
    CaptureRequest,
    CollectionCreate,
    CollectionEnd,
    LabelRequest,
)
from .routing import RiderRoute

router = APIRouter(prefix="/samples", route_class=RiderRoute)


def registry(request):
    result = request.app.state.rider_samples
    if result is None:
        raise RiderError("samples_storage_unavailable", 503)
    return result


@router.get("/status")
async def status(request: Request):
    if request.app.state.rider_samples is None:
        return {"available": False, "error": "samples_storage_unavailable"}
    return {"available": True, **await registry(request).status()}


@router.get("/sessions")
async def sessions(request: Request):
    domain = registry(request)
    return {"sessions": await domain.read(domain.store.sessions)}


@router.post("/sessions", status_code=201)
async def start(request: Request, body: CollectionCreate):
    if body.source_device_id not in request.app.state.rider.devices:
        raise RiderError("samples_source_unknown", 400)
    result = await registry(request).start(
        body.request_id, body.source_device_id, body.policy.model_dump()
    )
    request.app.state.rider.study_sampler.ensure()
    return result


@router.get("/sessions/{session_id}")
async def session(
    request: Request,
    session_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=50),
):
    domain = registry(request)
    return await domain.read(domain.store.page, session_id, offset=offset, limit=limit)


@router.post("/sessions/{session_id}/end")
async def end(request: Request, session_id: str, body: CollectionEnd | None = None):
    return await registry(request).end(
        session_id, incomplete=body.interrupted if body is not None else False
    )


@router.post("/capture", status_code=202)
async def capture(request: Request, body: CaptureRequest):
    return registry(request).capture(
        body.session_id,
        body.capture_request_id,
        body.frame_key,
        allow_historical=body.allow_historical,
    )


@router.get("/requests/{request_id}")
async def capture_status(request: Request, request_id: str):
    return await registry(request).request(request_id)


@router.get("/items/{sample_id}")
async def sample(request: Request, sample_id: str):
    domain = registry(request)
    return await domain.read(domain.store.sample, sample_id)


@router.get("/items/{sample_id}/original")
async def original(request: Request, sample_id: str):
    domain = registry(request)
    payload, mime = await domain.read(domain.store.original, sample_id)
    return Response(payload, media_type=mime, headers={"cache-control": "no-store"})


@router.post("/items/{sample_id}/labels")
async def label(request: Request, sample_id: str, body: LabelRequest):
    domain = registry(request)
    return await domain.read(
        domain.store.label,
        sample_id,
        body.values.model_dump(),
        expected_revision=body.expected_revision,
    )


@router.get("/sessions/{session_id}/export")
async def export(request: Request, session_id: str):
    from starlette.background import BackgroundTask
    from starlette.responses import FileResponse

    from ..samples_archive import SampleArchive

    domain = registry(request)
    archive = SampleArchive(domain.store)
    path, revision = await domain.read(archive.export, session_id)
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"rider-session-{session_id}.zip",
        headers={"X-Rider-Dataset-Revision": revision},
        background=BackgroundTask(path.unlink, missing_ok=True),
    )
