"""Opt-in worker artifacts preserve legacy behavior and raw model assessments."""

import base64
import io
from copy import deepcopy

import numpy as np
import pytest
from PIL import Image
from test_analysis_semantic import semantic_job
from test_analysis_worker import PlaneModel, execute, job

from venom_module_rider import analysis_worker
from venom_module_rider.analysis_semantic import SemanticCandidate
from venom_module_rider.navigation_archive import decode_evidence


def frame_messages(messages):
    return [message for message in messages if message["type"] == "frame"]


def test_default_false_keeps_legacy_artifact_and_row_schema(tmp_path, monkeypatch):
    source, _ = job(tmp_path, monkeypatch)
    source["context"]["spec"]["capture_evidence"] = False
    messages, report = execute(source, monkeypatch)
    assert all("navigation_evidence" not in row for row in report["rows"])
    assert all(len(message["artifacts"]) == 3 for message in frame_messages(messages))
    assert all("navigation_evidence" not in row["timings_ms"] for row in report["rows"])


def test_depth_opt_in_adds_bounded_npz_and_no_private_arrays_to_json(
    tmp_path, monkeypatch
):
    source, store = job(tmp_path, monkeypatch)
    source["context"]["spec"]["capture_evidence"] = True
    originals = {p.name: p.read_bytes() for p in store.blobs.iterdir()}
    messages, report = execute(source, monkeypatch)
    for message, row in zip(frame_messages(messages), report["rows"], strict=True):
        assert row["state"] == "clear" and row["reason"] == "depth_plane_candidate"
        assert row["navigation_evidence"]["valid"] is True
        assert not any(key.startswith("_") for key in row)
        assert len(message["artifacts"]) == 4
        encoded = next(a for a in message["artifacts"] if a["name"].endswith(".npz"))
        payload = base64.b64decode(encoded["data"])
        assert len(payload) <= 4 * 1024**2
        maps = decode_evidence(payload)
        assert maps["floor"].shape == maps["gray"].shape == (48, 64)
        assert row["navigation_evidence"]["artifact"] == encoded["name"]
        assert row["navigation_evidence"]["width"] == 64
        assert row["navigation_evidence"]["height"] == 48
    assert {p.name: p.read_bytes() for p in store.blobs.iterdir()} == originals


def test_quality_rejection_is_exported_for_every_frame_without_loading_model(
    tmp_path, monkeypatch
):
    source, _ = job(tmp_path, monkeypatch, dark=True)
    source["context"]["spec"]["capture_evidence"] = True
    messages, report = execute(source, monkeypatch)
    assert PlaneModel.loaded == 0 and len(report["rows"]) == 3
    for message, row in zip(frame_messages(messages), report["rows"], strict=True):
        assert row["state"] == "unknown" and row["reason"] == "vision_image_quality"
        assert row["navigation_evidence"]["valid"] is False
        assert len(message["artifacts"]) == 1
        payload = base64.b64decode(message["artifacts"][0]["data"])
        maps = decode_evidence(payload)
        assert maps["uncertain"].all() and not maps["floor"].any()
        assert maps["valid"] is False


def test_ground_opt_in_preserves_independent_geometry_metadata(tmp_path, monkeypatch):
    from test_adaptive_ground import CAMERA, scene

    source, _ = job(tmp_path, monkeypatch, count=1)
    source["context"]["spec"].update(
        capture_evidence=True, geometry=CAMERA.model_dump(mode="json")
    )
    messages = []
    monkeypatch.setattr(
        analysis_worker,
        "emit",
        lambda _output, message: messages.append(deepcopy(message)) or {},
    )

    class GroundModel(PlaneModel):
        def infer(self, image):
            assert image.size == (64, 48)
            return scene()[::2, ::2]

    analysis_worker.run(source, None, candidate_factory=GroundModel)
    message = frame_messages(messages)[0]
    metadata = message["row"]["navigation_evidence"]
    assert metadata["valid"] is True
    payload = base64.b64decode(
        next(
            item["data"]
            for item in message["artifacts"]
            if item["name"].endswith(".npz")
        )
    )
    maps = decode_evidence(payload)
    assert maps["geometry_guard_clear"] is True
    assert maps["geometry_complete"] is True
    assert maps["geometry_collision"] is False
    assert maps["corridor"].shape == (48, 64)


