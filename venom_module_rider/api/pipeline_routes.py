"""Native profile proxy and media consumers; Pi remains the only live authority."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import Field, StrictInt, ValidationError, field_validator, model_validator

from ..errors import RiderError
from ..stream_contract import SourceReference, StreamModel
from .routing import RiderRoute

router = APIRouter(route_class=RiderRoute)


class PipelineProfile(StreamModel):
    analysis_location: Literal["local", "venom"]
    local_engine: Literal["canny", "ssd", "tflite"]
    remote_transport: Literal["snapshots", "mjpeg"]


class PipelineAction(StreamModel):
    contract_version: Literal[1]
    request_id: Annotated[
        str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    ]
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    action: Literal["configure", "start", "stop"]
    profile: PipelineProfile | None = None

    @field_validator("contract_version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int:
            raise ValueError("integer_version_required")
        return value

    @model_validator(mode="after")
    def configuration_profile(self):
        if (self.action == "configure") != (self.profile is not None) or (
            self.action != "configure" and "profile" in self.model_fields_set
        ):
            raise ValueError("profile_required_only_for_configuration")
        return self


@router.get("/camera/pipeline")
async def pipeline_status(request: Request):
    return await request.app.state.rider.pipeline(force=True)


@router.post("/camera/pipeline")
async def pipeline_action(request: Request):
    try:
        body = PipelineAction.model_validate(await request.json())
    except (ValueError, ValidationError) as exc:
        raise RiderError("camera_pipeline_invalid_request", 400) from exc
    return await request.app.state.rider.configure_pipeline(
        body.model_dump(exclude_none=True), request.headers.get("X-Rider-Session")
    )


async def native_pipeline(domain, *, device=None, source=None):
    if device is not None and domain.transport().device.id != device:
        raise RiderError("stream_device_not_selected", 409)
    pipeline = await domain.pipeline()
    if source is not None and (
        pipeline.get("revision") != source.pipeline_revision
        or pipeline.get("transport_epoch") != source.transport_epoch
        or pipeline.get("camera_session") != source.camera_session
    ):
        pipeline = await domain.pipeline(force=True)
    if not pipeline.get("supported"):
        raise RiderError("camera_pipeline_unsupported", 503)
    active = pipeline.get("active_profile") or {}
    if active.get("remote_transport") != "mjpeg":
        raise RiderError("stream_not_selected", 409)
    return pipeline


def source_headers(frame, now, *, source="pi-camera"):
    metadata = frame.source.model_dump(mode="json")
    return {
        "cache-control": "no-store",
        "x-rider-source": source,
        "x-rider-source-json": json.dumps(metadata, separators=(",", ":")),
        "x-rider-stale": "false",
        "x-rider-placeholder": "false",
        "x-rider-camera-session": frame.source.camera_session,
        "x-rider-transport-epoch": str(frame.source.transport_epoch),
        "x-rider-pipeline-revision": str(frame.source.pipeline_revision),
        "x-rider-frame-id": frame.source.frame_id,
        "x-rider-sequence": str(frame.source.sequence),
        "x-rider-frame-age-ms": str(round(frame.age_ms(now), 2)),
        "x-rider-frame-age-source": "pi-send-plus-host-elapsed",
    }


def native_ticket(domain, frame, payload, headers):
    from ..sample_sources import raw

    client = domain.transport()
    return raw(
        getattr(domain, "samples", None),
        client.device.id,
        domain.session,
        payload,
        "image/jpeg",
        headers,
        mode=client.device.mode,
    )


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
    from .vision_routes import loop_goals

    domain, loop, hub = (
        request.app.state.rider,
        request.app.state.rider_vision,
        request.app.state.rider_stream,
    )
    policy = (await domain.read("/api/navigation/config", 0.2))["data"]
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
        if any(goals.get(mode) is not False for mode in ("observe", "drive")):
            raise RiderError("vision_busy", 429)
        payload = await hub.canonical_frame(connection, incoming)
        ticket = native_ticket(
            domain, incoming, payload, source_headers(incoming, hub.clock())
        )
        age = incoming.age_ms(hub.clock())
        if age >= 300:
            raise RiderError("frame_stale", 409)
        frame = VisionFrame(
            protocol_version=1,
            run_id="native-preview-" + incoming.source.camera_session[:40],
            frame_id=incoming.source.frame_id,
            sequence=incoming.source.sequence,
            config_revision=revision,
            age_ms=age,
            deadline_ms=300,
            mode="observe",
            corridor=corridor,
            perception=PerceptionSettings.model_validate(policy.get("perception", {})),
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
        if image is None:
            raise RiderError(result["reason"], 503)
        elapsed = result["inference_ms"]
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
        "x-rider-analysis-json": json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"_preview", "corridor"}
            },
            separators=(",", ":"),
            allow_nan=False,
        ),
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
            while not await request.is_disconnected():
                if hub.binding(domain, pipeline) != binding:
                    return
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
                        ticket = native_ticket(domain, frame, payload, headers)
                        if ticket:
                            headers["x-rider-capture-key"] = ticket
                    if frame.source.identity() == previous:
                        # A slower model must not retransmit a cached result as
                        # a fresh source part or spin at full CPU.
                        await asyncio.sleep(0.02)
                        continue
                    previous = frame.source.identity()
                    yield multipart(payload, headers)
                except RiderError:
                    # End this receiver visibly. A reconnect is a new request,
                    # never a heartbeat masquerading as a camera frame.
                    return

    import asyncio

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=rider",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
