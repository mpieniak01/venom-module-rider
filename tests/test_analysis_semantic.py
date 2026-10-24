"""Semantic research shares the archived-frame worker and host catalog."""

import base64
import hashlib
import io
import json
import sys
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError
from test_analysis_profiles import catalog
from test_analysis_worker import job

from venom_module_rider import analysis_worker, perception_runtime
from venom_module_rider.analysis_contract import AnalysisSpec
from venom_module_rider.analysis_semantic import FrozenCatalog, SemanticCandidate
from venom_module_rider.api.analysis_routes import compare_reports
from venom_module_rider.errors import RiderError
from venom_module_rider.perception_catalog import (
    FAST,
    SEGFORMER,
    SOURCES,
    YOLO,
    artifact_fingerprint,
)


def assets(configured, monkeypatch, model_id=SEGFORMER):
    live = configured.live()
    source = live.path(model_id)
    source.mkdir(parents=True)
    files = (
        {
            "model.safetensors": b"weights",
            "config.json": b"{}",
            "preprocessor_config.json": b"{}",
        }
        if model_id == SEGFORMER
        else {"source.pt": b"weights", "model.onnx": b"graph"}
    )
    monkeypatch.setitem(
        SOURCES[model_id], "sha256", hashlib.sha256(b"weights").hexdigest()
    )
    hashes = {}
    for name, content in files.items():
        (source / name).write_bytes(content)
        hashes[name] = hashlib.sha256(content).hexdigest()
    manifest = {
        "model_id": model_id,
        "revision": SOURCES[model_id]["revision"],
        "files": hashes,
    }
    if model_id == YOLO:
        manifest.update(exporter="8.4.184", parity=True)
    (source / "manifest.json").write_text(json.dumps(manifest))
    return live, source, live.save_receipt(model_id)


@pytest.mark.parametrize("model_id", [SEGFORMER, YOLO])
def test_semantic_profile_reuses_live_files_and_receipt_without_paths(
    tmp_path, monkeypatch, model_id
):
    configured, _ = catalog(tmp_path, monkeypatch)
    live, source, artifact = assets(configured, monkeypatch, model_id)
    profile = configured.resolve(model_id)
    assert set(configured.profiles()) == {"da2-default", "da3-small-280"}
    public = profile.describe()
    assert public["task"] == "semantic" and public["windows"] == [1]
    assert public["installed"] and not public["loaded"]
    assert not public["drive_supported"] and public["device"] == "cuda"
    assert str(tmp_path) not in json.dumps(public)
    qualified = profile.qualify()
    assert qualified["model_sha256"] == artifact["sha256"]
    assert qualified["files"] == artifact["files"]
    assert qualified["revision"] == SOURCES[model_id]["revision"]
    assert qualified["content_verified"]
    assert str(tmp_path) not in json.dumps(qualified)
    assert len(qualified["fingerprint"]) == 64
    live.receipt_path(model_id).unlink()
    assert not profile.describe()["installed"]
    with pytest.raises(RiderError, match="model_verification_required"):
        profile.qualify()
    live.save_receipt(model_id)
    (source / next(iter(artifact["files"]))).write_bytes(b"tampered")
    with pytest.raises(RiderError, match="model_hash_mismatch"):
        profile.qualify()


def test_fast_scnn_remains_unavailable_and_runtime_missing_is_explicit(
    tmp_path, monkeypatch
):
    configured, _ = catalog(tmp_path, monkeypatch)
    fast = configured.resolve(FAST)
    assert fast.describe()["error"] == "model_needs_review"
    with pytest.raises(RiderError, match="model_needs_review"):
        fast.qualify()
    assets(configured, monkeypatch)
    unavailable = replace(configured.resolve(SEGFORMER), python=tmp_path / "no-python")
    assert unavailable.describe()["error"] == "analysis_runtime_unavailable"


