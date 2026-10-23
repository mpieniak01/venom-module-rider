"""Compact artifact/export provenance, budget and replay integration checks."""

import hashlib
import io
import json

import numpy as np
import pytest
from PIL import Image

from scripts.export_navigation_evidence import main
from scripts.replay_navigation_policy import replay
from venom_module_rider import navigation_archive as archive
from venom_module_rider.navigation_profiles import DEFAULT_BEHAVIOR, parameters


def image(shape=(64, 96)):
    pixels = np.random.default_rng(27).integers(40, 220, shape, dtype=np.uint8)
    return Image.fromarray(pixels).convert("RGB")


def masks(shape=(64, 96), **changes):
    floor = np.ones(shape, dtype=bool)
    return {
        "floor": floor,
        "obstacle": ~floor,
        "uncertain": ~floor,
        "valid": True,
        **changes,
    }


def fixture_export(tmp_path, *, count=3, semantic=False, geometry=False):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    rows, inputs, timeline = [], [], []
    for index in range(count):
        source_sha = hashlib.sha256(f"original-{index}".encode()).hexdigest()
        frame_id = f"camera-{index}"
        source = {
            "camera_session": "camera",
            "sequence": index,
            "frame_id": frame_id,
            "sha256": source_sha,
            "transport_epoch": 2,
            "pipeline_revision": 4,
        }
        maps = (
            {
                "labels": np.full((64, 96), 3, np.uint8),
                "confidence": np.full((64, 96), 0.76, np.float32),
                "valid": True,
            }
            if semantic
            else masks()
        )
        if geometry:
            maps.update(
                geometry_guard_clear=False,
                geometry_collision=True,
                geometry_blocked=True,
                geometry_complete=True,
                corridor=np.ones((64, 96), dtype=bool),
            )
        payload, metadata = archive.encode_evidence(maps, image())
        name = f"{index:04}-navigation.npz"
        (artifacts / name).write_bytes(payload)
        rows.append(
            {
                "sample_id": f"sample{index}",
                "raw_sha256": source_sha,
                "state": "unknown" if semantic else "clear",
                "reason": "model_original_reason",
                "navigation_evidence": {**metadata, "artifact": name},
            }
        )
        inputs.append(
            {
                "sample_id": f"sample{index}",
                "sequence": index,
                "frame_id": frame_id,
                "raw": {"sha256": source_sha},
                "source_device_id": "recording",
                "session_id": "historical",
                "source_context": {
                    "source_identity": source,
                    "received_event": "import-read",
                    "received_at": 999999,
                },
            }
        )
        # Deliberately no frame_id: it is joined by original SHA+source_sequence.
        timeline.append(
            {"sha256": source_sha, "source_sequence": index, "source_s": index * 0.06}
        )
    context = {
        "inputs": inputs,
        "input_digest": "a" * 64,
        "spec": {"corridor": [0, 0, 1, 1]},
        "profile": {"fingerprint": "b" * 64, "model_sha256": "c" * 64},
        "code": {"sha256": "d" * 64},
        "session_id": "history",
        "dataset_revision": "e" * 64,
    }
    report = {
        "schema_version": 1,
        "context": context,
        "rows": rows,
        "input_digest": context["input_digest"],
    }
    report_file = tmp_path / "REPORT.json"
    report_file.write_text(json.dumps(report))
    timeline_file = tmp_path / "timeline.json"
    timeline_file.write_text(json.dumps(timeline))
    return report_file, artifacts, timeline_file, report, timeline


