"""Motor STOP does not terminate an explicitly requested raw/telemetry recording."""

import asyncio
from types import SimpleNamespace

import pytest
from test_rider import domain
from test_session_video import jpeg

from venom_module_rider.errors import RiderError
from venom_module_rider.samples_registry import SamplesRegistry
from venom_module_rider.study_frame_source import StudyFrameSource


async def test_real_control_stop_preserves_recording_until_explicit_end(tmp_path):
    service, pi = domain()
    await service.select("demo", service.session)
    registry = SamplesRegistry(tmp_path)
    service.samples = registry
    row = await registry.start(
        "record", "demo", {"scene_group": "motion-study-v1:", "record_video": True}
    )
    sid = row["session_id"]
    automatic = []
    registry.automatic.invalidate = lambda reason, **kwargs: automatic.append(reason)
    try:
        await registry.read(registry.store.video.append, sid, jpeg(1, 0))
        await service.read("/state", 0)
        await service.control({"action": "stop"}, service.session)
        assert not registry.evidence.sessions[sid]["retired"]
        assert "auto_global_stop" in automatic
        await service.read("/state", 0)
        await registry.read(registry.store.video.append, sid, jpeg(2, 0.25))
        final = await registry.end(sid)
        assert final["state"] == "closed" and final["video"]["state"] == "complete"
        assert final["video"]["frames"] == 2
    finally:
        await service.close()
        await registry.close()


@pytest.mark.parametrize(
    "path",
    [
        "/api/navigation/config",
        "/api/logic/feature/s14_venom_navigation",
        "/api/logic/feature/s15_venom_perception",
        "/api/logic/feature/s2_camera_preview",
    ],
)
async def test_non_camera_goal_or_settings_change_does_not_retire_raw(tmp_path, path):
    service, _ = domain()
    await service.select("demo", service.session)
    registry = SamplesRegistry(tmp_path)
    service.samples = registry
    row = await registry.start("record", "demo", {"scene_group": "motion-study-v1:"})
    try:
        # Fixture transport records POSTs only; no device communication occurs.
        try:
            await service.mutate(path, {"enabled": False}, service.session)
        except RiderError:
            pass
        assert not registry.evidence.sessions[row["session_id"]]["retired"]
        service.stop_sample_events("auto_device_changed")
        assert registry.evidence.sessions[row["session_id"]]["retired"]
    finally:
        await service.close()
        await registry.close()


@pytest.mark.parametrize("actual_change", [False, True])
async def test_overlapped_stop_epoch_is_transient_only_for_identical_source(
    monkeypatch, actual_change
):
    client = object()
    binding = ("device", "session", 1, "camera", 2)
    domain = SimpleNamespace(session="session", transport=lambda: client)
    source = StudyFrameSource(domain)
    source.client = client
    source.binding = binding
    source.connection = SimpleNamespace(binding=binding, closed=False)
    calls = []

    async def sleep(_):
        pass

    async def pipeline():
        calls.append(1)
        if len(calls) == 1:
            if actual_change:
                domain.session = "other"
            raise RiderError("stale_device_session")
        raise asyncio.CancelledError()

    domain.pipeline = pipeline
    monkeypatch.setattr(
        "venom_module_rider.study_frame_source.asyncio",
        SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError),
    )
    if actual_change:
        await source._refresh()
        assert source.error == "stream_epoch_changed"
    else:
        with pytest.raises(asyncio.CancelledError):
            await source._refresh()
        assert source.error is None and len(calls) == 2
