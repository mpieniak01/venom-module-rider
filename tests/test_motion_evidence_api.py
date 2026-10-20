"""Operator reports use saved data only, even with the device disconnected."""

import hashlib

import httpx
from test_rider import domain, host_app

from venom_module_rider.samples_registry import SamplesRegistry

BASE = "/api/v1/rider/samples"


def imu(index):
    timestamp = 10.0 + index * 0.34
    return {
        "schema_version": 1,
        "record_id": hashlib.sha256(str(index).encode()).hexdigest(),
        "kind": "imu",
        "device_id": "recorded",
        "source_session": "host-source",
        "source_instance_id": "boot",
        "source_observed_at": timestamp,
        "received_mono": 100.0 + index * 0.34,
        "received_at": 1000.0 + index * 0.34,
        "values": {"accel_x": 0.0, "accel_y": 0.0, "accel_z": 9.81, "yaw": 0.0},
        "units": {
            "accel_x": "m/s^2",
            "accel_y": "m/s^2",
            "accel_z": "m/s^2",
            "yaw": "sdk_raw",
        },
        "quality": {
            "ready": True,
            "stale": False,
            "offline": False,
            "simulated": False,
            "physical_validation": "unverified",
            "sensors": {
                "yaw": {"observed_at": timestamp, "unit": "sdk_raw", "ready": True}
            },
        },
    }


async def test_telemetry_only_saved_session_reports_without_any_pi_request(tmp_path):
    service, pi = domain()
    await service.select("demo", service.session)
    registry = SamplesRegistry(tmp_path / "data")
    app = host_app(service)
    app.state.rider_samples = registry
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )
    try:
        session = await registry.start("collect", "recorded")
        sid = session["session_id"]
        await registry.read(
            registry.store.evidence.append, sid, [imu(i) for i in range(4)]
        )
        await registry.end(sid)
        pi.offline = True
        trace = len(pi.trace)
        response = await client.get(BASE + "/sessions/" + sid + "/motion-evidence")
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["status"] == "available"
        assert report["counts"]["imu_records"] == 4
        assert report["input"]["dataset_revision"]
        assert report["clocks"]["source_gaps_s"]["p50"] > 0.15
        assert report["clocks"]["yaw_angular_rate"]["reason"] == "calibration_required"
        assert report["physical_velocity"]["value"] is None
        assert report["physical_distance"]["value"] is None
        assert len(pi.trace) == trace
    finally:
        await client.aclose()
        await registry.close()
        await service.close()


async def test_legacy_empty_session_is_unavailable_not_fake_zero(tmp_path):
    service, pi = domain()
    registry = SamplesRegistry(tmp_path / "data")
    app = host_app(service)
    app.state.rider_samples = registry
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )
    try:
        session = await registry.start("legacy", "recorded")
        await registry.end(session["session_id"])
        response = await client.get(
            BASE + "/sessions/" + session["session_id"] + "/motion-evidence"
        )
        assert response.status_code == 200
        assert response.json()["status"] == "unavailable"
        assert response.json()["physical_velocity"]["value"] is None
        assert not pi.trace
        missing = await client.get(BASE + "/sessions/" + "0" * 32 + "/motion-evidence")
        assert missing.status_code == 404
    finally:
        await client.aclose()
        await registry.close()
        await service.close()