def test_encode_semantic_scores_and_quality_rejection_are_source_aligned():
    labels = np.full((64, 96), 3, dtype=np.uint8)
    score = np.full((64, 96), 0.76, dtype=np.float32)
    payload, meta = archive.encode_evidence(
        {"labels": labels, "confidence": score, "valid": True}, image()
    )
    values = archive.decode_evidence(payload)
    assert np.array_equal(values["labels"], labels)
    assert np.array_equal(values["confidence"], score)
    assert values["valid"] is True
    assert meta["sha256"] == hashlib.sha256(payload).hexdigest()
    assert (meta["width"], meta["height"]) == (96, 64)
    assert "floor" not in values
    rejected, meta = archive.encode_evidence(
        {"labels": labels, "confidence": score, "valid": True}, image(), rejected=True
    )
    values = archive.decode_evidence(rejected)
    assert values["uncertain"].all() and not values["floor"].any()
    assert values["valid"] is meta["valid"] is False
    fallback, _ = archive.encode_evidence(None, image())
    assert archive.decode_evidence(fallback)["valid"] is False


def test_explicit_opt_in_archive_roundtrips_real_model_metadata_and_source_time(
    tmp_path,
):
    report, artifacts, timeline, _, _ = fixture_export(tmp_path)
    output = tmp_path / "export"
    summary = archive.export_evidence(report, artifacts, timeline, output)
    rows = [
        json.loads(line)
        for line in (output / "manifest.jsonl").read_text().splitlines()
    ]
    assert summary["status"] == "complete" and summary["exported_frames"] == 3
    assert summary["qualified"] is False and summary["ground_truth"] == "not_inferred"
    assert summary["identity_provenance"] == ["sha_sequence_join_to_frozen_input"]
    assert [row["captured_s"] for row in rows] == [0, 0.06, 0.12]
    assert [row["frame_id"] for row in rows] == ["camera-0", "camera-1", "camera-2"]
    assert rows[0]["context"]["model_fingerprint"] == "b" * 64
    assert rows[0]["source_context"]["received_at"] == 999999
    assert rows[0]["source_time_provenance"] == "explicit_source_timeline"
    assert rows[0]["raw_state"] == "clear"
    assert summary["artifact_bytes"] == next((output / "maps").iterdir()).stat().st_size
    assert summary["max_bytes"] >= sum(
        p.stat().st_size for p in output.rglob("*") if p.is_file()
    )


@pytest.mark.parametrize("geometry", [False, True])
def test_export_roundtrip_existing_replay_respects_geometry_veto(tmp_path, geometry):
    report, artifacts, timeline, _, _ = fixture_export(tmp_path, geometry=geometry)
    output = tmp_path / "export"
    archive.export_evidence(report, artifacts, timeline, output)
    params = parameters({**DEFAULT_BEHAVIOR, "profile_id": "balanced"})
    summary = replay(output / "manifest.jsonl", params, tmp_path / "results.jsonl")
    assert summary["actions"] == ({"stop": 3} if geometry else {"stop": 2, "go": 1})
    results = [
        json.loads(line)
        for line in (tmp_path / "results.jsonl").read_text().splitlines()
    ]
    if geometry:
        assert all(
            row["temporal"]["reason"] == "navigation_geometry_near_obstacle"
            for row in results
        )


def test_semantic_compact_only_export_replays_with_shared_score_threshold(tmp_path):
    report, artifacts, timeline, _, _ = fixture_export(tmp_path, semantic=True)
    output = tmp_path / "export"
    archive.export_evidence(report, artifacts, timeline, output)
    params = parameters({**DEFAULT_BEHAVIOR, "profile_id": "balanced"})
    summary = replay(output / "manifest.jsonl", params, tmp_path / "results.jsonl")
    assert summary["actions"] == {"stop": 2, "go": 1}


@pytest.mark.parametrize("value", [None, True, 0, -1, 257, float("nan"), "1"])
def test_invalid_export_budgets_are_explicit(tmp_path, value):
    report, artifacts, timeline, _, _ = fixture_export(tmp_path)
    with pytest.raises(ValueError, match="analysis_navigation_export_budget"):
        archive.export_evidence(
            report, artifacts, timeline, tmp_path / "out", max_mib=value
        )
    assert not (tmp_path / "out").exists()


