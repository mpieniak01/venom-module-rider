"""Recorded trials use the existing writer. All actuator calls are fixture-only."""

import json
from unittest.mock import patch

import httpx
import pytest
from test_motion_evidence_api import imu
from test_rider import arm, command, domain, host_app

from venom_module_rider.errors import RiderError
from venom_module_rider.evidence_contract import validate_evidence
from venom_module_rider.evidence_store import validate_extension
from venom_module_rider.motion_capture import safe_begin, safe_finish
from venom_module_rider.motion_timeline import build_timeline
from venom_module_rider.samples_archive import SampleArchive
from venom_module_rider.samples_import import SampleImporter
from venom_module_rider.samples_registry import SamplesRegistry
from venom_module_rider.service import POLLING, SECTIONS


async def setup_study(tmp_path):
    service, pi = domain()
    await service.select("demo", service.session)
    await service.status("calibration")
    registry = SamplesRegistry(tmp_path)
    service.samples = registry
    session = await registry.start(
        "study", "demo", {"kind": "manual", "scene_group": "motion-study-v1:wood"}
    )
    return service, pi, registry, session["session_id"]


async def snapshot(registry, sid):
    manifest, revision = await registry.read(
        SampleArchive(registry.store).snapshot, sid
    )
    manifest["dataset_revision"] = revision
    return manifest


async def test_records_profile_request_result_and_stop_survive_retirement(tmp_path):
    service, pi, registry, sid = await setup_study(tmp_path)
    try:
        await arm(service, pi)
        await service.control(
            command(
                pi,
                "move",
                direction="forward",
                mode="pulse",
                device_settings=True,
                settings_revision=pi.controls["revision"],
            ),
            service.session,
        )
        await service.control(command(pi, "stop"), service.session)
        await registry.end(sid)
        manifest = await snapshot(registry, sid)
        assert manifest["evidence"]["schema_version"] == 2
        rows = manifest["evidence"]["records"]
        events = [row["host"] for row in rows if row["kind"] == "command"]
        assert len(events) == 6
        assert {row["phase"] for row in events} == {"requested", "result"}
        stop = [row for row in events if row["intent"]["action"] == "stop"]
        assert len(stop) == 2
        assert stop[-1]["outcome"] == "accepted"
        move = next(
            row
            for row in events
            if row["intent"]["action"] == "move" and row["phase"] == "requested"
        )
        assert move["intent"]["duration_s"] > 0
        assert move["intent"]["intensity"] is not None
        profile = next(
            row["host"] for row in rows if row["record_id"] == move["profile_ref"]
        )
        assert profile["timing_revision"] == 0
        assert profile["timing"]["telemetry_ms"] == 2000
        assert profile["qualification"] == "request-revision-match"
        timeline = build_timeline(manifest)
        assert timeline["context"] == "motion-study-v1:wood"
        assert timeline["streams"][0]["origin"] == "first_move_request"
        assert (
            next(
                e
                for e in timeline["streams"][0]["events"]
                if e["operation_id"] == move["operation_id"]
            )["t"]
            == 0
        )
        assert timeline["physical_velocity"] is None
    finally:
        await registry.close()
        await service.close()


async def test_delivery_adds_telemetry_only_during_explicit_study(tmp_path):
    service, pi = domain()
    await service.select("demo", service.session)
    registry = SamplesRegistry(tmp_path)
    service.samples = registry
    try:
        await service.status("calibration")
        assert not any(row["path"] == "/state" for row in pi.trace)
        session = await registry.start(
            "study", "demo", {"scene_group": "motion-study-v1:carpet"}
        )
        await service.status("calibration")
        assert sum(row["path"] == "/state" for row in pi.trace) == 1
        await service.status("calibration")
        assert sum(row["path"] == "/state" for row in pi.trace) == 1
        await registry.end(session["session_id"])
        service.cache.pop("/state")
        await service.status("calibration")
        assert sum(row["path"] == "/state" for row in pi.trace) == 1
        assert all(row["method"] == "GET" for row in pi.trace)
    finally:
        await registry.close()
        await service.close()


