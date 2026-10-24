"""Native profile proxy and media consumers; Pi remains the only live authority."""

from __future__ import annotations

import hashlib
import logging
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from ..errors import RiderError
from ..preview_metadata import analysis_header
from ..sample_sources import native_ticket
from ..stream_contract import PipelineAction, SourceReference, source_headers
from ..stream_contract import PipelineProfile as PipelineProfile
from .routing import RiderRoute

router = APIRouter(route_class=RiderRoute)
logger = logging.getLogger(__name__)

# Rejected inputs do not end a healthy media subscription. They also do not
# produce a part: browser freshness keeps aging until a new valid source arrives.
TRANSIENT_PREVIEW_ERRORS = {
    "frame_stale",
    "vision_busy",
    "vision_frame_expired",
    "vision_decision_expired",
    "vision_inference_unavailable",
    "vision_inference_timeout",
    "stream_frame_unavailable",
    "stream_disconnected",
}


@router.get("/camera/pipeline")
async def pipeline_status(request: Request):
    status = await request.app.state.rider.pipeline(force=True)
    return {key: value for key, value in status.items() if not key.startswith("_")}


@router.post("/camera/pipeline")
async def pipeline_action(request: Request):
    try:
        body = PipelineAction.model_validate(await request.json())
    except (ValueError, ValidationError) as exc:
        raise RiderError("camera_pipeline_invalid_request", 400) from exc
    return await request.app.state.rider.configure_pipeline(
        body.model_dump(exclude_none=True), request.headers.get("X-Rider-Session")
    )


async def native_pipeline(domain, *, device=None, source=None, preparing=False):
    if device is not None and domain.transport().device.id != device:
        raise RiderError("stream_device_not_selected", 409)
    # Preparation runs before the first RAM capture and must not inherit a
    # cached status from the preceding camera session. It is not a per-frame poll.
    pipeline = await domain.pipeline(force=preparing)
    if source is not None and (
        pipeline.get("revision") != source.pipeline_revision
        or pipeline.get("transport_epoch") != source.transport_epoch
        or pipeline.get("camera_session") != source.camera_session
    ):
        pipeline = await domain.pipeline(force=True)
    if not pipeline.get("supported"):
        raise RiderError("camera_pipeline_unsupported", 503)
    active = (
        pipeline.get("active_profile")
        or (pipeline.get("profile") if preparing else {})
        or {}
    )
    if active.get("remote_transport") != "mjpeg":
        raise RiderError("stream_not_selected", 409)
    return pipeline


async def snapshot_decision_allowed(domain, device):
    """A late legacy sender cannot bypass the selected Pi's live transport."""
    selected = getattr(domain, "device", None)
    if selected is None or selected.id != device or not hasattr(domain, "pipeline"):
        return
    pipeline = await domain.pipeline()
    if pipeline.get("supported"):
        profile = pipeline.get("active_profile") or pipeline.get("profile") or {}
        if profile.get("analysis_location") == "local":
            raise RiderError("vision_remote_disabled", 409)
        if profile.get("remote_transport") == "mjpeg":
            raise RiderError("vision_transport_mismatch", 409)


def multipart(payload, headers):
    lines = [
        "--rider",
        "Content-Type: image/jpeg",
        f"Content-Length: {len(payload)}",
        *(f"{key}: {value}" for key, value in headers.items()),
        "",
        "",
    ]
    return "\r\n".join(lines).encode("ascii") + payload + b"\r\n"


