"""Local archive CLI safety and unavailable/error semantics."""

import json
import zipfile

from test_motion_evidence import manifest, record

from venom_module_rider.motion_evidence_cli import main


def test_cli_offline_manifest_and_refuses_overwrite(tmp_path, monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("offline report attempted network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    source = tmp_path / "session.json"
    output = tmp_path / "report.json"
    source.write_text(json.dumps(manifest([record()])))
    assert main([str(source), "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["counts"]["imu_records"] == 1
    original = output.read_bytes()
    assert main([str(source), "--output", str(output)]) == 1
    assert output.read_bytes() == original


def test_cli_uses_existing_zip_validator_and_legacy_is_unavailable(tmp_path):
    source = tmp_path / "session.zip"
    output = tmp_path / "report.json"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest()))
    assert main([str(source), "--output", str(output)]) == 0
    assert json.loads(output.read_text())["status"] == "unavailable"


def test_cli_corrupt_zip_or_bad_settings_writes_no_output(tmp_path):
    source = tmp_path / "session.zip"
    output = tmp_path / "report.json"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest()))
        archive.writestr("unexpected", "data")
    assert main([str(source), "--output", str(output)]) == 1
    assert not output.exists()
    source = tmp_path / "session.json"
    source.write_text(json.dumps(manifest()))
    settings = tmp_path / "settings.json"
    settings.write_text('{"source_yaw_unit_verified": true}')
    assert (
        main([str(source), "--output", str(output), "--settings", str(settings)]) == 1
    )
    assert not output.exists()


def test_cli_dangling_output_symlink_and_nonregular_input_rejected(tmp_path):
    source = tmp_path / "session.json"
    source.write_text(json.dumps(manifest()))
    output = tmp_path / "report.json"
    output.symlink_to(tmp_path / "missing")
    assert main([str(source), "--output", str(output)]) == 1
    assert not (tmp_path / "missing").exists()
    assert main([str(tmp_path), "--output", str(tmp_path / "other.json")]) == 1


def test_cli_bounds_json_read(tmp_path, monkeypatch):
    import venom_module_rider.motion_evidence_cli as cli

    monkeypatch.setattr(cli, "MAX_MANIFEST_BYTES", 8)
    source = tmp_path / "session.json"
    source.write_text(json.dumps(manifest()))
    output = tmp_path / "report.json"
    assert main([str(source), "--output", str(output)]) == 1
    assert not output.exists()