def test_native_semantic_worker_retains_scores_and_original_unknown_result(
    tmp_path, monkeypatch
):
    source, _ = semantic_job(tmp_path, monkeypatch)
    source["context"]["spec"]["capture_evidence"] = True
    messages = []
    calls = []
    monkeypatch.setattr(
        analysis_worker,
        "emit",
        lambda _output, message: messages.append(deepcopy(message)) or {},
    )

    class Semantic:
        metadata = {"fixture": True}

        def __init__(self, *_):
            pass

        def detect(self, payload, corridor, *, evidence):
            calls.append((corridor, evidence))
            with Image.open(io.BytesIO(payload)) as image:
                shape = (image.height, image.width)
                overlay = io.BytesIO()
                image.convert("RGB").save(overlay, "JPEG")
            return {
                "state": "unknown",
                "reason": "vision_low_confidence",
                "_navigation_maps": {
                    "labels": np.full(shape, 3, np.uint8),
                    "confidence": np.full(shape, 0.76, np.float32),
                    "valid": True,
                },
            }, overlay.getvalue()

    analysis_worker.run(source, None, semantic_factory=Semantic)
    assert len(calls) == 3 and all(evidence is True for _, evidence in calls)
    for message in frame_messages(messages):
        row = message["row"]
        assert row["state"] == "unknown" and row["reason"] == "vision_low_confidence"
        assert row["navigation_evidence"]["valid"] is True
        payload = base64.b64decode(
            next(
                item["data"]
                for item in message["artifacts"]
                if item["name"].endswith(".npz")
            )
        )
        values = decode_evidence(payload)
        assert values["confidence"].dtype == np.float32
        assert set(values) == {"labels", "confidence", "gray", "valid"}


def test_semantic_adapter_forwards_opt_in_only_when_requested(monkeypatch):
    from types import SimpleNamespace

    from venom_module_rider import perception_runtime

    calls = []
    adapter = SemanticCandidate.__new__(SemanticCandidate)
    adapter.identity = ("segformer-b0-ade20k", "a" * 64)

    def detect(*args, **kwargs):
        calls.append(kwargs)
        return {"state": "clear", "_preview": b"image"}

    adapter.detector = SimpleNamespace(detect=detect)
    monkeypatch.setattr(perception_runtime, "source_overlay", lambda *args: b"overlay")
    adapter.detect(b"source", (0.0, 0.0, 1.0, 1.0))
    adapter.detect(b"source", (0.0, 0.0, 1.0, 1.0), evidence=True)
    assert calls == [{}, {"evidence": True}]


def test_worker_evidence_budget_and_strict_optin_are_explicit(tmp_path, monkeypatch):
    from venom_module_rider import navigation_archive

    source, _ = job(tmp_path, monkeypatch)
    source["context"]["spec"]["capture_evidence"] = "true"
    with pytest.raises(ValueError, match="analysis_navigation_evidence_opt_in"):
        execute(source, monkeypatch)
    source["context"]["spec"]["capture_evidence"] = True
    monkeypatch.setattr(navigation_archive, "MAX_EVIDENCE_BYTES", 10)
    with pytest.raises(ValueError, match="analysis_navigation_evidence_size"):
        execute(source, monkeypatch)


def test_probe_preserves_raw_model_state_but_invalid_quality_evidence_cannot_drive(
    tmp_path, monkeypatch
):
    source, _ = job(tmp_path, monkeypatch, dark=True, count=1)
    source["context"]["spec"].update(capture_evidence=True, quality_mode="model_probe")
    messages, report = execute(source, monkeypatch)
    assert PlaneModel.loaded == 1
    row = report["rows"][0]
    assert row["state"] == "clear" and row["image_quality_passed"] is False
    assert row["navigation_evidence"]["valid"] is False
    encoded = next(
        item
        for item in frame_messages(messages)[0]["artifacts"]
        if item["name"].endswith(".npz")
    )
    maps = decode_evidence(base64.b64decode(encoded["data"]))
    assert maps["valid"] is False and maps["uncertain"].all()
