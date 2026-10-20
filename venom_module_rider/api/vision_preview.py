"""Operator-requested host processing; no camera start or actuation."""

import json
import uuid

from fastapi import Request, Response

from ..depth_detector import PerceptionSettings
from ..errors import RiderError
from ..vision_contract import VisionFrame
from .vision_routes import loop_goals


async def diagnostic_preview_allowed(domain, status, goals):
    """A retired journey may display fresh input; it never regains actuation."""
    if all(goals.get(mode) is False for mode in ("observe", "drive")):
        return True
    journey = status.get("journey") or {}
    preparation = status.get("preparation") or {}
    if not (
        goals.get("drive") is True
        and goals.get("observe") is False
        and status.get("running") is False
        and journey.get("version") == 1
        and journey.get("active") is False
        and journey.get("state") in {"stopped", "failed", "blocked"}
        and preparation.get("state") != "preparing"
    ):
        return False
    control = (await domain.read("/api/control/state", 0.2))["data"]
    safety = control.get("safety") or {}
    return (
        safety.get("state") == "DISARMED"
        and safety.get("can_move") is False
        and safety.get("moving") is not True
        and safety.get("stale") is not True
    )


async def processed_camera(request: Request):
    domain = request.app.state.rider
    client = domain.transport()
    if hasattr(domain, "pipeline"):
        pipeline = await domain.pipeline()
        if pipeline.get("supported"):
            profile = pipeline.get("active_profile") or pipeline.get("profile") or {}
            if profile.get("analysis_location") != "venom":
                raise RiderError("vision_remote_disabled", 409)
            if profile.get("remote_transport") == "mjpeg":
                from .pipeline_routes import processed_native

                hub = request.app.state.rider_stream
                async with hub.lease(domain, pipeline, keepalive=True) as connection:
                    frame = await hub.frame(connection, timeout=0.5)
                    _frame, image, headers = await processed_native(
                        request, connection, frame
                    )
                    return Response(image, media_type="image/jpeg", headers=headers)
    corridor = (0.35, 0.80, 0.65, 0.95)
    revision = 0
    source = "diagnostic_default"
    perception = {}
    try:
        policy = (await domain.read("/api/navigation/config", 0.2))["data"]
        corridor = tuple(policy["corridor"])
        revision = policy["revision"]
        perception = policy.get("perception", {})
        source = "device_policy"
    except RiderError as exc:
        if exc.status != 404:
            raise
    loop = request.app.state.rider_vision
    if source == "device_policy":
        # The producer status is lightweight. Serving a current Pi result must
        # not wait for the full service/ownership graph on every browser read.
        status = (await domain.read("/api/navigation/status", 0.2))["data"]
        run_id = status.get("run_id")
        if (
            status.get("running") is True
            and status.get("stale") is not True
            and isinstance(run_id, str)
            and run_id
        ):
            cached = await loop.wait_for_preview(
                client.device.id, corridor=corridor, revision=revision, run_id=run_id
            )
            if cached is None:
                raise RiderError("vision_busy", 429)
        else:
            # A stopped or stale producer cannot validate its previous JPEG.
            # Independent inference requires goals OFF or a confirmed retired,
            # disarmed journey. It never starts or resumes the Pi producer.
            goals = await loop_goals(domain, force=False)
            if not await diagnostic_preview_allowed(domain, status, goals):
                raise RiderError("vision_busy", 429)
            # Metadata reads may outlive the cached frame or admit a newer run.
            # Choose the latest matching result only after those awaits finish.
            cached = loop.preview(
                client.device.id, corridor=corridor, revision=revision
            )
            if cached is not None and cached[2]["age_source"] != "host-received":
                cached = None
    else:
        cached = loop.preview(client.device.id, corridor=corridor, revision=revision)
    if cached is None:

        async def produce():
            payload, mime, raw_headers = await domain.camera("raw")
            if mime not in {"image/jpeg", "image/png"}:
                raise RiderError("vision_input_format", 415)
            normalized = {key.lower(): value for key, value in raw_headers.items()}
            if (
                normalized.get("x-rider-stale") == "true"
                or normalized.get("x-rider-placeholder") == "true"
            ):
                raise RiderError("frame_stale", 409)
            # Legacy snapshots expose a Pi wall-clock timestamp, not a relative age.
            # Do not compare clocks on different hosts or grant motion from this path.
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
                perception=PerceptionSettings.model_validate(perception),
            )
            result = await loop.decide(
                client.device.id,
                frame,
                payload,
                with_preview=True,
                input_ticket=normalized.get("x-rider-capture-key"),
            )

            return result

        # Separate browsers share RAW retrieval and one model call. Session,
        # policy and handoff epoch prevent joining another device/configuration.
        key = (
            client.device.id,
            getattr(domain, "session", None),
            getattr(domain, "epoch", None),
            tuple(corridor),
            revision,
            json.dumps(perception, sort_keys=True),
        )
        result = await loop.share_snapshot(key, produce)
        cached = loop.preview(client.device.id, corridor=corridor, revision=revision)
        if cached is None:
            raise RiderError(result["reason"], 503)
    captured_at, image, result, elapsed = cached
    age_ms = max(0.0, (loop.clock() - captured_at) * 1000)
    return Response(
        image,
        media_type="image/jpeg",
        headers={
            **(
                {"x-rider-capture-key": result["capture_key"]}
                if result.get("capture_key")
                else {}
            ),
            "cache-control": "no-store",
            "x-rider-source": "venom",
            "x-rider-stale": "false",
            "x-rider-placeholder": "false",
            "x-rider-run-id": result["run_id"],
            "x-rider-sequence": str(result["sequence"]),
            "x-rider-frame-id": result["frame_id"],
            "x-rider-config-revision": str(result["config_revision"]),
            "x-rider-frame-age-ms": str(round(age_ms, 2)),
            "x-rider-frame-age-source": result["age_source"],
            "x-rider-file-observed-at": str(result["observed_at"]),
            "x-rider-vision-state": result["state"],
            "x-rider-vision-reason": result["reason"],
            "x-rider-vision-inference-ms": str(elapsed),
            "x-rider-vision-policy": source,
            "x-rider-vision-action": "stop",
            "x-rider-analysis-json": json.dumps(
                {
                    key: value
                    for key, value in result.items()
                    if key not in {"_preview", "corridor"}
                },
                separators=(",", ":"),
                allow_nan=False,
            ),
        },
    )
