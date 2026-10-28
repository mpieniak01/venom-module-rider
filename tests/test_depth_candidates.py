"""Offline research inputs/results cannot silently grant motion or hide errors."""

import json

import numpy as np
import pytest
from PIL import Image

from scripts.compare_depth_models import load_dataset, summarize
from scripts.depth_candidates import file_hash, resize_depth, validate_depth


@pytest.mark.parametrize(
    "values",
    [
        np.zeros((32, 32)),
        np.full((32, 32), np.nan),
        np.full((32, 32), np.inf),
        -np.ones((32, 32)),
        np.ones((31, 64)),
        np.ones((1, 32, 32)),
    ],
)
def test_invalid_model_outputs_are_rejected(values):
    with pytest.raises(ValueError):
        validate_depth(values)


def test_resize_preserves_entire_view_and_positive_orientation():
    y, x = np.mgrid[:64, :96]
    depth = (1 + x / 96 + y / 64).astype(np.float32)
    result = resize_depth(depth, (320, 240))
    assert result.shape == (240, 320)
    assert result[0, 0] == pytest.approx(depth[0, 0])
    assert result[-1, -1] == pytest.approx(depth[-1, -1])
    assert result[10, 10] < result[220, 300]


def manifest_row(tmp_path, name="one.jpg", expected="clear"):
    image = tmp_path / name
    Image.new("RGB", (64, 64), (150, 120, 80)).save(image)
    return {"file": name, "expected": expected, "sha256": file_hash(image)}


def write_manifest(tmp_path, rows):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(rows))
    return path


def test_explicit_labels_do_not_use_filename_heuristics(tmp_path):
    row = manifest_row(tmp_path, name="clear-floor.jpg", expected="unlabelled")
    rows = load_dataset(write_manifest(tmp_path, [row]))
    assert rows[0]["expected"] == "unlabelled"


@pytest.mark.parametrize("change", ["hash", "duplicate", "escape", "label", "empty"])
def test_manifest_rejects_ambiguous_or_changed_input(tmp_path, change):
    row = manifest_row(tmp_path)
    rows = [row]
    if change == "hash":
        row["sha256"] = "0" * 64
    elif change == "duplicate":
        rows.append(dict(row))
    elif change == "escape":
        row["file"] = "../outside.jpg"
    elif change == "label":
        row["expected"] = "probably_clear"
    else:
        rows = []
    with pytest.raises(ValueError):
        load_dataset(write_manifest(tmp_path, rows))


def test_same_file_under_symlink_alias_is_duplicate(tmp_path):
    row = manifest_row(tmp_path)
    (tmp_path / "alias.jpg").symlink_to(tmp_path / "one.jpg")
    alias = {**row, "file": "alias.jpg"}
    with pytest.raises(ValueError):
        load_dataset(write_manifest(tmp_path, [row, alias]))


def test_confusion_does_not_hide_false_clear_or_transition_unknown():
    result = summarize(
        [
            {
                "expected": "obstacle",
                "states": ["clear", "obstacle"],
                "latency_ms": [100, 300],
            },
            {
                "expected": "clear",
                "states": ["clear", "unknown"],
                "latency_ms": [150, 200],
            },
            {
                "expected": "unknown",
                "states": ["clear", "unknown"],
                "latency_ms": [10, 12],
            },
            {
                "expected": "unlabelled",
                "states": ["clear", "unknown"],
                "latency_ms": [20, 30],
            },
        ]
    )
    assert result["false_clear_samples"] == 2
    assert result["floor_non_clear_samples"] == 1
    assert result["over_300ms"] == 1
    assert result["unstable_frames"] == 4
    assert result["p95_ms"] == 300
    assert result["confusion"]["unlabelled->clear"] == 1


def test_safetensors_loader_restores_shared_weights(tmp_path):
    torch = pytest.importorskip("torch")
    safetensors = pytest.importorskip("safetensors.torch")

    # Real checkpoint format: repeated LayerNorm objects have one stored copy.
    class Shared(torch.nn.Module):
        def __init__(self):
            super().__init__()
            shared = torch.nn.LayerNorm(4)
            self.layers = torch.nn.ModuleList([shared, shared])

    wrapper = torch.nn.Module()
    wrapper.add_module("model", Shared())
    path = str(tmp_path / "shared.safetensors")
    safetensors.save_model(wrapper, path)
    restored = torch.nn.Module()
    restored.add_module("model", Shared())
    safetensors.load_model(restored, path, strict=True)
    assert torch.equal(restored.model.layers[0].weight, wrapper.model.layers[0].weight)
    assert restored.model.layers[0] is restored.model.layers[1]


