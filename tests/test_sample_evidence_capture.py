"""Host evidence uses received fixtures only, retaining source clocks and identity."""

import asyncio
import copy
import threading
import time
import zipfile

import pytest
from test_samples_store import frame

from venom_module_rider.errors import RiderError
from venom_module_rider.samples_archive import SampleArchive, canonical
from venom_module_rider.samples_frame import FrameBuffer
from venom_module_rider.samples_import import SampleImporter
from venom_module_rider.samples_registry import SamplesRegistry
from venom_module_rider.samples_store import SampleStore, StorageLimits


def imu(stamp=10.0, yaw=362.0):
    return {
        "meta": {},
        "data": {
            "devices": {
                "xgo": {
                    "instance_id": "pi-one",
                    "observed_at": stamp,
                    "sensor_quality": {
                        "ready": True,
                        "sensors": {
                            "yaw": {
                                "value": yaw,
                                "unit": "sdk_raw",
                                "observed_at": stamp,
                                "ready": True,
                                "stale": False,
                                "physical_validation": "unverified",
                            }
                        },
                    },
                }
            }
        },
    }


async def drain(registry):
    await asyncio.wrap_future(registry.io.submit(lambda: None))


def snapshot(registry, sid):
    return SampleArchive(registry.store).snapshot(sid)[0]


async def test_inactive_and_other_paths_cannot_record_or_create_transport(tmp_path):
    registry = SamplesRegistry(tmp_path)
    try:
        registry.observe_evidence("/state", imu(), "pi", "session")
        assert registry.evidence.status()["ram_records"] == 0
        session = await registry.start("one", "pi")
        registry.observe_evidence("/healthz", imu(), "pi", "session")
        registry.observe_evidence("/state", imu(), "other", "session")
        await drain(registry)
        assert "evidence" not in snapshot(registry, session["session_id"])
    finally:
        await registry.close()


async def test_raw_yaw_source_clock_dedup_cache_clones_and_eviction(tmp_path):
    now = [100.0]
    registry = SamplesRegistry(tmp_path, frames=FrameBuffer(clock=lambda: now[0]))
    try:
        sid = (await registry.start("one", "pi"))["session_id"]
        registry.observe_evidence("/state", imu(), "pi", "selected", 100.0, 1000.0)
        await drain(registry)
        first = snapshot(registry, sid)["evidence"]["records"][0]
        assert first["values"]["yaw"] == 362.0
        assert first["source_observed_at"] == 10.0
        assert first["received_at"] == 1000.0
        assert first["source_clock_domain"] == "device-wall-unsynchronised"
        clone = copy.deepcopy(imu())
        clone["meta"]["host_cache_age_ms"] = 2000
        clone["data"]["devices"]["xgo"]["sensor_quality"]["sensors"]["yaw"]["stale"] = (
            True
        )
        now[0] = 140
        registry.observe_evidence("/state", clone, "pi", "selected", 140.0, 1040.0)
        await drain(registry)
        assert registry.evidence.status()["ram_records"] == 0
        assert snapshot(registry, sid)["evidence"]["records"] == [first]
    finally:
        await registry.close()


async def test_image_association_preserves_source_reference_and_unmatched_exposure(
    tmp_path,
):
    registry = SamplesRegistry(tmp_path)
    try:
        await registry.start("one", "pi")
        registry.observe_evidence("/state", imu(), "pi", "selected")
        await drain(registry)
        source = frame()
        key = registry.offer(
            "pi",
            source.payload,
            source.mime,
            {
                "host_device_session": "selected",
                "association": "exact-source-reference",
                "source_identity": {"original": "camera-frame"},
            },
        )
        context = registry.frames.get(key).context()
        assert context["association"] == "exact-source-reference"
        assert context["source_identity"] == {"original": "camera-frame"}
        assert context["evidence"]["record_ids"]
        assert context["telemetry"]["association"].endswith("unmatched-exposure")
        wrong = registry.offer(
            "pi",
            source.payload,
            source.mime,
            {
                "host_device_session": "old",
            },
        )
        assert "evidence" not in registry.frames.get(wrong).context()
    finally:
        await registry.close()


