"""Stored analysis APIs never select a Pi or issue actuator/camera commands."""

from fastapi import APIRouter, Query, Request, Response

from ..analysis_contract import AnalysisCreate
from ..errors import RiderError
from .routing import RiderRoute

router = APIRouter(prefix="/analysis", route_class=RiderRoute)


def manager(request):
    value = getattr(request.app.state, "rider_analysis", None)
    if value is None:
        raise RiderError("analysis_unavailable", 503)
    return value


@router.get("/profiles")
async def profiles(request: Request):
    domain = manager(request)
    values = await domain.io(domain.catalog.profiles)
    return {"profiles": [profile.describe() for profile in values.values()]}


@router.get("/runs")
async def runs(request: Request, session_id: str | None = None):
    domain = manager(request)
    return {
        "runs": await domain.io(domain.store.list, session_id),
        "recovery_pending": not domain.recovered,
    }


@router.post("/runs", status_code=202)
async def submit(request: Request, body: AnalysisCreate):
    return await manager(request).submit(
        body.request_id, body.session_id, body.spec.model_dump(mode="json")
    )


@router.get("/requests/{request_id}")
async def recovery(request: Request, request_id: str):
    domain = manager(request)
    return await domain.io(domain.store.by_request, request_id)


@router.get("/runs/{identity}")
async def status(request: Request, identity: str, context: bool = False):
    domain = manager(request)
    return await domain.io(domain.store.get, identity, context=context)


@router.post("/runs/{identity}/cancel")
async def cancel(request: Request, identity: str):
    return await manager(request).cancel(identity)


@router.get("/runs/{identity}/artifacts")
async def artifacts(request: Request, identity: str):
    domain = manager(request)
    return {"artifacts": await domain.io(domain.store.artifacts, identity)}


@router.get("/runs/{identity}/artifacts/{name}")
async def artifact(request: Request, identity: str, name: str):
    domain = manager(request)
    payload, mime = await domain.io(domain.store.artifact, identity, name)
    return Response(payload, media_type=mime, headers={"cache-control": "no-store"})


def compare_reports(first, second):
    aligned = first["input_digest"] == second["input_digest"]
    a, b = first["summary"], second["summary"]
    # Same rows, frozen labels and transforms. Different model/params are
    # intentional; otherwise numerical quality deltas would be misleading.
    fields = ("false_go_rate", "false_stop_rate", "flicker_rate")
    return {
        "aligned_inputs_and_labels": aligned,
        "first": {"run_id": first["run_id"], "summary": a},
        "second": {"run_id": second["run_id"], "summary": b},
        "quality_delta_second_minus_first": {
            key: b[key] - a[key]
            if aligned and a.get(key) is not None and b.get(key) is not None
            else None
            for key in fields
        },
        "drive_supported": False,
        "distance_measurement": False,
    }


@router.get("/compare")
async def compare(request: Request, first: str = Query(), second: str = Query()):
    import json

    domain = manager(request)

    async def report(identity):
        status = await domain.io(domain.store.get, identity, context=False)
        if status["state"] != "succeeded":
            raise RiderError("analysis_report_incomplete", 409)
        data, _mime = await domain.io(domain.store.artifact, identity, "report.json")
        return await domain.io(json.loads, data)

    return compare_reports(await report(first), await report(second))