def test_export_is_prefix_partial_under_byte_cap_never_overwrites_sources(tmp_path):
    report, artifacts, timeline, frozen, _ = fixture_export(tmp_path, count=3)
    rng = np.random.default_rng(42)
    for row in frozen["rows"]:
        rgb = rng.integers(0, 255, (500, 600), dtype=np.uint8)
        payload, meta = archive.encode_evidence(
            {
                "labels": rng.integers(0, 150, (500, 600), dtype=np.uint8),
                "confidence": rng.random((500, 600), dtype=np.float32),
                "valid": True,
            },
            Image.fromarray(rgb),
        )
        name = row["navigation_evidence"]["artifact"]
        (artifacts / name).write_bytes(payload)
        row["navigation_evidence"] = {**meta, "artifact": name}
    report.write_text(json.dumps(frozen))
    before = {p.name: p.read_bytes() for p in artifacts.iterdir()}
    output = tmp_path / "partial"
    summary = archive.export_evidence(report, artifacts, timeline, output, max_mib=1)
    assert summary["status"] == "partial" and summary["reason"] == "byte_limit"
    assert summary["exported_frames"] + summary["skipped_frames"] == 3
    assert sum(p.stat().st_size for p in output.rglob("*") if p.is_file()) <= 1024**2
    assert {p.name: p.read_bytes() for p in artifacts.iterdir()} == before
    with pytest.raises(ValueError, match="analysis_navigation_export_output"):
        archive.export_evidence(report, artifacts, timeline, output)


@pytest.mark.parametrize(
    "change",
    [
        {"source_s": float("nan")},
        {"source_s": None},
        {"source_s": "receipt"},
        {"source_sequence": True},
        {"source_sequence": 1.1},
        {"frame_id": "different-frame"},
        {"sha256": "bad"},
    ],
)
def test_timeline_rejects_receipt_fallback_and_inconsistent_identities(
    tmp_path, change
):
    report, artifacts, timeline, _, source = fixture_export(tmp_path)
    source[0].update(change)
    timeline.write_text(json.dumps(source))
    with pytest.raises(ValueError, match="analysis_navigation_export_"):
        archive.export_evidence(report, artifacts, timeline, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_missing_or_ambiguous_source_times_never_use_import_receipts(tmp_path):
    report, artifacts, timeline, _, source = fixture_export(tmp_path)
    source[0].pop("source_s")
    source[0]["received_at"] = 1234
    timeline.write_text(json.dumps(source))
    with pytest.raises(ValueError, match="analysis_navigation_export_source_time"):
        archive.export_evidence(report, artifacts, timeline, tmp_path / "out")
    source[0]["source_s"] = 0
    source.append(source[0])
    timeline.write_text(json.dumps(source))
    with pytest.raises(ValueError, match="analysis_navigation_export_timeline_match"):
        archive.export_evidence(report, artifacts, timeline, tmp_path / "out")


def test_explicit_frame_identity_and_source_gap_preserved(tmp_path):
    report, artifacts, timeline, frozen, source = fixture_export(tmp_path)
    for row in source:
        row["frame_id"] = f"camera-{row['source_sequence']}"
    source[-1]["source_gap"] = True
    timeline.write_text(json.dumps(source))
    output = tmp_path / "out"
    summary = archive.export_evidence(report, artifacts, timeline, output)
    rows = [
        json.loads(line)
        for line in (output / "manifest.jsonl").read_text().splitlines()
    ]
    assert summary["identity_provenance"] == ["explicit_timeline_and_frozen_input"]
    assert rows[-1]["source_gap"] is True
    assert json.loads(report.read_text()) == frozen


@pytest.mark.parametrize(
    "patch",
    [
        {"artifact": "../outside.npz"},
        {"artifact": "bad..npz"},
        {"artifact": "/tmp/file.npz"},
        {"artifact": "image.jpg"},
        {"version": 2},
        {"sha256": "bad"},
    ],
)
def test_export_metadata_cannot_select_arbitrary_files(tmp_path, patch):
    report, artifacts, timeline, frozen, _ = fixture_export(tmp_path)
    frozen["rows"][0]["navigation_evidence"].update(patch)
    report.write_text(json.dumps(frozen))
    with pytest.raises(ValueError, match="analysis_navigation_export_"):
        archive.export_evidence(report, artifacts, timeline, tmp_path / "out")


def test_tampered_evidence_yields_failed_status_and_cannot_be_replayed_as_complete(
    tmp_path,
):
    report, artifacts, timeline, frozen, _ = fixture_export(tmp_path)
    path = artifacts / frozen["rows"][0]["navigation_evidence"]["artifact"]
    path.write_bytes(b"tampered")
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="analysis_navigation_export_failed"):
        archive.export_evidence(report, artifacts, timeline, output)
    assert json.loads((output / "EXPORT.json").read_text())["status"] == "failed"
    assert (output / "manifest.jsonl").read_bytes() == b""