async def test_source_switch_stop_and_end_retire_without_resuming(tmp_path):
    registry = SamplesRegistry(tmp_path)
    try:
        sid = (await registry.start("one", "pi"))["session_id"]
        registry.observe_evidence("/state", imu(), "pi", "selected")
        await drain(registry)
        registry.observe_evidence("/state", imu(11), "pi", "changed")
        registry.observe_evidence("/state", imu(12), "pi", "selected")
        await drain(registry)
        assert len(snapshot(registry, sid)["evidence"]["records"]) == 1
        assert registry.evidence.status()["ram_records"] == 0
        await registry.end(sid)
        assert not registry.evidence.sessions
        sid = (await registry.start("two", "pi"))["session_id"]
        registry.retire_evidence("stop")
        registry.observe_evidence("/state", imu(13), "pi", "changed")
        await drain(registry)
        assert "evidence" not in snapshot(registry, sid)
    finally:
        await registry.close()


async def test_one_evidence_job_is_nonblocking_and_retired_pending_drops(
    tmp_path,
):
    registry = SamplesRegistry(tmp_path, queue_slots=2)
    release = threading.Event()
    begun = threading.Event()
    try:
        sid = (await registry.start("one", "pi"))["session_id"]

        def block():
            begun.set()
            release.wait(5)

        registry._submit(block)
        await asyncio.to_thread(begun.wait, 1)
        before = time.monotonic()
        registry.observe_evidence("/state", imu(), "pi", "selected")
        registry.observe_evidence("/state", imu(11), "pi", "selected")
        assert time.monotonic() - before < 0.2
        assert registry.evidence.status()["dropped"] == 1
        registry.retire_evidence("stop")
        release.set()
        await drain(registry)
        assert registry.evidence.status()["dropped"] == 2
        assert "evidence" not in snapshot(registry, sid)
    finally:
        release.set()
        await registry.close()


async def test_failed_writes_and_ram_bounds_are_visible(tmp_path, monkeypatch):
    registry = SamplesRegistry(tmp_path)
    try:
        sid = (await registry.start("one", "pi"))["session_id"]

        def fail(*args, **kwargs):
            raise OSError("fixture disk full")

        monkeypatch.setattr(registry.store.evidence, "append", fail)
        for stamp in range(70):
            registry.observe_evidence("/state", imu(float(stamp)), "pi", "selected")
            await drain(registry)
        status = registry.evidence.status()
        assert status["failed"] == 70
        assert status["ram_records"] <= 64 and status["ram_bytes"] <= 65536
        await registry.end(sid)
        assert registry.store.session(sid)["evidence"]["failed"] == 70
    finally:
        await registry.close()


async def test_telemetry_only_roundtrip_revision_and_tamper(tmp_path):
    registry = SamplesRegistry(tmp_path / "source")
    target = SamplesRegistry(tmp_path / "target")
    try:
        sid = (await registry.start("one", "pi"))["session_id"]
        registry.observe_evidence("/state", imu(), "pi", "selected")
        await drain(registry)
        before = SampleArchive(registry.store).snapshot(sid)[1]
        registry.observe_evidence("/state", imu(11), "pi", "selected")
        await drain(registry)
        assert SampleArchive(registry.store).snapshot(sid)[1] != before
        await registry.end(sid)
        path, _ = SampleArchive(registry.store).export(sid)
        manifest = SampleArchive.validate_zip(path, registry.store.limits.session_bytes)
        assert manifest["samples"] == [] and len(manifest["evidence"]["records"]) == 2
        imported = await SampleImporter(target).run(path, "import")
        assert imported["evidence"]["saved"] == 2 and imported["state"] == "closed"
        assert (
            snapshot(target, imported["session_id"])["evidence"]["records"]
            == manifest["evidence"]["records"]
        )
        assert (await SampleImporter(target).run(path, "import"))[
            "session_id"
        ] == imported["session_id"]
        manifest["evidence"]["records"][0]["values"]["yaw"] = 123.0
        bad = tmp_path / "tampered.zip"
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("manifest.json", canonical(manifest))
        with pytest.raises(RiderError, match="samples_import_hash"):
            await SampleImporter(target).run(bad, "bad-import")
        assert len(target.store.sessions()) == 1
    finally:
        await registry.close()
        await target.close()


