"""Export/import tests verify hashes and provenance, not model accuracy."""

import json
import stat
import zipfile

import pytest
from test_samples_store import frame, save, setup

from venom_module_rider.errors import RiderError
from venom_module_rider.samples_archive import SampleArchive
from venom_module_rider.samples_cli import main
from venom_module_rider.samples_import import SampleImporter
from venom_module_rider.samples_registry import SamplesRegistry


def dataset(tmp_path):
    store, sid = setup(tmp_path)
    source = frame(context={"frame_id": "camera-1", "sequence": 5})
    one = save(store, sid, source)
    store.label(
        one["sample_id"],
        {"class_name": "fixture", "expected_state": "clear", "review": "approved"},
        expected_revision=0,
    )
    store.label(
        one["sample_id"],
        {"expected_state": "unknown"},
        expected_revision=1,
        author_kind="auto",
    )
    save(store, sid, frame("white"), "second")
    store.end(sid)
    return store, sid


async def test_export_and_reimport_preserve_exact_sources_and_review_history(tmp_path):
    store, sid = dataset(tmp_path)
    archive = SampleArchive(store)
    path, revision = archive.export(sid)
    manifest = archive.validate_zip(path, store.limits.session_bytes)
    assert manifest["dataset_revision"] == revision
    assert manifest["samples"][0]["labels"][0]["review"] == "approved"
    assert manifest["samples"][0]["labels"][1]["author"]["kind"] == "auto"
    target = SamplesRegistry(tmp_path / "target")
    try:
        imported = await SampleImporter(target).run(path, "import-1")
        assert imported["state"] == "closed"
        rows = target.store.samples(imported["session_id"])
        assert [row["raw"]["sha256"] for row in rows] == [
            row["raw"]["sha256"] for row in store.samples(sid)
        ]
        assert (
            target.store.original(rows[0]["sample_id"])[0]
            == store.original(manifest["samples"][0]["sample_id"])[0]
        )
        item = target.store.sample(rows[0]["sample_id"])
        assert item["historical"] is True
        assert item["labels"][0]["review"] == "approved"
        assert item["labels"][0]["import_origin"]["label"]["created_at"] == 1000.0
        assert item["labels"][1]["review"] == "pending"
        assert item["frame_id"] == "camera-1"
        assert item["source_timestamp"] is None
        assert (await SampleImporter(target).run(path, "import-1"))[
            "session_id"
        ] == imported["session_id"]
        assert len(target.store.sessions()) == 1
    finally:
        await target.close()
        path.unlink(missing_ok=True)


async def test_plain_history_has_unknown_exposure_order_and_no_invented_labels(
    tmp_path,
):
    source = tmp_path / "history"
    source.mkdir()
    (source / "box-1.png").write_bytes(frame().payload)
    (source / "floor-2.png").write_bytes(frame("white").payload)
    target = SamplesRegistry(tmp_path / "archive")
    try:
        session = await SampleImporter(target).run(source, "history")
        assert session["policy"]["order_source"] == "filename-unverified"
        for row in target.store.samples(session["session_id"]):
            sample = target.store.sample(row["sample_id"])
            assert sample["sequence"] is None and sample["source_timestamp"] is None
            assert sample["telemetry"] is None and sample["labels"] == []
            assert sample["source_context"]["import_origin"]["original_file_name"]
    finally:
        await target.close()


@pytest.mark.parametrize(
    "name", ["../outside.jpg", "/absolute.jpg", "a\\b.jpg", "", None]
)
def test_unsafe_local_members_are_rejected(tmp_path, name):
    (tmp_path / "good.jpg").write_bytes(frame().payload)
    with pytest.raises(RiderError, match="samples_import_path"):
        SampleArchive.safe_file(tmp_path, name)


def test_symlinked_files_and_roots_are_not_read_as_history(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "one.png").write_bytes(frame().payload)
    linked = tmp_path / "link"
    linked.symlink_to(real, target_is_directory=True)
    store, _sid = setup(tmp_path)
    registry = SamplesRegistry(store.root)
    importer = SampleImporter(registry)
    with pytest.raises(RiderError, match="samples_import_path"):
        importer.inspect(linked)
    source = tmp_path / "folder"
    source.mkdir()
    (source / "one.png").symlink_to(real / "one.png")
    with pytest.raises(RiderError, match="samples_import_path"):
        importer.inspect(source)
    # No jobs were submitted; shut down only this instance's idle executor.
    registry.io.shutdown(wait=True)


@pytest.mark.parametrize(
    "kind", ["zip-slip", "symlink", "extra", "duplicate", "hash", "malformed"]
)
def test_corrupt_or_unsafe_archives_fail_before_import_session(tmp_path, kind):
    store, sid = dataset(tmp_path)
    original, _revision = SampleArchive(store).export(sid)
    with zipfile.ZipFile(original) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    raw_name = next(name for name in entries if name.startswith("raw/"))
    target = tmp_path / (kind + ".zip")
    with zipfile.ZipFile(target, "w") as archive:
        for name, payload in entries.items():
            if kind == "hash" and name == raw_name:
                payload = b"tampered"
            if kind == "malformed" and name == "manifest.json":
                payload = json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "rider-sample-session",
                        "samples": [None],
                    }
                ).encode()
            archive.writestr(name, payload)
        if kind == "zip-slip":
            archive.writestr("../outside", b"bad")
        elif kind == "symlink":
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "/tmp/target")
        elif kind == "extra":
            archive.writestr("unexpected.bin", b"bad")
        elif kind == "duplicate":
            archive.writestr(raw_name, entries[raw_name])
    with pytest.raises(RiderError):
        SampleArchive.validate_zip(target, store.limits.session_bytes)
    original.unlink()


def test_export_slots_are_bounded_and_raws_are_not_deleted(tmp_path):
    store, sid = dataset(tmp_path)
    archive = SampleArchive(store)
    first, _ = archive.export(sid)
    second, _ = archive.export(sid)
    with pytest.raises(RiderError, match="samples_export_busy"):
        archive.export(sid)
    first.unlink()
    third, _ = archive.export(sid)
    second.unlink()
    third.unlink()
    assert len(store.samples(sid)) == 2


def test_cli_import_browse_label_export_and_no_overwrite(tmp_path, capsys):
    source = tmp_path / "images"
    source.mkdir()
    (source / "scene.png").write_bytes(frame().payload)
    root = tmp_path / "cli-root"
    assert (
        main(["--root", str(root), "import", str(source), "--request-id", "import-cli"])
        == 0
    )
    sid = json.loads(capsys.readouterr().out)["data"]["session_id"]
    assert main(["--root", str(root), "session", sid]) == 0
    row = json.loads(capsys.readouterr().out)["data"]["samples"][0]
    assert (
        main(
            [
                "--root",
                str(root),
                "label",
                row["sample_id"],
                "--expected-revision",
                "0",
                "--expected-state",
                "unknown",
                "--review",
                "pending",
            ]
        )
        == 0
    )
    capsys.readouterr()
    output = tmp_path / "export.zip"
    args = ["--root", str(root), "export", sid, str(output)]
    assert main(args) == 0
    capsys.readouterr()
    before = output.read_bytes()
    assert main(args) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "samples_export_exists"
    assert output.read_bytes() == before
