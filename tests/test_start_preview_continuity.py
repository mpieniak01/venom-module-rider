"""Real stream handoff uses fresh passive results without granting movement."""

import asyncio
import json

import pytest
from test_stream_browser import Browser, setup, unpack

from venom_module_rider.api.pipeline_routes import browser_stream
from venom_module_rider.api.vision_preview import diagnostic_preview_allowed


@pytest.mark.parametrize(
    "phase", ["stop_old_producer", "configure", "start_navigation", "first_status"]
)
async def test_preparing_journey_keeps_passive_preview_in_same_camera_session(phase):
    pi, domain, hub, loop, state = await setup()
    original = pi.json

    async def read(path, **kwargs):
        if path == "/api/navigation/status":
            return {
                "data": {
                    "running": False,
                    "run_id": "previous",
                    "preparation": {
                        "state": "preparing",
                        "fresh": True,
                        "journey_version": 1,
                        "phase": phase,
                    },
                }
            }
        if path == "/api/logic/state":
            return {
                "data": {
                    "features": {
                        "s14_venom_navigation": {"requested": True},
                        "s15_venom_perception": {"requested": False},
                    }
                }
            }
        if path == "/api/control/state":
            return {
                "data": {
                    "safety": {
                        "state": "DISARMED",
                        "can_move": False,
                        "moving": False,
                        "stale": False,
                    }
                }
            }
        return await original(path, **kwargs)

    pi.json = read
    iterator = (await browser_stream("venom", Browser(state))).body_iterator
    task = asyncio.create_task(anext(iterator))
    try:
        headers, _ = unpack(await asyncio.wait_for(task, 1))
        first = json.loads(headers["x-rider-analysis-json"])
        await pi.emit(2)
        headers, _ = unpack(await asyncio.wait_for(anext(iterator), 1))
        second = json.loads(headers["x-rider-analysis-json"])
        assert first["source"]["camera_session"] == second["source"]["camera_session"]
        assert second["source"]["sequence"] > first["source"]["sequence"]
        assert first["preview_only"] is second["preview_only"] is True
        assert "journey" not in second
        assert headers["x-rider-vision-action"] == "stop"
        assert pi.opened == 1 and pi.snapshot_requests == 0
        assert not any(method != "GET" for method, _path, _body in pi.calls)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await iterator.aclose()
        await hub.close()
        await loop.close()
        await domain.close()


@pytest.mark.parametrize(
    "safety,expected",
    [
        (
            {"state": "DISARMED", "can_move": False, "moving": False, "stale": False},
            True,
        ),
        (
            {"state": "DISARMED", "can_move": False, "moving": False, "stale": True},
            False,
        ),
        (
            {
                "state": "ARMED",
                "owner": "navigation",
                "can_move": True,
                "stale": False,
                "arming_mode": "journey",
                "operator_control": {"state": "qualifying"},
            },
            True,
        ),
        (
            {
                "state": "ARMED",
                "owner": "navigation",
                "can_move": True,
                "stale": False,
                "arming_mode": "journey",
                "operator_control": {"state": "active"},
            },
            False,
        ),
    ],
)
async def test_preparation_preview_is_not_permission_for_active_navigation(
    safety, expected
):
    class Domain:
        async def read(self, _path, _ttl):
            return {"data": {"safety": safety}}

    status = {
        "running": False,
        "preparation": {"state": "preparing", "fresh": True, "journey_version": 1},
    }
    assert (
        await diagnostic_preview_allowed(
            Domain(), status, {"observe": False, "drive": True}
        )
        is expected
    )


async def test_pi_result_takes_over_reader_without_duplicate_inference():
    from venom_module_rider.vision_contract import VisionFrame

    pi, domain, hub, loop, state = await setup()
    original = pi.json
    producer = {
        "running": False,
        "run_id": "old",
        "preparation": {"state": "preparing", "fresh": True, "journey_version": 1},
    }

    async def read(path, **kwargs):
        if path == "/api/navigation/status":
            return {"data": dict(producer)}
        if path == "/api/logic/state":
            return {
                "data": {
                    "features": {
                        "s14_venom_navigation": {"requested": True},
                        "s15_venom_perception": {"requested": False},
                    }
                }
            }
        if path == "/api/control/state":
            return {
                "data": {
                    "safety": {
                        "state": "DISARMED",
                        "can_move": False,
                        "moving": False,
                        "stale": False,
                    }
                }
            }
        return await original(path, **kwargs)

    pi.json = read
    iterator = (await browser_stream("venom", Browser(state))).body_iterator
    try:
        before_headers, _ = unpack(await asyncio.wait_for(anext(iterator), 1))
        before = json.loads(before_headers["x-rider-analysis-json"])
        connection = hub.connection
        previous = await hub.frame(connection, timeout=0.1)
        await pi.emit(2)
        incoming = await hub.frame(
            connection, after=previous.source.identity(), timeout=0.5
        )
        payload = await hub.canonical_frame(connection, incoming)
        config = (await domain.read("/api/navigation/config", 0.2))["data"]
        # This is a distinct Pi-produced reply for a new capture/run, not a
        # presentation result relabelled as a navigation permission.
        frame = VisionFrame(
            protocol_version=1,
            run_id="pi-run",
            frame_id="pi-frame-2",
            sequence=1,
            config_revision=config["revision"],
            age_ms=incoming.age_ms(hub.clock()),
            deadline_ms=300,
            mode="observe",
            corridor=tuple(config["corridor"]),
        )
        await loop.decide(
            "pi",
            frame,
            payload,
            with_preview=True,
            source_reference=incoming.source.reference().model_dump(mode="json"),
        )
        producer.update(running=True, stale=False, run_id="pi-run", mode="observe")
        domain.cache.pop("/api/navigation/status", None)
        headers, _ = unpack(await asyncio.wait_for(anext(iterator), 1))
        after = json.loads(headers["x-rider-analysis-json"])
        assert before["preview_only"] is True
        assert after["run_id"] == "pi-run" and not after.get("preview_only")
        assert after["source"]["sequence"] == 2
        assert after["source"]["camera_session"] == before["source"]["camera_session"]
        assert (
            loop.detector.calls == 2
        )  # One diagnostic + one Pi, no browser duplicate.
        assert pi.opened == 1 and pi.snapshot_requests == 0
        assert not any(method != "GET" for method, _path, _body in pi.calls)
    finally:
        await iterator.aclose()
        await hub.close()
        await loop.close()
        await domain.close()
