"""Research reads the same exported sources and never activates robot services."""

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from scripts import mapping_research as research
from venom_module_rider.errors import RiderError
from venom_module_rider.samples_archive import SampleArchive
from venom_module_rider.samples_frame import FrameBuffer
from venom_module_rider.samples_store import SampleStore


def archive(tmp_path, count=3, *, texture=True):
    store = SampleStore(tmp_path / "registry")
    session = store.start("create", "recorded", {"order_source": "receipt"})
    frame_buffer = FrameBuffer()
    for index in range(count):
        if texture:
            rng = np.random.default_rng(41)
            pixels = rng.integers(0, 255, (120, 160, 3), dtype=np.uint8)
            pixels = np.roll(pixels, index * 5, axis=1)
            image = Image.fromarray(pixels)
        else:
            image = Image.new("RGB", (160, 120))
        stream = io.BytesIO()
        image.save(stream, "PNG")
        key = frame_buffer.offer(
            "recorded",
            stream.getvalue(),
            "image/png",
            {
                "run_id": "run",
                "frame_id": f"f-{index}",
                "sequence": index,
            },
        )
        store.capture(
            session["session_id"], f"capture-{index}", key, frame_buffer.get(key)
        )
    store.end(session["session_id"])
    exported, _ = SampleArchive(store).export(session["session_id"])
    return exported


def test_actual_export_feature_audit_preserves_inputs_and_does_not_claim_map(tmp_path):
    source = archive(tmp_path)
    before = source.read_bytes()
    report = research.audit(source)
    assert source.read_bytes() == before
    assert report["readiness"]["source_order_verified"]
    assert report["readiness"]["transformed_intrinsics_qualified"] is False
    assert report["readiness"]["motion_model_baseline"]["trajectory"] is None
    assert len(report["overlap"]["pairs"]) == 2
    assert all(row["overlap_candidate"] for row in report["overlap"]["pairs"])
    assert report["panorama"]["artifact"] is None and report["map"]["artifact"] is None
    assert report["map"]["ate_rmse_m"] is None
    assert report["drive_supported"] is False and report["pi_access"] is False
    assert report["pose"]["state"] == "not_requested"


def test_unverified_order_and_black_frames_remain_unknown(tmp_path):
    source = archive(tmp_path, texture=False)
    manifest, images = research.load_archive(source)
    manifest["session"]["policy"]["order_source"] = "filename-unverified"
    assert research.readiness(manifest)["source_order_verified"] is False
    matching = research.overlap(images)
    assert matching["features"] == [0, 0, 0]
    assert all(
        row["matches"] == 0 and row["inlier_fraction"] is None
        for row in matching["pairs"]
    )


def test_archive_limits_hash_and_image_validation(tmp_path, monkeypatch):
    with pytest.raises((RiderError, zipfile.BadZipFile)):
        research.load_archive(tmp_path / "absent.zip")
    source = archive(tmp_path)
    with zipfile.ZipFile(source) as source_zip:
        values = {name: source_zip.read(name) for name in source_zip.namelist()}
    corrupt = tmp_path / "corrupt.zip"
    values[next(name for name in values if name.startswith("raw/"))] = b"broken"
    with zipfile.ZipFile(corrupt, "w") as output:
        for name, value in values.items():
            output.writestr(name, value)
    with pytest.raises(RiderError):
        research.load_archive(corrupt)
    monkeypatch.setattr(research, "MAX_FRAMES", 1)
    with pytest.raises(ValueError, match="mapping_input_limit"):
        research.load_archive(source)


def test_publish_is_atomic_immutable_bounded_and_outside_git(tmp_path, monkeypatch):
    output = tmp_path / "derived" / "report.json"
    research.publish(output, {"kind": "research"})
    assert json.loads(output.read_bytes())["kind"] == "research"
    with pytest.raises(FileExistsError):
        research.publish(output, {"kind": "replacement"})
    assert not list(output.parent.glob("tmp*"))
    with pytest.raises(ValueError, match="mapping_output"):
        research.publish(Path("relative"), {})
    repo = tmp_path / "git"
    repo.mkdir()
    (repo / ".git").mkdir()
    with pytest.raises(ValueError, match="mapping_output"):
        research.publish(repo / "output.json", {})
    link = tmp_path / "linked"
    link.symlink_to(output.parent)
    with pytest.raises(ValueError, match="mapping_output"):
        research.publish(link / "other.json", {})
    monkeypatch.setattr(research, "MAX_REPORT_BYTES", 1)
    with pytest.raises(ValueError, match="mapping_report_limit"):
        research.publish(tmp_path / "large.json", {"a": "b"})