def test_failed_inference_writes_failure_not_success_report(tmp_path, monkeypatch):
    import sys

    from scripts import compare_depth_models as compare

    output = tmp_path / "failed.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compare",
            "--backend",
            "da3-small",
            "--model",
            str(tmp_path),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--output",
            str(output),
        ],
    )

    def fail(_args):
        raise RuntimeError("inference_failed")

    monkeypatch.setattr(compare, "benchmark", fail)
    with pytest.raises(RuntimeError, match="inference_failed"):
        compare.main()
    report = json.loads(output.read_text())
    assert report["status"] == "failed"
    assert report["drive_supported"] is False
    assert "summary" not in report


def test_inference_latency_excludes_quality_rejections():
    result = summarize(
        [
            {
                "expected": "clear",
                "states": ["clear"],
                "latency_ms": [120],
                "assessments": [{"reason": "depth_plane_candidate"}],
            },
            {
                "expected": "unknown",
                "states": ["unknown"],
                "latency_ms": [1],
                "assessments": [{"reason": "vision_image_quality"}],
            },
        ]
    )
    assert result["samples"] == 2
    assert result["inferred_samples"] == 1
    assert result["inferred_p95_ms"] == 120


def test_dark_quality_gate_skips_model_without_motion(tmp_path, monkeypatch):
    from types import SimpleNamespace

    pytest.importorskip("torch")
    from scripts import compare_depth_models as compare

    row = manifest_row(tmp_path, name="dark.jpg", expected="unknown")
    Image.new("RGB", (64, 64), (0, 0, 0)).save(tmp_path / "dark.jpg")
    row["sha256"] = file_hash(tmp_path / "dark.jpg")
    manifest = write_manifest(tmp_path, [row])

    class NeverInfer:
        def __init__(self, *args):
            self.metadata = {"backend": "da3-small"}

        def infer(self, image):
            raise AssertionError("dark frames must not reach model")

    monkeypatch.setattr(compare, "Candidate", NeverInfer)
    args = SimpleNamespace(
        threads=1,
        device="cpu",
        manifest=manifest,
        backend="da3-small",
        model=tmp_path,
        resolution=280,
        warmup=1,
        repeats=2,
        quality_mode="production",
    )
    report = compare.benchmark(args)
    assert report["status"] == "completed"
    assert report["drive_supported"] is False
    assert report["warmup_samples"] == 0
    assert report["warmup_kind"] == "quality_gate_only"
    assert report["summary"]["inferred_samples"] == 0
    assert report["rows"][0]["states"] == ["unknown", "unknown"]


def test_low_quality_clear_is_reported_without_label_accuracy_claim():
    result = summarize(
        [
            {
                "expected": "unlabelled",
                "states": ["clear"],
                "latency_ms": [90],
                "assessments": [
                    {
                        "state": "clear",
                        "reason": "depth_plane_candidate",
                        "image_quality_passed": False,
                    }
                ],
            }
        ]
    )
    assert result["labelled_frames"] == 0
    assert result["low_quality_clear_samples"] == 1


def test_probe_mode_is_explicit_and_still_never_grants_motion(tmp_path, monkeypatch):
    from types import SimpleNamespace

    pytest.importorskip("torch")
    from scripts import compare_depth_models as compare

    row = manifest_row(tmp_path, name="dark.jpg", expected="unlabelled")
    Image.new("RGB", (64, 64), (0, 0, 0)).save(tmp_path / "dark.jpg")
    row["sha256"] = file_hash(tmp_path / "dark.jpg")

    class Probe:
        calls = 0

        def __init__(self, *args):
            self.metadata = {"backend": "da3-small"}

        def infer(self, image):
            self.calls += 1
            return np.ones((64, 64), dtype=np.float32)

    monkeypatch.setattr(compare, "Candidate", Probe)
    args = SimpleNamespace(
        threads=1,
        device="cpu",
        manifest=write_manifest(tmp_path, [row]),
        backend="da3-small",
        model=tmp_path,
        resolution=280,
        warmup=1,
        repeats=2,
        quality_mode="model_probe",
    )
    report = compare.benchmark(args)
    assert report["quality_mode"] == "model_probe"
    assert report["drive_supported"] is False
    assert report["summary"]["inferred_samples"] == 2
    assert all(not a["image_quality_passed"] for a in report["rows"][0]["assessments"])


def test_probe_cli_does_not_replace_production_default(tmp_path, monkeypatch):
    import sys

    from scripts import compare_depth_models as compare

    received = []

    def fake_benchmark(args):
        received.append(args.quality_mode)
        return {"summary": {}, "drive_supported": False}

    monkeypatch.setattr(compare, "benchmark", fake_benchmark)
    output = tmp_path / "default.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compare",
            "--backend",
            "da2",
            "--model",
            str(tmp_path),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--output",
            str(output),
        ],
    )
    compare.main()
    assert received == ["production"]


