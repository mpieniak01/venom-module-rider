"""Inference endpoints for configured Pi identities, not arbitrary remote URLs."""

from __future__ import annotations

import hmac
import json
import os

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import ValidationError

from ..compute import ComputeService
from ..compute_contract import LIMITS, MAX_INPUT, JobRequest, LeaseRequest
from ..errors import RiderError
from .routing import RiderRoute

router = APIRouter(route_class=RiderRoute)
DEVICE_PATHS = {"capabilities", "sessions", "jobs", "vision"}


def device_path(path: str):
    part = path.removeprefix("/api/v1/rider/compute/").split("/", 1)[0]
    return path.startswith("/api/v1/rider/compute/") and part in DEVICE_PATHS


def authenticate(request: Request):
    device_id = request.headers.get("X-Rider-Device", "")
    domain = request.app.state.rider
    device = domain.devices.get(device_id)
    token = (
        os.getenv(device.compute_token_env or "RIDER_COMPUTE_TOKEN", "")
        if device
        else ""
    )
    if (
        not device
        or not token
        or not hmac.compare_digest(
            request.headers.get("Authorization", "").encode("utf-8"),
            ("Bearer " + token).encode("utf-8"),
        )
    ):
        raise RiderError("compute_access_denied", 403)
    return device_id


def compute(request: Request) -> ComputeService:
    return request.app.state.rider_compute


async def bounded_body(request: Request, limit: int):
    length = request.headers.get("Content-Length", "")
    if length and (not length.isdigit() or int(length) > limit):
        raise RiderError("compute_input_size", 413)
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > limit:
            raise RiderError("compute_input_size", 413)
        chunks.extend(chunk)
    return bytes(chunks)


@router.get("/compute/capabilities")
async def capabilities(request: Request):
    authenticate(request)
    return await compute(request).capabilities()


@router.post("/compute/sessions")
async def open_session(request: Request):
    device_id = authenticate(request)
    try:
        payload = LeaseRequest.model_validate_json(await bounded_body(request, 4096))
    except ValidationError as exc:
        raise RiderError("compute_invalid_session", 400) from exc
    return compute(request).open_session(
        device_id, payload.device_session, payload.client_id, payload.client_instance
    )


@router.delete("/compute/sessions/{session_id}")
async def close_session(session_id: str, request: Request):
    return compute(request).close_session(authenticate(request), session_id)


@router.post("/compute/jobs", status_code=202)
async def submit(request: Request):
    device_id = authenticate(request)
    raw = request.headers.get("X-Rider-Job", "")
    if len(raw.encode("utf-8")) > LIMITS["max_header_bytes"]:
        raise RiderError("compute_invalid_job", 400)
    try:
        payload = JobRequest.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as exc:
        raise RiderError("compute_invalid_job", 400) from exc
    content = await bounded_body(request, MAX_INPUT)
    return await compute(request).submit(
        device_id, payload, content, request.headers.get("Content-Type", "")
    )


@router.get("/compute/jobs/{job_id}")
async def observe(job_id: str, request: Request):
    return await compute(request).observe(authenticate(request), job_id)


@router.delete("/compute/jobs/{job_id}")
async def cancel(job_id: str, request: Request):
    return compute(request).cancel(authenticate(request), job_id)


@router.get("/compute/jobs/{job_id}/output")
async def output(job_id: str, request: Request):
    content, mime = await compute(request).output(authenticate(request), job_id)
    return Response(content, media_type=mime, headers={"Cache-Control": "no-store"})


@router.get("/compute/status")
async def operator_status(request: Request):
    domain = request.app.state.rider
    host = await compute(request).status(domain.device.id if domain.device else "")
    try:
        pi = await domain.read("/api/compute/status", 5.0)
        config = await domain.read("/api/compute/config", 10.0)
        return {"host": host, "pi": pi["data"], "config": config["data"]}
    except RiderError as exc:
        return {"host": host, "pi": None, "config": None, "error": exc.code}


@router.post("/compute/executor")
async def select_executor(request: Request):
    domain = request.app.state.rider
    body = await request.json()
    if set(body) != {"executor_id", "revision"}:
        raise RiderError("compute_invalid_configuration", 400)
    return await domain.mutate(
        "/api/compute/config", body, request.headers.get("X-Rider-Session")
    )