def test_optional_pose_failure_does_not_invent_trajectory(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("unqualified provider detail /private/path")

    monkeypatch.setattr(research, "pose_hypothesis", fail)
    report = research.audit(archive(tmp_path), with_pose=True)
    assert report["pose"] == {"state": "failed", "error": "mapping_pose_failed"}
    assert report["map"]["artifact"] is None


def test_joint_pose_adapter_uses_exact_retained_profile_and_unscaled_sdk_output(
    tmp_path, monkeypatch
):
    import torch

    import scripts.depth_candidates as candidates

    monkeypatch.setattr(torch, "set_num_interop_threads", lambda _: None)
    profile = SimpleNamespace(
        sdk=tmp_path,
        backend="da3-small",
        model=tmp_path,
        resolution=280,
        qualify=lambda: {"fingerprint": "known"},
    )
    catalog = SimpleNamespace(resolve=lambda name: profile)

    class Candidate:
        def __init__(self, name, path, device, resolution):
            assert (name, path, device, resolution) == (
                "da3-small",
                tmp_path,
                "cpu",
                280,
            )
            self.processor = lambda images, **kwargs: (
                torch.zeros((len(images), 3, 32, 32)),
                None,
                None,
            )

        def model(self, inputs, **kwargs):
            assert kwargs == {
                "infer_gs": False,
                "use_ray_pose": False,
                "ref_view_strategy": "first",
            }
            n = inputs.shape[1]
            ext = torch.eye(4).repeat(1, n, 1, 1)
            ext[0, 1, 0, 3] = 2
            return {"extrinsics": ext, "intrinsics": torch.eye(3).repeat(1, n, 1, 1)}

    monkeypatch.setattr(candidates, "Candidate", Candidate)
    images = [Image.new("RGB", (32, 32)) for _ in range(2)]
    result = research.pose_hypothesis(images, catalog=catalog)
    assert result["camera_centres_model_units"][1] == [-2.0, 0.0, 0.0]
    assert result["metric_scale_available"] is False and result["ate_rmse_m"] is None
    assert result["qualified_localization"] is False
    with pytest.raises(ValueError, match="mapping_pose_views"):
        research.pose_hypothesis(images[:1], catalog=catalog)


def test_cli_process_no_network_no_source_writes(tmp_path):
    source = archive(tmp_path, texture=False)
    output = tmp_path / "derived" / "cli.json"
    before = source.read_bytes()
    code = (
        "import socket; from scripts.mapping_research import main; "
        f"main(['--archive',{str(source)!r},'--output',{str(output)!r}]); "
        "s=socket.socket(); "
        "\ntry: s.connect(('127.0.0.1',9))"
        "\nexcept RuntimeError as exc: assert str(exc)=='analysis_network_denied'"
        "\nelse: raise AssertionError('network allowed')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text())["pi_access"] is False
    assert source.read_bytes() == before


def test_reject_changed_source_and_disk_full(tmp_path, monkeypatch):
    source = archive(tmp_path)
    from scripts import depth_candidates

    original = depth_candidates.file_hash
    calls = [0]

    def changed(path):
        calls[0] += 1
        return original(path) if calls[0] == 1 else "changed"

    monkeypatch.setattr(depth_candidates, "file_hash", changed)
    with pytest.raises(ValueError, match="mapping_input_changed"):
        research.audit(source)
    import shutil

    monkeypatch.setattr(shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(ValueError, match="mapping_disk_full"):
        research.publish(tmp_path / "full.json", {})


def test_cli_limits_are_scoped_and_report_creation_is_explicit(tmp_path, monkeypatch):
    source = archive(tmp_path, texture=False)
    output = tmp_path / "derived" / "main.json"
    calls = []
    monkeypatch.setattr(
        research.os, "nice", lambda value: calls.append(("nice", value))
    )
    monkeypatch.setattr(
        research.os,
        "sched_setaffinity",
        lambda pid, cpus: calls.append(("affinity", len(cpus))),
    )
    monkeypatch.setattr(
        research.resource, "setrlimit", lambda *args: calls.append(("limit", args))
    )
    monkeypatch.setattr(
        research, "deny_network", lambda: calls.append(("offline", True))
    )
    assert research.main(["--archive", str(source), "--output", str(output)]) == 0
    assert ("offline", True) in calls and ("nice", 10) in calls
    assert any(row[0] == "affinity" and row[1] <= 2 for row in calls)
    assert json.loads(output.read_text())["pose"]["state"] == "not_requested"


def test_empty_input_and_oversized_source_are_rejected(tmp_path, monkeypatch):
    source = archive(tmp_path)
    import hashlib

    from venom_module_rider.samples_archive import canonical

    with zipfile.ZipFile(source) as archive_zip:
        manifest = json.loads(archive_zip.read("manifest.json"))
    manifest["samples"] = []
    check = {
        key: value
        for key, value in manifest.items()
        if key not in {"dataset_revision", "exported_at"}
    }
    manifest["dataset_revision"] = hashlib.sha256(canonical(check)).hexdigest()
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as archive_zip:
        archive_zip.writestr("manifest.json", canonical(manifest))
    with pytest.raises(ValueError, match="mapping_input_limit"):
        research.load_archive(empty)
    monkeypatch.setattr(research, "MAX_INPUT_BYTES", 1)
    monkeypatch.setattr(research, "MAX_REPORT_BYTES", 1)
    with pytest.raises(ValueError, match="mapping_input_limit"):
        research.audit(source)