async def test_evidence_respects_shared_session_quota_and_export_import_validation(
    tmp_path,
):
    store = SampleStore(tmp_path, limits=StorageLimits(session_bytes=1))
    registry = SamplesRegistry(tmp_path, store=store)
    try:
        sid = (await registry.start("one", "pi"))["session_id"]
        registry.observe_evidence("/state", imu(), "pi", "selected")
        await drain(registry)
        assert registry.evidence.status()["failed"] == 1
        assert snapshot(registry, sid)["evidence"]["records"] == []
        assert registry.store.session(sid)["evidence"]["failed"] == 1
    finally:
        await registry.close()


async def test_expired_collection_does_not_admit_observations(tmp_path):
    now = [0.0]
    registry = SamplesRegistry(tmp_path, frames=FrameBuffer(clock=lambda: now[0]))
    try:
        sid = (await registry.start("one", "pi"))["session_id"]
        now[0] = registry.store.limits.session_seconds + 1
        registry.observe_evidence("/state", imu(), "pi", "selected")
        await drain(registry)
        assert (
            registry.evidence.status()["sessions"][sid]["retired_reason"]
            == "session_expired"
        )
        assert "evidence" not in snapshot(registry, sid)
    finally:
        await registry.close()


async def test_executor_observation_is_unverified_and_multiple_sessions_share_writer(
    tmp_path,
):
    registry = SamplesRegistry(tmp_path)
    try:
        one = (await registry.start("one", "pi"))["session_id"]
        two = (await registry.start("two", "pi"))["session_id"]
        registry.observe_evidence(
            "/api/control/state",
            {
                "data": {
                    "safety": {
                        "instance_id": "executor-one",
                        "observed_at": 12.0,
                        "state": "DISARMED",
                        "output": {"lx": 0, "az": 0},
                        "last_ack": {"status": "accepted"},
                    }
                },
                "meta": {},
            },
            "pi",
            "selected",
        )
        await drain(registry)
        first = snapshot(registry, one)["evidence"]["records"][0]
        assert first == snapshot(registry, two)["evidence"]["records"][0]
        assert first["executor"]["last_ack"]["status"] == "accepted"
        assert first["quality"]["physical_validation"] == "unverified"
        assert first["quality"]["ready"] == "unknown"
        await registry.end(one)
        registry.observe_evidence("/state", imu(), "pi", "selected")
        await drain(registry)
        assert len(snapshot(registry, two)["evidence"]["records"]) == 2
    finally:
        await registry.close()


async def test_full_writer_drops_without_waiting_and_oversize_is_rejected(tmp_path):
    registry = SamplesRegistry(tmp_path, queue_slots=1)
    release = threading.Event()
    try:
        sid = (await registry.start("one", "pi"))["session_id"]
        registry._submit(lambda: release.wait(5))
        before = time.monotonic()
        registry.observe_evidence("/state", imu(), "pi", "selected")
        assert time.monotonic() - before < 0.2
        assert registry.evidence.status()["dropped"] == 1
        large = imu(20)
        large["data"]["devices"]["xgo"]["sensor_quality"]["sensors"]["yaw"][
            "reason"
        ] = "x" * 8192
        registry.observe_evidence("/state", large, "pi", "selected")
        assert registry.evidence.status()["dropped"] == 2
        release.set()
        await drain(registry)
        assert "evidence" not in snapshot(registry, sid)
        assert registry.evidence.status()["ram_records"] == 0
    finally:
        release.set()
        await registry.close()


