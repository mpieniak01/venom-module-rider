"""Global release drains host vision and never starts camera or movement."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from venom_module_rider.errors import RiderError
from venom_module_rider.model_resources import RiderModelResources
from venom_module_rider.vision_loop import VisionLoop


@pytest.mark.asyncio
async def test_release_unloads_only_after_confirmed_prepare_and_clears_both_detectors():
    loop = SimpleNamespace(
        busy=None,
        depth_busy=None,
        depth_detector=SimpleNamespace(
            model=object(), processor=object(), device="cuda"
        ),
        detector=SimpleNamespace(session=object()),
        previews={"old": 1},
        inference_cache={"old": 1},
        runs={"old": 1},
    )
    domain = SimpleNamespace(device=None)
    runtime = SimpleNamespace(reset=Mock())
    app = SimpleNamespace(
        state=SimpleNamespace(
            rider=domain,
            rider_vision=loop,
            rider_models=SimpleNamespace(runtime=runtime),
            rider_analysis=None,
        )
    )
    resource = RiderModelResources(app)
    assert resource.release_model_resource() is False
    await resource.prepare_model_release()
    assert resource.release_model_resource() is True
    assert loop.resources_suspended
    assert loop.depth_detector.model is None
    assert loop.depth_detector.processor is None
    assert loop.detector.session is None
    assert loop.previews == loop.inference_cache == loop.runs == {}
    runtime.reset.assert_called_once()


@pytest.mark.asyncio
async def test_preparation_disarms_before_stopping_camera_and_never_arms():
    calls = []

    async def read(*_args, **_kwargs):
        return {
            "data": {
                "safety": {
                    "state": "DISARMED" if calls else "ARMED",
                    "can_move": not calls,
                }
            }
        }

    async def control(body, session):
        calls.append(body["action"])
        assert session == "session"

    domain = SimpleNamespace(
        device=object(),
        session="session",
        read=read,
        control=control,
        pipeline=AsyncMock(return_value={"effective_stages": {"capture": True}}),
        goal=AsyncMock(),
        stop_camera=AsyncMock(),
    )
    loop = SimpleNamespace(
        busy=None, depth_busy=None, depth_detector=SimpleNamespace(model=object())
    )
    app = SimpleNamespace(
        state=SimpleNamespace(rider=domain, rider_vision=loop, rider_analysis=None)
    )
    resource = RiderModelResources(app)
    await resource.prepare_model_release()
    assert calls == ["disarm"]
    assert domain.goal.await_count == 2
    domain.stop_camera.assert_awaited_once_with({"action": "stop"}, "session")


@pytest.mark.asyncio
async def test_failed_device_confirmation_does_not_claim_vision_memory_released():
    domain = SimpleNamespace(
        device=object(), read=AsyncMock(side_effect=RuntimeError("offline"))
    )
    loop = SimpleNamespace(depth_detector=SimpleNamespace(model=object()))
    resource = RiderModelResources(
        SimpleNamespace(state=SimpleNamespace(rider=domain, rider_vision=loop))
    )
    with pytest.raises(RuntimeError, match="offline"):
        await resource.prepare_model_release()
    assert resource.release_model_resource() is False


@pytest.mark.asyncio
async def test_suspended_vision_cannot_reload_from_passive_preview_polling():
    loop = VisionLoop()
    loop.resources_suspended = True
    try:
        with pytest.raises(RiderError, match="model_resources_released"):
            await loop._admit_inference("device", None, None, True)
    finally:
        await loop.close()


@pytest.mark.asyncio
async def test_cold_host_release_does_not_require_an_online_robot():
    domain = SimpleNamespace(
        device=object(), read=AsyncMock(side_effect=RuntimeError("offline"))
    )
    loop = SimpleNamespace(busy=None, depth_busy=None)
    app = SimpleNamespace(
        state=SimpleNamespace(rider=domain, rider_vision=loop, rider_analysis=None)
    )
    resource = RiderModelResources(app)
    await resource.prepare_model_release()
    domain.read.assert_not_awaited()
    assert resource.prepared


@pytest.mark.asyncio
async def test_unconfirmed_archive_child_cannot_reuse_previous_preparation():
    loop = SimpleNamespace(busy=None, depth_busy=None)
    analysis = SimpleNamespace(tasks={}, children={"orphan": object()})
    resource = RiderModelResources(
        SimpleNamespace(
            state=SimpleNamespace(
                rider=SimpleNamespace(device=None),
                rider_vision=loop,
                rider_analysis=analysis,
            )
        )
    )
    resource.prepared = True
    with pytest.raises(RuntimeError, match="analysis_unconfirmed"):
        await resource.prepare_model_release()
    assert resource.release_model_resource() is False


@pytest.mark.asyncio
async def test_allocated_but_unloaded_detector_does_not_require_an_online_robot():
    domain = SimpleNamespace(
        device=object(), read=AsyncMock(side_effect=RuntimeError("offline"))
    )
    loop = SimpleNamespace(busy=None, depth_busy=None)
    detector = SimpleNamespace(status=lambda: {"loaded": False})
    app = SimpleNamespace(
        state=SimpleNamespace(
            rider=domain,
            rider_vision=loop,
            rider_analysis=None,
            rider_models=SimpleNamespace(runtime=SimpleNamespace(detector=detector)),
        )
    )
    resource = RiderModelResources(app)
    await resource.prepare_model_release()
    domain.read.assert_not_awaited()
    assert resource.prepared


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "safety",
    [
        {},
        {"state": "DISARMED"},
        {"can_move": None},
        {"can_move": 0},
        {"can_move": False, "state": "ARMED"},
    ],
)
async def test_unknown_or_conflicting_safety_requires_confirmed_disarm(safety):
    domain = SimpleNamespace(
        device=object(),
        session="fixture",
        read=AsyncMock(return_value={"data": {"safety": safety}}),
        control=AsyncMock(),
        pipeline=AsyncMock(),
        stop_camera=AsyncMock(),
    )
    loop = SimpleNamespace(depth_detector=SimpleNamespace(model=object()))
    resource = RiderModelResources(
        SimpleNamespace(
            state=SimpleNamespace(
                rider=domain,
                rider_vision=loop,
            )
        )
    )
    with pytest.raises(RuntimeError, match="disarm_unconfirmed"):
        await resource.prepare_model_release()
    domain.control.assert_awaited_once_with({"action": "disarm"}, "fixture")
    domain.pipeline.assert_not_awaited()
    domain.stop_camera.assert_not_awaited()
    assert resource.release_model_resource() is False


@pytest.mark.asyncio
async def test_initially_unknown_safety_can_be_explicitly_disarmed():
    domain = SimpleNamespace(
        device=object(),
        session="fixture",
        read=AsyncMock(
            side_effect=[
                {"data": {"safety": {}}},
                {"data": {"safety": {"can_move": False, "state": "DISARMED"}}},
            ]
        ),
        control=AsyncMock(),
        pipeline=AsyncMock(return_value={"effective_stages": {"capture": False}}),
    )
    loop = SimpleNamespace(
        busy=None, depth_busy=None, depth_detector=SimpleNamespace(model=object())
    )
    resource = RiderModelResources(
        SimpleNamespace(
            state=SimpleNamespace(
                rider=domain,
                rider_vision=loop,
                rider_analysis=None,
            )
        )
    )
    await resource.prepare_model_release()
    domain.control.assert_awaited_once_with({"action": "disarm"}, "fixture")
    assert resource.prepared