@pytest.mark.parametrize("model_id", [SEGFORMER, YOLO])
def test_semantic_defaults_are_observational_without_accepting_depth_geometry(model_id):
    spec = AnalysisSpec(profile=model_id)
    assert spec.perception.engine == "segformer"
    assert spec.window == 1 and spec.geometry.mode == "image"
    for patch in (
        {"window": 3},
        {"geometry": {"mode": "ground"}},
        {"perception": {"engine": "depth_plane"}},
        {
            "perception": {
                "engine": "segformer",
                "model_id": FAST,
                "artifact_sha256": "a" * 64,
            }
        },
        {"perception": {"engine": "segformer", "model_id": model_id}},
    ):
        with pytest.raises(ValidationError):
            AnalysisSpec.model_validate({"profile": model_id, **patch})
    explicit = AnalysisSpec.model_validate(
        {
            "profile": model_id,
            "perception": {
                "engine": "segformer",
                "model_id": model_id,
                "artifact_sha256": "a" * 64,
            },
        }
    )
    assert explicit.perception.model_id == model_id


def semantic_job(tmp_path, monkeypatch, *, model_id=SEGFORMER, dark=False):
    source, store = job(tmp_path, monkeypatch, dark=dark)
    model = tmp_path / "semantic"
    model.mkdir()
    (model / "model.onnx").write_bytes(b"graph")
    files = {"model.onnx": hashlib.sha256(b"graph").hexdigest()}
    source["profile"] = {"backend": "semantic", "model": str(model), "resolution": 0}
    source["context"]["spec"] = AnalysisSpec(profile=model_id).model_dump(mode="json")
    source["context"]["profile"] = {
        "id": model_id,
        "task": "semantic",
        "backend": "segformer_onnx" if model_id == SEGFORMER else "yolo_semantic_onnx",
        "files": files,
        "model_sha256": artifact_fingerprint(files),
    }
    return source, store


def execute(source, monkeypatch):
    messages, constructed, thread_counts = [], [], []
    states = iter(["clear", "obstacle", "unknown"])
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "green").save(output, "JPEG")
    preview = output.getvalue()

    class Detector:
        def detect(self, payload, corridor):
            assert self.threads == source["context"]["spec"]["threads"]
            return {
                "state": next(states),
                "reason": "fixture-semantic",
                "floor_fraction": 0.7,
                "confidence_fraction": 0.99,
                "_preview": preview,
            }

    def build(catalog, model_id, artifact):
        assert catalog.path(model_id) == __import__("pathlib").Path(
            source["profile"]["model"]
        )
        constructed.append((model_id, artifact))
        return Detector()

    monkeypatch.setattr(perception_runtime, "build_detector", build)
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(is_initialized=lambda: False),
            set_num_threads=thread_counts.append,
            set_num_interop_threads=lambda _value: None,
        ),
    )

    def emit(_output, message):
        messages.append(deepcopy(message))
        return {"timings_ms": {"persistence": 0.5, "local_ipc_and_queue": 0.2}}

    monkeypatch.setattr(analysis_worker, "emit", emit)
    analysis_worker.run(source, None)
    return messages, messages[-1]["report"], constructed, thread_counts


