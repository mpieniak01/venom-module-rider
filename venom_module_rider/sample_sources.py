"""Source provenance adapter. Reading or saving never starts another camera."""

import math


def numeric(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def offer(registry, device_id, payload, mime, context):
    if registry is None:
        return None
    try:
        return registry.offer(device_id, payload, mime, context)
    except Exception:
        # A data-collection failure cannot change a live movement decision.
        registry.failures += 1
        return None


def raw(registry, device_id, source_session, payload, mime, headers, *, mode=None):
    values = {key.lower(): value for key, value in headers.items()}
    if values.get("x-rider-placeholder") == "true":
        return None
    observed = values.get("x-rider-file-observed-at")
    recorded = values.get("x-rider-recorded-at")
    replay = recorded not in {None, "", "0", "0.0"}
    context = {
        "source_session": values.get("x-rider-run-id"),
        "host_device_session": source_session,
        "device_mode": mode,
        "frame_id": values.get("x-rider-frame-id"),
        "run_id": values.get("x-rider-run-id"),
        "sequence": numeric(values.get("x-rider-sequence")),
        "source_timestamp": recorded if replay else observed,
        "recorded_at_header": recorded if replay else None,
        "timestamp_source": "recorded-at-header"
        if replay
        else "pi-file-observed-at"
        if observed
        else None,
        "clock_domain": "device-wall-unsynchronised" if observed else None,
        "source_age_ms": numeric(values.get("x-rider-frame-age-ms")),
        "age_source": values.get("x-rider-frame-age-source"),
        "clock_sync_uncertainty_ms": None,
        "stale": values.get("x-rider-stale") == "true",
        "transform": None,
        "telemetry": None,
        "commands": None,
        "association": "unmeasured",
        "input_kind": "raw",
    }
    descriptor = values.get("x-rider-source-json")
    if descriptor and len(descriptor.encode()) <= 8192:
        try:
            from .stream_contract import SourceDescription

            native = SourceDescription.model_validate_json(descriptor)
            context.update(
                source_session=native.camera_session,
                source_timestamp=str(native.captured_monotonic_ns),
                timestamp_source="pi-capture-monotonic-ns",
                clock_domain="pi-monotonic-unsynchronised",
                transform={
                    **native.image_transform.model_dump(mode="json"),
                    "applied_on": "venom",
                },
                source_identity=native.reference().model_dump(mode="json"),
                source_sha256=native.sha256,
                association="exact-source-reference",
            )
            # Two browsers or a subsequent model reference must not create a
            # new receipt age or evict the same displayed frame's ticket.
            if registry is not None:
                key = registry.frames.lookup(device_id, context)
                if key and registry.frames.get(key).payload == payload:
                    return key
        except Exception:
            # Optional provenance and collection must not break live viewing.
            pass
    return offer(registry, device_id, payload, mime, context)


def native_ticket(domain, payload, headers):
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


def vision(
    registry,
    device_id,
    source_session,
    frame,
    payload,
    *,
    elapsed=0.0,
    input_ticket=None,
    diagnostic=False,
):
    if input_ticket and registry is not None:
        try:
            previous = registry.frames.get(input_ticket)
            if previous.device_id == device_id and previous.payload == payload:
                return input_ticket
        except Exception:
            registry.failures += 1
    mime = "image/png" if payload.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
    return offer(
        registry,
        device_id,
        payload,
        mime,
        {
            "source_session": None if diagnostic else frame.run_id,
            "host_device_session": source_session,
            "device_mode": getattr(registry, "device_modes", {}).get(device_id),
            "frame_id": None if diagnostic else frame.frame_id,
            "run_id": frame.run_id,
            "sequence": frame.sequence,
            "source_timestamp": None,
            "timestamp_source": None,
            "clock_domain": None,
            "source_age_ms": frame.age_ms + elapsed,
            "age_source": "host-processing-only"
            if diagnostic
            else "pi-reported-relative-plus-host-processing",
            "received_event": "host-registry-offer",
            "clock_sync_uncertainty_ms": None,
            "stale": False,
            "calibration": {
                "calibration_id": frame.calibration_id,
                "config_revision": frame.config_revision,
            },
            "transform": None,
            "telemetry": None,
            "commands": None,
            "association": "unmeasured",
            "input_kind": "vision-raw",
            "frame_descriptor": frame.model_dump(mode="json"),
        },
    )