def test_store_record_count_and_extension_size_limits_are_atomic(tmp_path):
    from venom_module_rider.evidence_store import validate_extension
    from venom_module_rider.sample_evidence import observations

    store = SampleStore(tmp_path)
    sid = store.start("one", "pi")["session_id"]
    records = [
        observations("/state", imu(float(index)), "pi", "selected", 100.0, 1000.0)[0]
        for index in range(2049)
    ]
    saved, _ = store.evidence.append(sid, records[:2048])
    assert saved == 2048
    with pytest.raises(RiderError, match="samples_evidence_quota_reached"):
        store.evidence.append(sid, records[2048:])
    assert store.session(sid)["evidence"]["saved"] == 2048
    with pytest.raises(RiderError, match="samples_import_evidence_invalid"):
        validate_extension({"schema_version": 1, "records": records, "status": {}})
    padded = []
    for record in records[:350]:
        row = copy.deepcopy(record)
        row["quality"]["fixture_padding"] = "x" * 7000
        padded.append(row)
    with pytest.raises(RiderError, match="samples_import_evidence_invalid"):
        validate_extension({"schema_version": 1, "records": padded, "status": {}})


@pytest.mark.parametrize("wrapped", [False, True])
async def test_actual_pi_flat_sensor_quality_and_observation_wrapper(tmp_path, wrapped):
    # Shape and numeric values from an already received host recording, not a Pi read.
    sensors = {
        key: {
            "value": value,
            "unit": unit,
            "instance_id": "producer-instance",
            "observed_at": 1792507743.0646675,
            "ready": True,
            "stale": False,
            "offline": False,
            "simulated": False,
            "physical_validation": "unverified",
            "error": None,
        }
        for key, value, unit in (
            ("accel_x", 0.46894532442092896, "m/s^2"),
            ("accel_y", -6.044848442077637, "m/s^2"),
            ("accel_z", 7.58447265625, "m/s^2"),
            ("roll", 3.4740371704101562, "sdk_raw"),
            ("pitch", -38.08158493041992, "sdk_raw"),
            ("yaw", -455.730712890625, "sdk_raw"),
        )
    }
    source = {
        "sensor_quality": sensors,
        "orientation": {"yaw": -95.730712890625},
    }
    if wrapped:
        source["observation"] = {
            "instance_id": "producer-instance",
            "sensor_quality": {
                "ready": True,
                "stale": False,
                "offline": False,
                "simulated": False,
                "sensors": sensors,
            },
        }
        source["sensor_quality"] = {"yaw": {"value": -95.73}}
    registry = SamplesRegistry(tmp_path)
    try:
        sid = (await registry.start("one", "pi"))["session_id"]
        registry.observe_evidence(
            "/state",
            {"data": {"devices": {"xgo": source}}, "meta": {}},
            "pi",
            "selected",
        )
        await drain(registry)
        row = snapshot(registry, sid)["evidence"]["records"][0]
        assert row["source_instance_id"] == "producer-instance"
        assert row["source_observed_at"] == 1792507743.0646675
        assert row["values"] == {key: value["value"] for key, value in sensors.items()}
        assert row["units"]["accel_z"] == "m/s^2"
        assert row["values"]["yaw"] == -455.730712890625
        assert row["quality"]["physical_validation"] == "unverified"
        assert row["quality"]["ready"] is True
        assert row["quality"]["stale"] is False
        assert row["quality"]["sensors"] == sensors
    finally:
        await registry.close()