async def test_recorder_fault_never_breaks_stop(tmp_path):
    service, pi, registry, _ = await setup_study(tmp_path)
    try:
        with patch(
            "venom_module_rider.motion_capture.begin",
            side_effect=RuntimeError("bad metadata"),
        ):
            response = await service.control(command(pi, "stop"), service.session)
        assert response["data"]["safety"]["state"] == "DISARMED"
        assert registry.failures >= 1
    finally:
        await registry.close()
        await service.close()


async def test_rejected_and_unknown_results_are_explicit(tmp_path):
    service, pi, registry, sid = await setup_study(tmp_path)
    try:
        with pytest.raises(RiderError):
            await service.control(
                command(pi, "move", direction="forward", mode="pulse"), service.session
            )
        await registry.end(sid)
        rows = (await snapshot(registry, sid))["evidence"]["records"]
        results = [
            row["host"]
            for row in rows
            if row["kind"] == "command" and row["host"]["phase"] == "result"
        ]
        assert len(results) == 1 and results[0]["outcome"] in {"rejected", "unknown"}
    finally:
        await registry.close()
        await service.close()


async def test_allowlist_drops_secret_body_and_deduplicates_profiles(tmp_path):
    service, pi, registry, sid = await setup_study(tmp_path)
    try:
        body = command(
            pi,
            "move",
            direction="forward",
            mode="hold",
            device_settings=True,
            settings_revision=pi.controls["revision"],
        )
        body.update(token="never-store-this", url="private-address")
        one = safe_begin(service, body, service.session)
        safe_finish(service, one, result={"data": {"accepted": True}})
        two = safe_begin(service, body, service.session)
        safe_finish(
            service, two, result={"data": {"accepted": False, "error": "denied"}}
        )
        await registry.end(sid)
        manifest = await snapshot(registry, sid)
        encoded = json.dumps(manifest)
        assert "never-store-this" not in encoded and "private-address" not in encoded
        assert (
            sum(row["kind"] == "profile" for row in manifest["evidence"]["records"])
            == 1
        )
        results = [
            row["host"]["outcome"]
            for row in manifest["evidence"]["records"]
            if row["kind"] == "command" and row["host"]["phase"] == "result"
        ]
        assert results == ["accepted", "rejected"]
    finally:
        await registry.close()
        await service.close()


async def test_bounded_queue_failure_and_late_result_after_close(tmp_path):
    service, pi, registry, sid = await setup_study(tmp_path)
    try:
        with patch.object(registry, "_submit", side_effect=RuntimeError("busy")):
            assert safe_begin(service, command(pi, "stop"), service.session)
        assert registry.evidence.sessions[sid]["dropped"] > 0
        ticket = safe_begin(service, command(pi, "stop"), service.session)
        await registry.end(sid)
        safe_finish(service, ticket, result={"data": {"accepted": True}})
        manifest = await snapshot(registry, sid)
        assert any(row["kind"] == "command" for row in manifest["evidence"]["records"])
    finally:
        await registry.close()
        await service.close()


async def test_versioned_archive_roundtrip_and_offline_api(tmp_path):
    service, pi, registry, sid = await setup_study(tmp_path / "source")
    restored = SamplesRegistry(tmp_path / "target")
    try:
        ticket = safe_begin(service, command(pi, "stop"), service.session)
        safe_finish(service, ticket, result={"data": {"accepted": True}})
        await registry.read(
            registry.store.evidence.append, sid, [imu(0), imu(1), imu(2)]
        )
        await registry.end(sid)
        app = host_app(service)
        app.state.rider_samples = registry
        pi.offline = True
        before = len(pi.trace)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/v1/rider/samples/sessions/" + sid + "/motion-timeline"
            )
        assert response.status_code == 200, response.text
        assert response.json()["counts"]["imu_records"] == 3
        assert len(pi.trace) == before
        archive, _ = await registry.read(SampleArchive(registry.store).export, sid)
        result = await SampleImporter(restored).run(archive, "roundtrip")
        assert result
    finally:
        await restored.close()
        await registry.close()
        await service.close()


