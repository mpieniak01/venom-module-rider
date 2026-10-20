"""Whole-decision deadlines and isolated inference scheduling, without hardware."""

import asyncio
import threading

import pytest
from test_vision_loop import Detector, frame

from venom_module_rider import vision_loop
from venom_module_rider.vision_loop import VisionLoop


async def test_live_inference_does_not_use_shared_application_executor(monkeypatch):
    names = []

    class Recording(Detector):
        def detect(self, *args):
            names.append(threading.current_thread().name)
            return super().detect(*args)

    async def shared_queue(*_args, **_kwargs):
        raise AssertionError("unrelated application work owns the shared queue")

    monkeypatch.setattr(asyncio, "to_thread", shared_queue)
    loop = VisionLoop(Recording())
    try:
        result = await loop.decide("pi", frame(), b"fixture")
        assert result["state"] == "clear"
        assert names and names[0].startswith("rider-vision")
    finally:
        await loop.close()


async def test_planning_time_cannot_publish_go_after_original_deadline(monkeypatch):
    now = [0.0]
    original = vision_loop.record_result

    def slow_planner(state, capture, action, result):
        original(state, capture, action, result)
        if capture.sequence == 3:
            now[0] += 0.31

    monkeypatch.setattr(vision_loop, "record_result", slow_planner)
    loop = VisionLoop(Detector(), clock=lambda: now[0])
    try:
        for seq in range(1, 4):
            result = await loop.decide("pi", frame(seq), b"fixture")
        assert result["action"] == result["vision_action"] == "stop"
        assert result["reason"] == "vision_decision_expired"
        assert result["valid_for_ms"] == 0
        assert result["clear_count"] == 0
        assert loop.runs[("pi", "run")]["count"] == 0
    finally:
        await loop.close()


async def test_model_failure_has_sanitized_cause_without_private_exception_text():
    class Failed(Detector):
        def detect(self, *_args):
            raise ValueError("private-input-or-path")

    loop = VisionLoop(Failed())
    try:
        result = await loop.decide("pi", frame(), b"fixture")
        assert result["inference_error"] == "vision_model_failure"
        assert result["action"] == "stop"
        assert "private-input" not in str(result)
    finally:
        await loop.close()


async def test_reference_budget_also_bounds_source_preparation(monkeypatch):
    import json
    import time
    from types import SimpleNamespace

    from test_stream_contract import native

    from venom_module_rider.api import vision_routes
    from venom_module_rider.errors import RiderError

    source = native().source.reference().model_dump(mode="json")
    payload = json.dumps(
        {
            "frame": frame()
            .model_copy(update={"deadline_ms": 50})
            .model_dump(mode="json"),
            "source": source,
        }
    ).encode()
    monkeypatch.setattr(vision_routes, "authenticate", lambda _request: "pi")

    async def body(_request, _limit):
        return payload

    cancelled = []

    async def prepare(*_args):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)

    monkeypatch.setattr(vision_routes, "bounded_body", body)
    monkeypatch.setattr(vision_routes, "_reference_decision", prepare)
    request = SimpleNamespace(
        headers={"Content-Type": "application/json"},
        app=SimpleNamespace(
            state=SimpleNamespace(rider_stream=SimpleNamespace(clock=time.monotonic))
        ),
    )
    with pytest.raises(RiderError, match="vision_frame_expired"):
        await asyncio.wait_for(vision_routes.reference_decision(request), 0.5)
    assert cancelled == [True]