async def test_inflight_write_cannot_repopulate_retired_source_window(
    tmp_path, monkeypatch
):
    registry = SamplesRegistry(tmp_path)
    begun, release = threading.Event(), threading.Event()
    original = registry.store.evidence.append
    try:
        await registry.start("one", "pi")

        def delayed(*args, **kwargs):
            begun.set()
            release.wait(5)
            return original(*args, **kwargs)

        monkeypatch.setattr(registry.store.evidence, "append", delayed)
        registry.observe_evidence("/state", imu(), "pi", "selected")
        await asyncio.to_thread(begun.wait, 1)
        registry.retire_evidence("stop")
        release.set()
        await drain(registry)
        assert registry.evidence.status()["ram_records"] == 0
        assert next(iter(registry.evidence.status()["sessions"].values()))["retired"]
    finally:
        release.set()
        await registry.close()


async def test_shared_ticket_filters_evidence_owned_by_other_collection(tmp_path):
    registry = SamplesRegistry(tmp_path)
    try:
        first = (await registry.start("one", "pi"))["session_id"]
        registry.observe_evidence("/state", imu(), "pi", "selected")
        await drain(registry)
        second = (await registry.start("two", "pi"))["session_id"]
        source = frame()
        key = registry.offer(
            "pi",
            source.payload,
            source.mime,
            {
                "host_device_session": "selected",
                "source_identity": {"original": "camera-source"},
                "association": "exact-source-reference",
            },
        )
        original = registry.frames.get(key).context()
        assert original["evidence"]["record_ids"]
        for sid in (first, second):
            registry.capture(sid, "capture-" + sid, key)
        await drain(registry)
        one = registry.store.samples(first)[0]
        two = registry.store.samples(second)[0]
        one = registry.store.sample(one["sample_id"])
        two = registry.store.sample(two["sample_id"])
        assert (
            one["source_context"]["evidence"]["record_ids"]
            == original["evidence"]["record_ids"]
        )
        assert one["telemetry"]["values"]["yaw"] == 362.0
        assert two["source_context"]["evidence"]["record_ids"] == []
        assert (
            two["source_context"]["evidence"]["availability"]
            == "unavailable-for-collection"
        )
        assert two["telemetry"] is None
        assert two["source_context"]["source_identity"] == original["source_identity"]
        assert two["association"] == "exact-source-reference"
        assert registry.frames.get(key).context() == original
        exported, _ = SampleArchive(registry.store).export(second)
        manifest = SampleArchive.validate_zip(
            exported, registry.store.limits.session_bytes
        )
        assert manifest["samples"][0]["source_context"]["evidence"]["record_ids"] == []
        assert "evidence" not in manifest
    finally:
        await registry.close()


async def test_partial_collection_ownership_keeps_only_its_archived_metadata(tmp_path):
    registry = SamplesRegistry(tmp_path)
    try:
        await registry.start("one", "pi")
        registry.observe_evidence("/state", imu(), "pi", "selected")
        await drain(registry)
        second = (await registry.start("two", "pi"))["session_id"]
        registry.observe_evidence(
            "/api/control/state",
            {
                "data": {"safety": {"observed_at": 12.0, "state": "DISARMED"}},
                "meta": {},
            },
            "pi",
            "selected",
        )
        await drain(registry)
        source = frame()
        key = registry.offer(
            "pi", source.payload, source.mime, {"host_device_session": "selected"}
        )
        registry.capture(second, "capture", key)
        await drain(registry)
        sample = registry.store.sample(registry.store.samples(second)[0]["sample_id"])
        references = sample["source_context"]["evidence"]
        assert references["availability"] == "partial"
        assert references["unavailable_count"] == 1
        assert len(references["record_ids"]) == 1
        assert sample["telemetry"] is None
        assert sample["commands"]["record_id"] in references["record_ids"]
        assert sample["commands"]["executor"]["state"] == "DISARMED"
    finally:
        await registry.close()


