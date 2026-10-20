"""Orientation metadata is an allowlisted source declaration, never invented."""

from copy import deepcopy

from venom_module_rider.motion_timeline import build_timeline
from venom_module_rider.sample_evidence import observations
from venom_module_rider.samples_archive import SampleArchive
from venom_module_rider.samples_import import SampleImporter
from venom_module_rider.samples_registry import SamplesRegistry


def payload():
    return {
        "data": {
            "devices": {
                "xgo": {
                    "instance_id": "boot",
                    "sensor_quality": {
                        "ready": True,
                        "stale": False,
                        "offline": False,
                        "sensors": {
                            axis: {
                                "value": 0.0,
                                "unit": "deg",
                                "observed_at": 100.0,
                                "instance_id": "boot",
                                "ready": True,
                                "stale": False,
                                "offline": False,
                            }
                            for axis in ("roll", "pitch", "yaw")
                        },
                    },
                }
            }
        }
    }


def convert(value):
    return observations("/state", value, "demo", "session", 10.0, 1000.0)[0]


def test_orientation_metadata_is_not_invented_and_unknown_fields_are_removed():
    original = payload()
    plain = convert(original)
    assert "orientation" not in plain["quality"]
    meta = {
        "convention": "rider-body-zyx-v1",
        "revision": 1,
        "verified": True,
        "qualification_ref": "known-poses-v1",
        "instance_id": "boot",
        "unit": "deg",
    }
    original["data"]["devices"]["xgo"]["sensor_quality"]["orientation"] = {
        **meta,
        "secret": "do-not-save",
    }
    row = convert(original)
    assert row["quality"]["orientation"] == meta
    assert row["record_id"] != plain["record_id"]
    assert "do-not-save" not in str(row)
    changed = deepcopy(original)
    changed["data"]["devices"]["xgo"]["sensor_quality"]["orientation"]["revision"] = 2
    assert convert(changed)["record_id"] != row["record_id"]


def test_invalid_large_metadata_does_not_discard_raw_angles():
    value = payload()
    value["data"]["devices"]["xgo"]["sensor_quality"]["orientation"] = {
        "convention": ["bad"] * 10000,
        "qualification_ref": "x" * 10000,
        "verified": "yes",
        "revision": True,
    }
    row = convert(value)
    assert row["values"]["roll"] == 0.0
    assert row["quality"]["orientation"] == {}


async def test_qualified_metadata_survives_archive_import_and_timeline(tmp_path):
    source, target = (
        SamplesRegistry(tmp_path / "source"),
        SamplesRegistry(tmp_path / "target"),
    )
    try:
        session = await source.start("known-poses", "demo", {})
        sid = session["session_id"]
        value = payload()
        meta = {
            "convention": "rider-body-zyx-v1",
            "revision": 1,
            "verified": True,
            "qualification_ref": "known-poses-v1",
            "instance_id": "boot",
            "unit": "deg",
        }
        value["data"]["devices"]["xgo"]["sensor_quality"]["orientation"] = meta
        await source.read(source.store.evidence.append, sid, [convert(value)])
        await source.end(sid)
        archive, _ = await source.read(SampleArchive(source.store).export, sid)
        result = await SampleImporter(target).run(archive, "known-poses")
        restored_id = result["session_id"]
        manifest, revision = await target.read(
            SampleArchive(target.store).snapshot, restored_id
        )
        manifest["dataset_revision"] = revision
        point = build_timeline(manifest)["streams"][0]["points"][0]
        assert point["quality"]["orientation"] == meta
        assert point["units"]["roll"] == "deg"
        assert point["source_time"] == 100.0
    finally:
        await source.close()
        await target.close()
