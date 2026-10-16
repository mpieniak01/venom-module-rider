"""Conservative source age from one Pi status sample and host-local HTTP timing."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .stream_contract import SourceDescription


@dataclass(frozen=True)
class SourceAgeAnchor:
    camera_session: str
    transport_epoch: int
    pipeline_revision: int
    captured_monotonic_ns: int
    frame_age_ms: float
    request_started_mono: float
    response_received_mono: float

    def authority(self):
        return self.transport_epoch, self.camera_session, self.pipeline_revision

    def upper_age_ms(self, source, host_now):
        if (
            source.camera_session != self.camera_session
            or source.transport_epoch != self.transport_epoch
            or source.pipeline_revision != self.pipeline_revision
        ):
            return None
        # Only subtract timestamps from the SAME clock on each side. Treat the
        # Pi sample as if it happened at host GET start: the entire HTTP RTT is
        # included, conservatively covering both directions and server work.
        return (
            self.frame_age_ms
            + (self.captured_monotonic_ns - source.captured_monotonic_ns) / 1e6
            + (host_now - self.request_started_mono) * 1000
        )


def age_anchor(status, request_started, response_received):
    if (
        not isinstance(request_started, (int, float))
        or isinstance(request_started, bool)
        or not isinstance(response_received, (int, float))
        or isinstance(response_received, bool)
        or not math.isfinite(request_started)
        or not math.isfinite(response_received)
        or response_received < request_started
    ):
        return None
    try:
        latest = SourceDescription.model_validate(status.get("latest_frame"))
        if (
            latest.camera_session != status.get("camera_session")
            or latest.transport_epoch != status.get("transport_epoch")
            or latest.pipeline_revision != status.get("revision")
        ):
            return None
    except ValueError:
        return None
    return SourceAgeAnchor(
        latest.camera_session,
        latest.transport_epoch,
        latest.pipeline_revision,
        latest.captured_monotonic_ns,
        latest.frame_age_ms,
        float(request_started),
        float(response_received),
    )
