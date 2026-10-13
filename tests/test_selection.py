"""Two isolated fixtures exercise panel selection; no physical robot is used."""

import asyncio
import json
import time

import httpx
import pytest

from venom_module_rider.client import PiClient
from venom_module_rider.config import Device, persist_device, restored_device
from venom_module_rider.demo import DemoPi, create_app
from venom_module_rider.errors import RiderError
from venom_module_rider.service import RiderService

DEVICES = (
    Device("demo", "Demo", "http://127.0.0.1:18880", "demo"),
    Device("prod", "Prod fixture", "http://127.0.0.1:18881", "device"),
)


def fixtures(path):
    pis = {device.id: DemoPi() for device in DEVICES}

    def client(device):
        return PiClient(
            device, transport=httpx.ASGITransport(app=create_app(pis[device.id]))
        )

    return (
        RiderService(DEVICES, client_factory=client, selection_file=path),
        pis,
        client,
    )


async def test_switch_stops_old_motion_and_loops_then_restores_without_activation(
    tmp_path,
):
    path = tmp_path / "selection.json"
    service, pis, factory = fixtures(path)
    await service.restore("demo")
    assert (
        path.exists() is False
    )  # Reading/startup does not write operator preferences.
    old = service.session
    pis["demo"].state = "ARMED"
    pis["demo"].deadline = time.monotonic() + 10
    pis["demo"].goals.update(s14_venom_navigation=True, s15_venom_perception=True)
    service.cache["old"] = (1, {})
    service.sequences["old"] = 20
    result = await service.select("prod", old)
    assert result["device_id"] == "prod" and result["session"] != old
    assert pis["demo"].state == "DISARMED" and not pis["demo"].safety()["moving"]
    assert not pis["demo"].goals["s14_venom_navigation"]
    assert not pis["demo"].goals["s15_venom_perception"]
    assert pis["prod"].trace == [] and pis["prod"].state == "DISARMED"
    assert not service.cache and not service.sequences
    with pytest.raises(RiderError, match="stale_device_session"):
        await service.mutate("/api/resource/camera", {"enabled": True}, old)
    assert json.loads(path.read_text()) == {"version": 1, "device_id": "prod"}
    assert path.stat().st_mode & 0o777 == 0o600
    await service.close()

    restarted = RiderService(DEVICES, client_factory=factory, selection_file=path)
    await restarted.restore("demo")
    assert restarted.device.id == "prod"
    assert pis["prod"].trace == []  # No ARM, camera or loop activation on restore.
    await restarted.select("demo", restarted.session)
    assert restarted.device.id == "demo"
    assert not any(row["method"] == "POST" for row in pis["prod"].trace)
    await restarted.select("demo", restarted.session)
    await restarted.close()


@pytest.mark.parametrize(
    "saved",
    [
        "broken",
        "[]",
        '{"version":1,"device_id":"removed"}',
        '{"version":true,"device_id":"prod"}',
        '{"version":1}',
        '{"version":1,"device_id":[]}',
    ],
)
def test_invalid_saved_choice_never_falls_back_to_prod(tmp_path, saved):
    path = tmp_path / "selection.json"
    path.write_text(saved)
    devices = {d.id: d for d in DEVICES}
    assert restored_device(path, devices, "prod") == "demo"
    assert restored_device(path, {"prod": DEVICES[1]}, "prod") is None


def test_selection_can_be_explicitly_disconnected_and_write_failures_are_visible(
    tmp_path,
):
    path = tmp_path / "selection.json"
    persist_device(path, None)
    assert restored_device(path, {d.id: d for d in DEVICES}, "demo") is None
    persist_device(None, "demo")
    blocked = tmp_path / "blocked"
    blocked.write_text("file")
    with pytest.raises(RiderError, match="selection_persistence_failed"):
        persist_device(blocked / "selection.json", "prod")


async def test_offline_old_target_reports_unconfirmed_stop_without_activating_new(
    tmp_path,
):
    service, pis, factory = fixtures(tmp_path / "selection.json")

    def offline(request):
        raise httpx.ConnectError("fixture disconnected", request=request)

    service.factory = lambda device: (
        PiClient(device, transport=httpx.MockTransport(offline))
        if device.id == "prod"
        else factory(device)
    )
    await service.restore("prod")
    result = await service.select("demo", service.session)
    assert result["selection_warning"] == "previous_device_stop_unconfirmed"
    assert pis["demo"].trace == []
    await service.close()


async def test_switch_blocks_new_old_session_commands_and_cancels_pending_operations(
    tmp_path,
):
    service, pis, _ = fixtures(tmp_path / "selection.json")
    await service.restore("demo")
    old_session = service.session
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.client.json

    async def delayed(path, **kwargs):
        if path == "/api/resource/camera":
            await asyncio.Event().wait()
        if path == "/api/control/state":
            entered.set()
            await release.wait()
        return await original(path, **kwargs)

    service.client.json = delayed
    mutation = asyncio.create_task(
        service.mutate("/api/resource/camera", {"enabled": True}, old_session)
    )
    await asyncio.sleep(0)
    selection = asyncio.create_task(service.select("prod", old_session))
    await entered.wait()
    with pytest.raises(RiderError, match="device_switch_in_progress"):
        await service.mutate("/api/resource/lcd", {"enabled": True}, old_session)
    with pytest.raises(RiderError, match="stale_device_session"):
        await mutation
    release.set()
    await selection
    assert pis["prod"].trace == []
    await service.close()


async def test_unknown_device_does_not_change_or_persist_selection(tmp_path):
    path = tmp_path / "selection.json"
    service, pis, _ = fixtures(path)
    await service.restore("demo")
    with pytest.raises(RiderError, match="unknown_device"):
        await service.select("missing", service.session)
    assert service.device.id == "demo" and not path.exists()
    assert pis["demo"].trace == []
    await service.close()