async def processed_native(request, connection, incoming):
    """A live Pi result wins; diagnostics never create another motion run."""
    from ..depth_detector import PerceptionSettings
    from ..vision_contract import VisionFrame
    from .vision_preview import diagnostic_preview_allowed
    from .vision_routes import loop_goals

    domain, loop, hub = (
        request.app.state.rider,
        request.app.state.rider_vision,
        request.app.state.rider_stream,
    )
    policy = (await domain.read("/api/navigation/config", 0.2))["data"]
    models = getattr(request.app.state, "rider_models", None)
    if models is not None:
        models.observe_policy(domain.transport().device.id, policy)
    corridor, revision = tuple(policy["corridor"]), policy["revision"]
    status = (await domain.read("/api/navigation/status", 0.2))["data"]
    run_id = status.get("run_id")
    if status.get("running") is True and status.get("stale") is not True and run_id:
        row = await loop.wait_for_preview(
            domain.transport().device.id,
            corridor=corridor,
            revision=revision,
            run_id=run_id,
            timeout=0.3,
        )
        if row is None or not isinstance(row[2].get("source"), dict):
            raise RiderError("vision_busy", 429)
        source = SourceReference.model_validate(row[2]["source"])
        incoming = await hub.frame(connection, source=source, timeout=0.05)
        image, result, elapsed = row[1], row[2], row[3]
    else:
        goals = await loop_goals(domain, force=False)
        if not await diagnostic_preview_allowed(domain, status, goals):
            raise RiderError("vision_busy", 429)
        # Status/configuration I/O may outlive the candidate selected by the
        # receiver. In diagnostic mode choose the latest source AFTER those
        # awaits; a live Pi result above still pins its exact original source.
        incoming = await hub.frame(connection, timeout=0.5)
        payload = await hub.canonical_frame(connection, incoming)
        if connection.closed:
            raise RiderError("stream_epoch_changed", 409)
        ticket = native_ticket(domain, payload, source_headers(incoming, hub.clock()))
        age = incoming.age_ms(hub.clock())
        if age >= 300:
            raise RiderError("frame_stale", 409)
        frame = VisionFrame(
            protocol_version=1,
            # Configuration edits do not restart the camera. A diagnostic run
            # must still change, or the previous policy's signature rejects every
            # subsequent frame as replay/configuration_changed indefinitely.
            run_id="native-preview-"
            + hashlib.sha256(
                f"{incoming.source.camera_session}:{revision}".encode()
            ).hexdigest()[:40],
            frame_id=incoming.source.frame_id,
            sequence=incoming.source.sequence,
            config_revision=revision,
            age_ms=age,
            deadline_ms=300,
            mode="observe",
            corridor=corridor,
            perception=PerceptionSettings.model_validate(policy.get("perception", {})),
            behavior=policy.get("behavior", {}),
        )

        async def infer():
            cached = loop.preview(
                domain.transport().device.id,
                corridor=corridor,
                revision=revision,
            )
            if cached and cached[2].get("source") == (
                incoming.source.reference().model_dump(mode="json")
            ):
                return {**cached[2], "_preview": cached[1], "inference_ms": cached[3]}
            return await loop.decide(
                domain.transport().device.id,
                frame,
                payload,
                with_preview=True,
                input_ticket=ticket,
                source_key=(
                    incoming.source.identity(),
                    incoming.source.image_transform.model_dump_json(),
                ),
                source_reference=incoming.source.reference().model_dump(mode="json"),
            )

        result = dict(
            await loop.share_snapshot(
                (
                    "native-preview",
                    domain.session,
                    domain.epoch,
                    incoming.source.identity(),
                    tuple(corridor),
                    revision,
                    frame.perception.model_dump_json(),
                ),
                infer,
            )
        )
        image = result.pop("_preview", None)
        result.update(result.pop("_preview_analysis", {}))
        if image is None:
            raise RiderError(result["reason"], 503)
        elapsed = result["inference_ms"]
        result["preview_only"] = True
    if connection.closed:
        raise RiderError("stream_epoch_changed", 409)
    if incoming.age_ms(hub.clock()) >= 500:
        raise RiderError("frame_stale", 409)
    headers = {
        **source_headers(incoming, hub.clock(), source="venom"),
        "x-rider-run-id": result["run_id"],
        "x-rider-config-revision": str(result["config_revision"]),
        "x-rider-vision-state": result["state"],
        "x-rider-vision-reason": result["reason"],
        "x-rider-vision-inference-ms": str(elapsed),
        "x-rider-vision-action": "stop",
        **(
            {"x-rider-capture-key": result["capture_key"]}
            if result.get("capture_key")
            else {}
        ),
        "x-rider-analysis-json": analysis_header(result),
    }
    return incoming, image, headers


@router.get("/camera/stream/{kind}")
async def browser_stream(kind: Literal["raw", "venom"], request: Request):
    domain, hub = request.app.state.rider, request.app.state.rider_stream
    pipeline = await native_pipeline(domain)
    if (
        kind == "venom"
        and (pipeline.get("active_profile") or {}).get("analysis_location") != "venom"
    ):
        raise RiderError("vision_remote_disabled", 409)
    binding = hub.binding(domain, pipeline)

    async def generate():
        async with hub.lease(domain, pipeline) as connection:
            previous = None
            last_rejection_log = float("-inf")
            while not await request.is_disconnected():
                if hub.binding(domain, pipeline) != binding:
                    return
                frame = None
                try:
                    frame = await hub.frame(connection, after=previous, timeout=0.5)
                    if kind == "venom":
                        frame, payload, headers = await processed_native(
                            request, connection, frame
                        )
                    else:
                        payload = await hub.canonical_frame(connection, frame)
                        if frame.age_ms(hub.clock()) >= 500:
                            return
                        headers = source_headers(frame, hub.clock())
                        if (
                            connection.closed
                            or hub.binding(domain, pipeline) != binding
                        ):
                            return
                        ticket = native_ticket(domain, payload, headers)
                        if ticket:
                            headers["x-rider-capture-key"] = ticket
                    if frame.source.identity() == previous:
                        # A slower model must not retransmit a cached result as
                        # a fresh source part or spin at full CPU.
                        await asyncio.sleep(0.02)
                        continue
                    previous = frame.source.identity()
                    yield multipart(payload, headers)
                except RiderError as exc:
                    if not connection.closed and exc.code in TRANSIENT_PREVIEW_ERRORS:
                        # Skip work for this source; do not replay the preceding
                        # JPEG or reset its age. The ingest continues latest-only.
                        if frame is not None:
                            previous = frame.source.identity()
                        if hub.clock() - last_rejection_log >= 2:
                            logger.warning(
                                "Native %s preview skipped source: %s received=%s "
                                "rejected=%s reconnects=%s",
                                kind,
                                exc.code,
                                connection.received,
                                connection.rejected,
                                connection.reconnects,
                            )
                            last_rejection_log = hub.clock()
                        await asyncio.sleep(0.02)
                        continue
                    logger.warning(
                        "Native %s preview ended: %s received=%s rejected=%s "
                        "reconnects=%s closed=%s",
                        kind,
                        exc.code,
                        connection.received,
                        connection.rejected,
                        connection.reconnects,
                        connection.closed,
                    )
                    return

    import asyncio

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=rider",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
