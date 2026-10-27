"""Panel identity is forwarded by the real service; SDK outputs are fixtures."""

import time

import pytest
from pydantic import ValidationError
from test_rider import command, domain

from venom_module_rider.api.schemas import Command
from venom_module_rider.errors import RiderError


@pytest.mark.parametrize("invalid", ["", "   ", "x" * 129, True, 7])
def test_identity_is_strict_and_bounded(invalid):
    with pytest.raises(ValidationError):
        Command(action="arm", panel_id=invalid)


def test_old_commands_remain_compatible():
    assert "panel_id" not in Command(action="stop").model_dump(exclude_none=True)
    assert Command(action="arm", panel_id="panel-a").panel_id == "panel-a"


async def bind(service, pi, panel):
    return await service.control(
        command(
            pi,
            "arm",
            owner="manual",
            arming_mode="operator",
            operator_consent=True,
            operator_plan="Isolated authority test",
            panel_id=panel,
        ),
        service.session,
    )


async def test_two_panels_use_explicit_takeover_and_old_generation_cannot_resume():
    service, pi = domain()
    await service.select("demo", service.session)
    try:
        await bind(service, pi, "panel-a")
        status = await service.status("navigation")
        assert status["control"]["safety"]["panel_control"] == {
            "version": 1,
            "holder": "panel-a",
        }
        first = command(
            pi,
            "move",
            direction="forward",
            mode="hold",
            device_settings=True,
            panel_id="panel-a",
        )
        accepted = await service.control(first, service.session)
        assert accepted["data"]["ack"]["panel_id"] == "panel-a"
        assert pi.safety()["moving"]
        deadline = pi.deadline
        foreign = command(
            pi,
            "move",
            direction="left",
            mode="hold",
            device_settings=True,
            panel_id="panel-b",
        )
        with pytest.raises(RiderError, match="panel_owner_mismatch"):
            await service.control(foreign, service.session)
        assert pi.deadline == deadline
        # Global STOP is available even to a stale panel and needs no envelope.
        await service.control({"action": "stop"}, "old-session")
        assert not pi.safety()["moving"]
        assert pi.safety()["panel_control"]["holder"] is None
        await bind(service, pi, "panel-b")
        with pytest.raises(RiderError):
            await service.control(first, service.session)
        assert pi.safety()["panel_control"]["holder"] == "panel-b"
        # Fresh timestamps/sequence cannot let a former holder reclaim control.
        with pytest.raises(RiderError, match="panel_owner_mismatch"):
            await service.control(
                command(
                    pi,
                    "move",
                    direction="forward",
                    mode="hold",
                    device_settings=True,
                    panel_id="panel-a",
                ),
                service.session,
            )
        accepted = await service.control(
            command(
                pi,
                "move",
                direction="right",
                mode="hold",
                device_settings=True,
                panel_id="panel-b",
            ),
            service.session,
        )
        assert accepted["data"]["ack"]["status"] == "executed"
        assert pi.safety()["moving"]
        await service.control(
            command(
                pi,
                "move",
                direction="right",
                mode="release",
                device_settings=True,
                panel_id="panel-b",
            ),
            service.session,
        )
        assert not pi.safety()["moving"]
        assert pi.state == "ARMED"
    finally:
        await service.close()


async def test_fixture_watchdog_expires_without_disarming_or_archiving():
    service, pi = domain()
    await service.select("demo", service.session)
    try:
        await bind(service, pi, "panel-a")
        await service.control(
            command(
                pi,
                "move",
                direction="forward",
                mode="hold",
                device_settings=True,
                panel_id="panel-a",
            ),
            service.session,
        )
        pi.deadline = time.monotonic() - 1
        assert not pi.safety()["moving"]
        assert pi.state == "ARMED"
        assert not pi.camera_on
    finally:
        await service.close()


def test_in_place_turn_schema_is_scoped():
    assert (
        Command(
            action="move", direction="left", device_settings=True, turn_mode="in_place"
        ).turn_mode
        == "in_place"
    )
    for values in (
        {"action": "height"},
        {"action": "move", "direction": "forward", "device_settings": True},
        {"action": "move", "direction": "left", "device_settings": False},
    ):
        with pytest.raises(ValidationError):
            Command(**values, turn_mode="in_place")
