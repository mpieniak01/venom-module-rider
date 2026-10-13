"""Operator-requested host processing; no camera start or actuation."""

import uuid

from fastapi import Request, Response

from ..errors import RiderError
from ..vision_contract import VisionFrame


async def processed_camera(request: Request):
    domain = request.app.state.rider
    client = domain.transport()
    payload, mime, headers = await domain.camera("raw")
    if mime not in {"image/jpeg", "image/png"}:
        raise RiderError("vision_input_format", 415)
    normalized = {key.lower(): value for key, value in headers.items()}
    if (
        normalized.get("x-rider-stale") == "true"
        or normalized.get("x-rider-placeholder") == "true"
    ):
        raise RiderError("frame_stale", 409)
    headers = normalized
    corridor = (0.35, 0.65, 0.65, 0.95)
    revision = 0
    source = "diagnostic_default"
    try:
        policy = (await domain.read("/api/navigation/config", 0.2))["data"]
        corridor = tuple(policy["corridor"])
        revision = policy["revision"]
        source = "device_policy"
    except RiderError as exc:
        if exc.status != 404:
            raise
    frame = VisionFrame(
        protocol_version=1,
        run_id=uuid.uuid4().hex,
        frame_id=uuid.uuid4().hex,
        sequence=1,
        config_revision=revision,
        age_ms=0.0,
        deadline_ms=500,
        mode="observe",
        corridor=corridor,
    )
    loop = request.app.state.rider_vision
    cached = loop.preview(client.device.id)
    if (
        cached is not None
        and cached[2]["corridor"] == corridor
        and cached[2]["config_revision"] == revision
    ):
        _, image, result, elapsed = cached
        result = {**result, "_preview": image, "inference_ms": elapsed}
        headers = {
            **headers,
            "x-rider-frame-id": result["frame_id"],
            "x-rider-file-observed-at": str(result["observed_at"]),
        }
    else:
        result = await loop.decide(client.device.id, frame, payload, with_preview=True)
    image = result.pop("_preview", None)
    if image is None:
        raise RiderError(result["reason"], 503)
    return Response(
        image,
        media_type="image/jpeg",
        headers={
            **headers,
            "cache-control": "no-store",
            "x-rider-source": "venom",
            "x-rider-vision-state": result["state"],
            "x-rider-vision-reason": result["reason"],
            "x-rider-vision-inference-ms": str(result["inference_ms"]),
            "x-rider-vision-policy": source,
            "x-rider-vision-action": "stop",
        },
    )
