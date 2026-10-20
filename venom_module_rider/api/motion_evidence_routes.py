"""Explicit offline report from a saved session, independent of Pi availability."""

from fastapi import APIRouter, Request

from ..errors import RiderError
from ..motion_evidence import analyze_manifest
from ..samples_archive import SampleArchive
from .routing import RiderRoute
from .samples_routes import registry

router = APIRouter(prefix="/samples", route_class=RiderRoute)


def session_report(store, session_id):
    """One bounded existing IO job fixes the revision and computes its report."""
    manifest, revision = SampleArchive(store).snapshot(session_id)
    manifest["dataset_revision"] = revision
    try:
        return analyze_manifest(manifest)
    except ValueError as exc:
        raise RiderError("motion_evidence_invalid", 409) from exc


@router.get("/sessions/{session_id}/motion-evidence")
async def motion_report(request: Request, session_id: str):
    domain = registry(request)
    return await domain.read(session_report, domain.store, session_id)