def test_v1_rejects_host_records_and_host_clock_mismatch():
    base = imu(0)
    for patch_values in [
        {"schema_version": 2},
        {"schema_version": True},
        {"source_clock_domain": "host-monotonic"},
    ]:
        with pytest.raises(ValueError):
            validate_evidence({**base, **patch_values})
    assert (
        validate_extension({"schema_version": 1, "records": [base], "status": {}})[0][
            "kind"
        ]
        == "imu"
    )


async def test_timeline_breaks_gaps_resets_and_mixed_sources(tmp_path):
    service, _, registry, sid = await setup_study(tmp_path)
    try:
        rows = [imu(i) for i in range(6)]
        for row in rows:
            row["source_instance_id"] = "boot"
            row["quality"]["ready"] = True
        rows[2]["source_observed_at"] = rows[1]["source_observed_at"]
        rows[3]["received_mono"] += 5
        rows[4]["source_instance_id"] = "reboot"
        rows[5]["source_session"] = "other"
        await registry.read(registry.store.evidence.append, sid, rows)
        report = build_timeline(await snapshot(registry, sid))
        assert len(report["streams"]) == 2
        segments = [p["segment"] for p in report["streams"][0]["points"]]
        assert segments[0] == segments[1]
        assert len(set(segments)) == 4
        assert report["streams"][0]["points"][0]["units"]["yaw"] == "sdk_raw"
    finally:
        await registry.close()
        await service.close()


@pytest.mark.parametrize(
    "data",
    [
        None,
        {"safety": None},
        {"ack": "bad"},
        {"safety": {"last_ack": True}},
        {"accepted": True, "safety": None},
    ],
)
async def test_malformed_optional_ack_still_records_explicit_result(tmp_path, data):
    service, pi, registry, sid = await setup_study(tmp_path)
    try:
        ticket = safe_begin(
            service,
            command(pi, "move", direction="forward", mode="pulse"),
            service.session,
        )
        safe_finish(service, ticket, result={"data": data})
        await registry.end(sid)
        events = [
            r["host"]
            for r in (await snapshot(registry, sid))["evidence"]["records"]
            if r["kind"] == "command"
        ]
        result = next(e for e in events if e["phase"] == "result")
        assert result["outcome"] == (
            "accepted"
            if isinstance(data, dict) and data.get("accepted") is True
            else "unknown"
        )
        assert registry.failures == 0
    finally:
        await registry.close()
        await service.close()


@pytest.mark.parametrize(
    "section",
    [
        "control",
        "overview",
        "calibration",
        "resources",
        "face",
        "compute",
        "navigation",
        "vision",
    ],
)
async def test_study_records_across_tabs_with_shared_cache_and_bounded_cadence(
    tmp_path, section
):
    service, pi, registry, sid = await setup_study(tmp_path)
    try:
        normal = service.timing.polling(POLLING)
        service.cache.pop("/state", None)
        before = sum(row["path"] == "/state" for row in pi.trace)
        first = await service.status(section)
        await service.status(section)
        assert sum(row["path"] == "/state" for row in pi.trace) == before + 1
        assert (first["telemetry"] is not None) == ("telemetry" in SECTIONS[section])
        assert first["polling"] == normal
        assert (await snapshot(registry, sid))["evidence"]["records"]
        await registry.end(sid)
        after = await service.status(section)
        assert after["polling"] == normal
        assert all(row["method"] == "GET" for row in pi.trace)
    finally:
        await registry.close()
        await service.close()


async def test_concurrent_status_reports_bounded_writer_drops_without_delays(tmp_path):
    service, pi, registry, sid = await setup_study(tmp_path)
    try:
        # Deterministically keep the existing one-job slot busy while the
        # unchanged gather() delivers control and telemetry without a delay.
        collector = registry.evidence
        assert collector.slot.acquire(blocking=False)
        try:
            status = await service.status("control")
            assert status["telemetry"] is not None
            assert any(row["path"] == "/state" for row in pi.trace)
            assert any(row["path"] == "/api/control/state" for row in pi.trace)
            assert collector.sessions[sid]["dropped"] >= 2
        finally:
            collector.slot.release()
        await registry.end(sid)
        manifest = await snapshot(registry, sid)
        assert manifest["evidence"]["status"]["dropped"] >= 2
        assert all(row["method"] == "GET" for row in pi.trace)
    finally:
        await registry.close()
        await service.close()