def test_sdk_rejects_untracked_source(tmp_path, monkeypatch):
    import subprocess

    from scripts import depth_candidates as candidates

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), *args], text=True
        ).strip()

    git("init", "-q")
    source = tmp_path / "src"
    source.mkdir()
    (source / "original.py").write_text("pass")
    git("add", "src")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    monkeypatch.setattr(candidates, "SDK_REVISION", git("rev-parse", "HEAD"))
    candidates.verify_sdk(tmp_path)
    (source / "untracked.py").write_text("pass")
    with pytest.raises(ValueError, match="sdk_source_modified"):
        candidates.verify_sdk(tmp_path)
    (source / "untracked.py").unlink()
    (source / "original.py").write_text("changed")
    with pytest.raises(ValueError, match="sdk_source_modified"):
        candidates.verify_sdk(tmp_path)


def test_warmup_uses_valid_image_after_initial_dark_frame(tmp_path, monkeypatch):
    from types import SimpleNamespace

    pytest.importorskip("torch")
    from scripts import compare_depth_models as compare

    dark = manifest_row(tmp_path, "dark.jpg", "unlabelled")
    Image.new("RGB", (64, 64), (0, 0, 0)).save(tmp_path / "dark.jpg")
    dark["sha256"] = file_hash(tmp_path / "dark.jpg")
    bright = manifest_row(tmp_path, "bright.jpg", "unlabelled")
    pixels = np.tile(np.arange(64, dtype=np.uint8) * 3, (64, 1))
    Image.fromarray(pixels).convert("RGB").save(tmp_path / "bright.jpg")
    bright["sha256"] = file_hash(tmp_path / "bright.jpg")
    calls = []

    class Probe:
        def __init__(self, *args):
            self.metadata = {}

        def infer(self, image):
            calls.append(np.asarray(image).mean())
            return np.ones((64, 64), dtype=np.float32)

    monkeypatch.setattr(compare, "Candidate", Probe)
    args = SimpleNamespace(
        threads=1,
        device="cpu",
        manifest=write_manifest(tmp_path, [dark, bright]),
        backend="da3-small",
        model=tmp_path,
        resolution=280,
        warmup=2,
        repeats=1,
        quality_mode="production",
    )
    report = compare.benchmark(args)
    assert len(calls) == 3
    assert report["warmup_samples"] == 2
    assert report["warmup_kind"] == "model_inference"
    assert all(value > 0 for value in calls)


def test_retained_profile_is_explicit_and_keeps_legacy_defaults():
    from argparse import Namespace

    from scripts.compare_depth_models import apply_profile

    selected = apply_profile(
        Namespace(profile="da3-small-280", backend=None, model=None, resolution=None)
    )
    assert selected.backend == "da3-small"
    assert selected.resolution == 280
    assert selected.model.name == "small"
    legacy = apply_profile(
        Namespace(profile=None, backend="da2", model="unchanged", resolution=None)
    )
    assert legacy.model == "unchanged"
    assert legacy.resolution == 504
    with pytest.raises(ValueError, match="profile_conflicts"):
        apply_profile(
            Namespace(
                profile="da3-small-280", backend="da2", model=None, resolution=None
            )
        )
    with pytest.raises(ValueError, match="backend_and_model_required"):
        apply_profile(
            Namespace(profile=None, backend=None, model=None, resolution=None)
        )


def test_depth_report_does_not_follow_dangling_output_symlink(tmp_path, monkeypatch):
    from scripts import compare_depth_models as compare

    target = tmp_path / "private"
    output = tmp_path / "report.json"
    output.symlink_to(target)
    monkeypatch.setattr(
        "sys.argv",
        [
            "compare_depth_models",
            "--backend",
            "da2",
            "--model",
            str(tmp_path / "model"),
            "--manifest",
            str(tmp_path / "manifest"),
            "--output",
            str(output),
        ],
    )
    with pytest.raises(FileExistsError):
        compare.main()
    assert not target.exists()


@pytest.mark.parametrize("replace_output", [False, True])
def test_interrupted_benchmark_removes_only_its_own_report(
    tmp_path, monkeypatch, replace_output
):
    from scripts import compare_depth_models as compare

    output = tmp_path / "report.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "compare",
            "--backend",
            "da2",
            "--model",
            str(tmp_path / "model"),
            "--manifest",
            str(tmp_path / "manifest"),
            "--output",
            str(output),
        ],
    )

    def interrupt(_args):
        if replace_output:
            output.unlink()
            output.write_text("replacement")
        raise KeyboardInterrupt

    monkeypatch.setattr(compare, "benchmark", interrupt)
    with pytest.raises(KeyboardInterrupt):
        compare.main()
    if replace_output:
        assert output.read_text() == "replacement"
    else:
        assert not output.exists()
