"""Optional data callbacks isolate recorder failures from live control delivery."""

from __future__ import annotations


def observe_received(domain, path, payload, *, session, received_mono, received_at):
    """Tap an existing completed response; this function never reads transport."""
    registry = domain.samples
    if (
        registry is None
        or path not in {"/state", "/api/control/state"}
        or domain.device is None
        or session != domain.session
    ):
        return
    try:
        registry.observe_evidence(
            path,
            payload,
            device_id=domain.device.id,
            source_session=session,
            received_mono=received_mono,
            received_at=received_at,
        )
    except Exception:
        registry.failures += 1


def retire_received(domain, reason):
    """Retirement does no IO and must not interfere with STOP or shutdown."""
    registry = domain.samples
    if registry is None:
        return
    try:
        retire = getattr(registry, "retire_evidence", None)
        if callable(retire):
            retire(reason)
    except Exception:
        registry.failures += 1