@pytest.mark.parametrize("invalid", [float("nan"), object()])
async def test_invalid_context_during_enrichment_fails_closed(tmp_path, invalid):
    registry = SamplesRegistry(tmp_path)
    try:
        await registry.start("one", "pi")
        registry.observe_evidence("/state", imu(), "pi", "selected")
        await drain(registry)
        source = frame()
        assert (
            registry.offer(
                "pi",
                source.payload,
                source.mime,
                {
                    "host_device_session": "selected",
                    "invalid": invalid,
                },
            )
            is None
        )
        assert registry.failures == 1
        assert registry.frames.rejected == 1
    finally:
        await registry.close()


@pytest.mark.parametrize("failure", ["disk", "database"])
async def test_optional_evidence_status_failure_does_not_prevent_session_end(
    tmp_path, monkeypatch, failure
):
    import sqlite3

    registry = SamplesRegistry(tmp_path)
    try:
        sid = (await registry.start("one", "pi"))["session_id"]

        def fail_status(*args, **kwargs):
            if failure == "database":
                raise sqlite3.OperationalError("fixture evidence status failure")
            raise OSError("fixture evidence status failure")

        monkeypatch.setattr(registry.store.evidence, "status", fail_status)
        result = await registry.end(sid)
        assert result["state"] == "closed"
        assert registry.store.session(sid)["state"] == "closed"
        assert sid not in registry.active and sid not in registry.closing
        assert sid not in registry.deadlines and sid not in registry.evidence.sessions
        assert registry.lease is None
        assert registry.failures == 1
    finally:
        await registry.close()


@pytest.mark.parametrize("failure", ["disk", "database"])
async def test_optional_evidence_status_failure_does_not_skip_close_recovery(
    tmp_path, monkeypatch, failure
):
    import sqlite3

    registry = SamplesRegistry(tmp_path)
    try:
        first = (await registry.start("one", "pi"))["session_id"]
        second = (await registry.start("two", "pi"))["session_id"]

        def fail_status(*args, **kwargs):
            if failure == "database":
                raise sqlite3.OperationalError("fixture evidence status failure")
            raise OSError("fixture evidence status failure")

        monkeypatch.setattr(registry.store.evidence, "status", fail_status)
        await registry.close()
        assert registry.store.session(first)["state"] == "interrupted"
        assert registry.store.session(second)["state"] == "interrupted"
        assert registry.closed and registry.lease is None
        assert not registry.active and not registry.evidence.sessions
        assert not registry.deadlines
        assert registry.failures == 2

        replacement = SamplesRegistry(tmp_path)
        try:
            assert (await replacement.start("replacement", "pi"))[
                "state"
            ] == "collecting"
        finally:
            await replacement.close()
    finally:
        await registry.close()


@pytest.mark.parametrize("finish_remaining", ["end", "global_stop"])
async def test_ending_one_collection_preserves_other_live_evidence_associations(
    tmp_path, finish_remaining
):
    registry = SamplesRegistry(tmp_path)
    try:
        first = (await registry.start("one", "pi"))["session_id"]
        second = (await registry.start("two", "pi"))["session_id"]
        registry.observe_evidence("/state", imu(), "pi", "selected")
        await drain(registry)
        archived_ids = {
            row["record_id"]
            for row in snapshot(registry, second)["evidence"]["records"]
        }
        assert archived_ids and registry.evidence.status()["ram_records"] == 1

        await registry.end(first)
        source = frame()
        key = registry.offer(
            "pi", source.payload, source.mime, {"host_device_session": "selected"}
        )
        registry.capture(second, "capture-second", key)
        await drain(registry)
        sample = registry.store.sample(registry.store.samples(second)[0]["sample_id"])
        assert set(sample["source_context"]["evidence"]["record_ids"]) == archived_ids
        assert sample["telemetry"]["record_id"] in archived_ids

        if finish_remaining == "end":
            await registry.end(second)
        else:
            registry.retire_evidence("stop")
        assert registry.evidence.status()["ram_records"] == 0
        assert registry.evidence.status()["ram_bytes"] == 0
    finally:
        await registry.close()
