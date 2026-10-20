"""Recording follows delivered responses, never causes a new Pi request."""

from types import SimpleNamespace

import pytest
from test_rider import domain

from venom_module_rider.errors import RiderError


class Recorder:
    def __init__(self, *, fail=False):
        self.rows, self.retired = [], []
        self.failures, self.fail = 0, fail
        self.automatic = SimpleNamespace(invalidate=lambda reason: None)

    def observe_evidence(self, path, payload, **context):
        if self.fail:
            raise RuntimeError("disk/recorder failure")
        self.rows.append((path, payload, context))

    def retire_evidence(self, reason):
        self.retired.append(reason)


async def test_existing_response_only_and_cache_hit_adds_zero_requests():
    service, pi = domain()
    await service.select("demo", service.session)
    recorder = Recorder()
    service.samples = recorder
    try:
        before = len(pi.trace)
        first = await service.read("/state", 10)
        assert await service.read("/state", 10) == first
        assert len(pi.trace) == before + 1
        assert len(recorder.rows) == 1
        path, payload, context = recorder.rows[0]
        assert path == "/state" and payload == first
        assert context["source_session"] == service.session
        assert context["device_id"] == "demo"
        assert context["received_mono"] > 0 and context["received_at"] > 0
        await service.read("/healthz", 10)
        assert len(recorder.rows) == 1
    finally:
        await service.close()
    assert "module_closed" in recorder.retired


async def test_faulty_recorder_and_transport_error_do_not_change_delivery():
    service, pi = domain()
    await service.select("demo", service.session)
    recorder = Recorder(fail=True)
    service.samples = recorder
    try:
        assert "data" in await service.read("/state", 0)
        assert recorder.failures == 1
        pi.offline = True
        with pytest.raises(RiderError):
            await service.read("/state", 0, force=True)
        assert recorder.failures == 1
        service.stop_sample_events("auto_global_stop")
        assert recorder.retired == ["auto_global_stop"]
    finally:
        await service.close()


async def test_source_change_retires_before_selecting_new_device():
    service, _ = domain()
    await service.select("demo", service.session)
    recorder = Recorder()
    service.samples = recorder
    try:
        await service.select(None, service.session)
        assert recorder.retired == ["auto_device_changed"]
    finally:
        await service.close()