@pytest.mark.parametrize("model_id", [SEGFORMER, YOLO])
def test_real_archive_overlay_and_semantic_metrics_share_depth_report_schema(
    tmp_path, monkeypatch, model_id
):
    source, store = semantic_job(tmp_path, monkeypatch, model_id=model_id)
    originals = {p.name: p.read_bytes() for p in store.blobs.glob("*")}
    messages, report, built, threads = execute(source, monkeypatch)
    assert len(built) == 1 and threads == [2]
    assert report["input_digest"] == source["context"]["input_digest"]
    assert report["summary"]["frames"] == 3
    assert report["summary"]["temporal_pairs"] == 2
    assert report["summary"]["false_go"] is None
    assert report["device"] == "cuda" and report["peak_vram_mib"] == 0
    assert report["model_load_ms"] is None
    assert report["model"]["load_timing"] == "included_in_first_perception_call"
    assert [row["state"] for row in report["rows"]] == ["clear", "obstacle", "unknown"]
    for message in messages:
        if message["type"] != "frame":
            continue
        row = message["row"]
        assert row["drive_supported"] is False
        assert row["actual_window"] == 1 and row["requested_window"] == 1
        assert row["model_id"] == model_id
        assert row["artifact_sha256"] == source["context"]["profile"]["model_sha256"]
        assert "perception_total" in row["timings_ms"]
        assert "geometry" not in row["timings_ms"]
        assert "_preview" not in row
        assert len(message["artifacts"]) == 1
        assert message["artifacts"][0]["name"].endswith("-overlay.jpg")
        with Image.open(
            io.BytesIO(base64.b64decode(message["artifacts"][0]["data"]))
        ) as image:
            assert image.size == (64, 48)
    assert {p.name: p.read_bytes() for p in store.blobs.glob("*")} == originals
    other = {
        **report,
        "run_id": "other",
        "summary": {**report["summary"], "false_go_rate": None},
    }
    assert compare_reports(report, other)["aligned_inputs_and_labels"]


def test_semantic_quality_rejection_never_loads_model(tmp_path, monkeypatch):
    source, _store = semantic_job(tmp_path, monkeypatch, dark=True)
    _messages, report, built, threads = execute(source, monkeypatch)
    assert not built and not threads
    assert report["model"] is None
    assert report["summary"]["rejected_inputs"] == 3


@pytest.mark.parametrize(
    "patch",
    [
        {"window": 3},
        {"geometry": {"mode": "ground"}},
        {"perception": {"engine": "depth_plane"}},
        {"perception": {"engine": "segformer", "model_id": YOLO}},
        {"perception": {"engine": "segformer", "artifact_sha256": "f" * 64}},
    ],
)
def test_semantic_worker_rechecks_frozen_configuration_before_inference(
    tmp_path, monkeypatch, patch
):
    source, _store = semantic_job(tmp_path, monkeypatch)
    source["context"]["spec"].update(patch)
    with pytest.raises(ValueError, match="analysis_"):
        execute(source, monkeypatch)


def test_semantic_worker_rechecks_exact_frozen_artifact_bytes(tmp_path, monkeypatch):
    source, _store = semantic_job(tmp_path, monkeypatch)
    model = __import__("pathlib").Path(source["profile"]["model"]) / "model.onnx"
    model.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="analysis_model_hash_mismatch"):
        execute(source, monkeypatch)


def test_adapter_rejects_unknown_models_backends_and_bad_artifact_fingerprints(
    monkeypatch,
):
    base = {
        "id": SEGFORMER,
        "files": {"model.onnx": "a" * 64},
        "backend": "segformer_onnx",
    }
    valid = {**base, "model_sha256": artifact_fingerprint(base["files"])}
    for patch, error in [
        ({"id": FAST}, "analysis_model_needs_review"),
        ({"backend": "unknown"}, "analysis_model_variant_mismatch"),
        ({"model_sha256": "b" * 64}, "analysis_model_hash_mismatch"),
    ]:
        with pytest.raises(ValueError, match=error):
            SemanticCandidate({"model": "unused"}, {**valid, **patch}, 2)
    frozen = FrozenCatalog(SEGFORMER, "unused")
    with pytest.raises(ValueError, match="analysis_model_identity_mismatch"):
        frozen.path(YOLO)


@pytest.mark.parametrize(
    "result,error",
    [
        ({"state": "arbitrary", "_preview": b"bytes"}, "analysis_model_output"),
        ({"state": "clear"}, "analysis_model_preview_unavailable"),
    ],
)
def test_adapter_requires_real_preview_and_valid_classification(result, error):
    adapter = SemanticCandidate.__new__(SemanticCandidate)
    adapter.detector = SimpleNamespace(detect=lambda *_: result)
    adapter.identity = SEGFORMER, "a" * 64
    with pytest.raises(ValueError, match=error):
        adapter.detect(b"frame", (0.3, 0.6, 0.7, 0.9))