def test_cli_exports_a_new_artifact_directory_and_prints_summary(tmp_path, capsys):
    report, artifacts, timeline, _, _ = fixture_export(tmp_path)
    assert (
        main(
            [
                "--report",
                str(report),
                "--artifacts",
                str(artifacts),
                "--timeline",
                str(timeline),
                "--output",
                str(tmp_path / "out"),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "complete"


@pytest.mark.parametrize(
    "patch",
    [
        {"valid": "true"},
        {"floor": np.ones((2, 2), bool)},
        {"critical": np.ones((2, 2), bool)},
        {"geometry_guard_clear": 1},
    ],
)
def test_invalid_masks_and_boolean_metadata_rejected(patch):
    with pytest.raises(ValueError, match="analysis_navigation_evidence_"):
        archive.encode_evidence(masks(**patch), image())


def test_invalid_labels_scores_and_partitions_are_never_serialized_as_clear():
    for maps in [
        {"labels": np.full((64, 96), 3, np.uint8), "valid": True},
        {
            "labels": np.full((64, 96), 255, np.uint8),
            "confidence": np.ones((64, 96), np.float32),
            "valid": True,
        },
        masks(obstacle=np.ones((64, 96), bool)),
    ]:
        with pytest.raises(ValueError):
            archive.encode_evidence(maps, image())


def test_encode_and_decode_hard_artifact_caps(monkeypatch):
    monkeypatch.setattr(archive, "MAX_EVIDENCE_BYTES", 10)
    with pytest.raises(ValueError, match="analysis_navigation_evidence_size"):
        archive.encode_evidence(masks(), image())
    with pytest.raises(ValueError, match="analysis_navigation_evidence_size"):
        archive.decode_evidence(b"x" * 11)


def test_decode_rejects_untrusted_payload_members_and_pickle():
    for payload in [b"not-a-zip", b""]:
        with pytest.raises(ValueError):
            archive.decode_evidence(payload)
    for values in [
        {"untrusted": np.ones((2, 2))},
        {"gray": np.empty((64, 96), object), "valid": True},
        {"gray": np.ones((64, 96), np.uint8), "valid": np.ones(1, bool)},
    ]:
        stream = io.BytesIO()
        np.savez_compressed(stream, **values)
        with pytest.raises(ValueError):
            archive.decode_evidence(stream.getvalue())


def test_identity_can_join_historical_source_context_when_top_fields_absent(tmp_path):
    report, artifacts, timeline, frozen, _ = fixture_export(tmp_path, count=2)
    for sample in frozen["context"]["inputs"]:
        sample.pop("frame_id")
        sample.pop("sequence")
    report.write_text(json.dumps(frozen))
    out = tmp_path / "out"
    summary = archive.export_evidence(report, artifacts, timeline, out)
    rows = [
        json.loads(line) for line in (out / "manifest.jsonl").read_text().splitlines()
    ]
    assert [row["frame_id"] for row in rows] == ["camera-0", "camera-1"]
    assert summary["total_bytes"] == sum(
        p.stat().st_size for p in out.rglob("*") if p.is_file()
    )


def test_cli_partial_export_has_explicit_nonzero_status(tmp_path, capsys):
    report, artifacts, timeline, _, _ = fixture_export(tmp_path)
    status = main(
        [
            "--report",
            str(report),
            "--artifacts",
            str(artifacts),
            "--timeline",
            str(timeline),
            "--output",
            str(tmp_path / "partial"),
            "--max-mib",
            ".01",
        ]
    )
    summary = json.loads(capsys.readouterr().out)
    assert status == 2 and summary["status"] == "partial"
    assert summary["skipped_frames"] > 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("width", 1),
        ("height", 1),
        ("valid", "true"),
        ("valid", False),
    ],
)
def test_declared_evidence_metadata_must_match_verified_array_bytes(
    tmp_path, field, value
):
    report, artifacts, timeline, frozen, _ = fixture_export(tmp_path)
    frozen["rows"][0]["navigation_evidence"][field] = value
    report.write_text(json.dumps(frozen))
    with pytest.raises(ValueError, match="analysis_navigation_export_failed"):
        archive.export_evidence(report, artifacts, timeline, tmp_path / "out")


def test_export_rejects_bad_report_and_missing_evidence_without_writing(tmp_path):
    report, artifacts, timeline, frozen, _ = fixture_export(tmp_path)
    for malformed in [[], {}, {"context": {}, "rows": []}]:
        report.write_text(json.dumps(malformed))
        with pytest.raises(ValueError, match="analysis_navigation_export_report"):
            archive.export_evidence(report, artifacts, timeline, tmp_path / "out")
    frozen["rows"][0].pop("navigation_evidence")
    report.write_text(json.dumps(frozen))
    with pytest.raises(ValueError, match="analysis_navigation_export_evidence_missing"):
        archive.export_evidence(report, artifacts, timeline, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_export_invalid_source_sequences_and_non_monotonic_time_are_explicit(tmp_path):
    report, artifacts, timeline, frozen, source = fixture_export(tmp_path)
    frozen["context"]["inputs"][0]["source_context"]["source_identity"]["sequence"] = (
        True
    )
    report.write_text(json.dumps(frozen))
    with pytest.raises(ValueError, match="analysis_navigation_export_identity"):
        archive.export_evidence(report, artifacts, timeline, tmp_path / "out")
    frozen["context"]["inputs"][0]["source_context"]["source_identity"]["sequence"] = 0
    report.write_text(json.dumps(frozen))
    source[1]["source_s"] = 0
    timeline.write_text(json.dumps(source))
    with pytest.raises(ValueError, match="analysis_navigation_export_source_time"):
        archive.export_evidence(report, artifacts, timeline, tmp_path / "out")


def test_symlink_sources_and_artifacts_are_not_followed(tmp_path):
    report, artifacts, timeline, frozen, _ = fixture_export(tmp_path)
    alias = tmp_path / "alias.json"
    alias.symlink_to(report)
    with pytest.raises(ValueError, match="analysis_navigation_export_source"):
        archive.export_evidence(alias, artifacts, timeline, tmp_path / "out")
    directory = tmp_path / "alias-artifacts"
    directory.symlink_to(artifacts, target_is_directory=True)
    with pytest.raises(ValueError, match="analysis_navigation_export_artifact"):
        archive.export_evidence(report, directory, timeline, tmp_path / "out")
    path = artifacts / frozen["rows"][0]["navigation_evidence"]["artifact"]
    target = tmp_path / "real.npz"
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(ValueError, match="analysis_navigation_export_artifact"):
        archive.export_evidence(report, artifacts, timeline, tmp_path / "out")


def test_invalid_gray_shape_or_oversized_compressed_members_rejected():
    for gray in [np.ones((2, 2), np.float32), np.ones((1, 2, 3), np.uint8)]:
        stream = io.BytesIO()
        np.savez_compressed(stream, gray=gray, valid=False)
        with pytest.raises(ValueError, match="analysis_navigation_evidence_shape"):
            archive.decode_evidence(stream.getvalue())
    stream = io.BytesIO()
    np.savez_compressed(stream, floor=np.ones((2200, 2200), dtype=bool))
    with pytest.raises(ValueError, match="analysis_navigation_evidence_size"):
        archive.decode_evidence(stream.getvalue())


def test_semantic_source_size_must_equal_decoded_image():
    with pytest.raises(ValueError, match="analysis_navigation_evidence_shape"):
        archive.encode_evidence(
            {
                "labels": np.full((32, 32), 3, np.uint8),
                "confidence": np.ones((32, 32), np.float32),
                "valid": True,
            },
            image(),
        )


def test_export_rejects_empty_timeline_and_report_row_identity_mismatch(tmp_path):
    report, artifacts, timeline, frozen, source = fixture_export(tmp_path)
    timeline.write_text("[]")
    with pytest.raises(ValueError, match="analysis_navigation_export_timeline"):
        archive.export_evidence(report, artifacts, timeline, tmp_path / "out")
    timeline.write_text(json.dumps(source))
    frozen["rows"][0]["raw_sha256"] = "f" * 64
    report.write_text(json.dumps(frozen))
    with pytest.raises(ValueError, match="analysis_navigation_export_report"):
        archive.export_evidence(report, artifacts, timeline, tmp_path / "out")


@pytest.mark.parametrize(
    "model_id,artifact,expected_error",
    [
        ("segformer-b0-ade20k", "", None),
        ("yolo26n-sem-ade20k", "", "replay_model_identity"),
        ("segformer-b0-ade20k", "d" * 64, "replay_model_artifact"),
    ],
)
def test_explicit_replay_settings_are_bound_to_model_maps(
    tmp_path, model_id, artifact, expected_error
):
    from venom_module_rider.depth_detector import PerceptionSettings

    report, artifacts, timeline, frozen, _ = fixture_export(tmp_path, semantic=True)
    frozen["context"]["profile"]["id"] = "segformer-b0-ade20k"
    report.write_text(json.dumps(frozen))
    output = tmp_path / "export"
    archive.export_evidence(report, artifacts, timeline, output)
    perception = PerceptionSettings(
        engine="segformer",
        model_id=model_id,
        artifact_sha256=artifact or "c" * 64,
        semantic_models={model_id: {"surface": "floor_rug"}},
    ).model_dump()
    behavior = {**DEFAULT_BEHAVIOR, "profile_id": "balanced"}
    if expected_error:
        with pytest.raises(ValueError, match=expected_error):
            replay(
                output / "manifest.jsonl",
                parameters(behavior, perception),
                tmp_path / "result.jsonl",
                behavior=behavior,
                perception=perception,
            )
    else:
        summary = replay(
            output / "manifest.jsonl",
            parameters(behavior, perception),
            tmp_path / "result.jsonl",
            behavior=behavior,
            perception=perception,
        )
        assert summary["frames"] == 3
        assert (
            summary["profile"]["perception"]["semantic_models"][model_id]["surface"]
            == "floor_rug"
        )


@pytest.mark.parametrize("semantic_maps", [True, False])
def test_explicit_replay_rejects_maps_from_another_model_type(tmp_path, semantic_maps):
    report, artifacts, timeline, frozen, _ = fixture_export(
        tmp_path, semantic=semantic_maps
    )
    model = "da2-default" if semantic_maps else "segformer-b0-ade20k"
    frozen["context"]["profile"]["id"] = model
    report.write_text(json.dumps(frozen))
    output = tmp_path / "export"
    archive.export_evidence(report, artifacts, timeline, output)
    perception = {"engine": "depth_plane" if semantic_maps else "segformer"}
    behavior = {**DEFAULT_BEHAVIOR, "profile_id": "balanced"}
    with pytest.raises(ValueError, match="replay_.*_maps_required"):
        replay(
            output / "manifest.jsonl",
            parameters(behavior, perception),
            tmp_path / "result.jsonl",
            behavior=behavior,
            perception=perception,
        )
