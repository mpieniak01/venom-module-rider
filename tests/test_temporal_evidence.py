"""Temporal policy tests use synthetic frames and explicit model observations."""

import json
from pathlib import Path

import numpy as np
import pytest

from scripts.replay_navigation_policy import main, replay
from venom_module_rider.optical_motion import OpticalMotion
from venom_module_rider.temporal_evidence import TemporalEvidence, _region_link


@pytest.fixture
def params():
    return {
        "min_region_fraction": 0.02,
        "thin_span_fraction": 0.4,
        "near_zone_fraction": 0.25,
        "min_floor_fraction": 0.95,
        "min_near_floor_fraction": 0.98,
        "max_uncertain_fraction": 0.04,
        "enter_frames": 3,
        "enter_ms": 100,
        "release_frames": 2,
        "release_ms": 40,
        "history_ms": 1000,
        "max_gap_ms": 200,
        "max_uncertain_ms": 180,
        "min_tracking_ratio": 0.5,
        "slow_speed_factor": 0.5,
    }


def observation(candidate="clear", **kwargs):
    result = {
        "valid": True,
        "candidate": candidate,
        "guard_clear": True,
        "hard_stop": False,
        "regions": ([{"bbox": [20, 20, 40, 40]}] if candidate == "obstacle" else []),
    }
    return result | kwargs


def tick(evaluator, params, index, candidate="clear", **kwargs):
    return evaluator.update(
        observation(candidate) | kwargs.pop("obs", {}),
        **{
            "context": {"source": "camera-a", "epoch": 1, "model": "m", "policy": 1},
            "sequence": index,
            "frame_id": f"frame-{index}",
            "captured_s": 10.0 + index * 0.06,
            "params": params,
        }
        | kwargs,
    )


def establish(evaluator, params, **kwargs):
    assert tick(evaluator, params, 0, **kwargs)["action"] == "stop"
    assert tick(evaluator, params, 1, **kwargs)["action"] == "go"


def test_initial_clear_release_and_no_tracking_mode(params):
    evaluator = TemporalEvidence()
    establish(evaluator, params)
    result = tick(evaluator, params, 2)
    assert result["action"] == "go"
    assert result["state"] == "clear"
    assert result["speed_factor"] == 1
    assert result["tracking"] == {"status": "disabled", "mode": "temporal_only"}
    assert result["evidence_frames"] == 3
    assert result["near_guard_clear"] is True
    assert result["distance_measurement"] is False


def test_near_hazard_and_support_stop_without_confirmation(params):
    evaluator = TemporalEvidence()
    result = tick(
        evaluator,
        params,
        0,
        "obstacle",
        obs={"hard_stop": True, "reason": "navigation_critical_support"},
    )
    assert result["action"] == "stop"
    assert result["state"] == "obstacle"
    assert result["speed_factor"] == 0
    assert result["reason"] == "navigation_critical_support"
    assert tick(evaluator, params, 1)["action"] == "stop"
    assert tick(evaluator, params, 2)["action"] == "go"


def test_far_obstacle_region_requires_enter_hysteresis(params):
    evaluator = TemporalEvidence()
    establish(evaluator, params)
    assert tick(evaluator, params, 2, "obstacle")["action"] == "slow"
    assert tick(evaluator, params, 3, "obstacle")["action"] == "slow"
    result = tick(evaluator, params, 4, "obstacle")
    assert result["action"] == "stop"
    assert result["state"] == "obstacle"
    assert result["reason"] == "navigation_obstacle_confirmed"


def test_unrelated_regions_do_not_confirm_same_obstacle(params):
    evaluator = TemporalEvidence()
    establish(evaluator, params)
    for index in (2, 3, 4):
        offset = index * 30
        result = tick(
            evaluator,
            params,
            index,
            "obstacle",
            obs={"regions": [{"bbox": [offset, 0, offset + 10, 10]}]},
        )
        assert result["reason"] == "navigation_obstacle_confirmation"
        assert result["action"] == "slow"


def test_short_uncertainty_slows_but_expires_and_needs_clear_release(params):
    evaluator = TemporalEvidence()
    establish(evaluator, params)
    result = tick(evaluator, params, 2, "uncertain")
    assert result["action"] == "slow"
    assert result["speed_factor"] == 0.5
    assert tick(evaluator, params, 3, "uncertain")["action"] == "slow"
    assert tick(evaluator, params, 4, "uncertain")["action"] == "slow"
    assert tick(evaluator, params, 6, "uncertain")["action"] == "stop"
    assert tick(evaluator, params, 7)["action"] == "stop"
    assert tick(evaluator, params, 8)["action"] == "go"


def test_no_near_positive_evidence_never_uses_uncertainty_allowance(params):
    evaluator = TemporalEvidence()
    establish(evaluator, params)
    result = tick(evaluator, params, 2, "uncertain", obs={"guard_clear": False})
    assert result["action"] == "stop"
    assert result["near_guard_clear"] is False
    assert tick(evaluator, params, 3, "uncertain")["action"] == "stop"


def test_disabled_uncertainty_allowance(params):
    params["max_uncertain_ms"] = 0
    evaluator = TemporalEvidence()
    establish(evaluator, params)
    assert tick(evaluator, params, 2, "uncertain")["action"] == "stop"


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"sequence": 1}, "navigation_duplicate_frame"),
        ({"frame_id": "frame-0"}, "navigation_duplicate_frame"),
        ({"captured_s": 10.0}, "navigation_time_discontinuity"),
        ({"captured_s": 11.0}, "navigation_frame_gap"),
        ({"context": {"source": "other"}}, "navigation_context_changed"),
        ({"obs": {"source_gap": True}}, "navigation_frame_gap"),
    ],
)
def test_discontinuities_reset_without_new_permission(params, kwargs, expected):
    evaluator = TemporalEvidence()
    establish(evaluator, params)
    result = tick(evaluator, params, 2, **kwargs)
    assert result["action"] == "stop"
    assert result["reason"] == expected
    assert result["evidence_frames"] == 0


def test_duplicate_is_remembered_and_cannot_be_replayed_twice(params):
    evaluator = TemporalEvidence()
    establish(evaluator, params)
    for index in (2, 3):
        result = tick(evaluator, params, index, frame_id="frame-0")
        assert result["reason"] == "navigation_duplicate_frame"


def test_skipped_unprocessed_camera_sequences_are_not_stream_gap(params):
    evaluator = TemporalEvidence()
    assert tick(evaluator, params, 0)["action"] == "stop"
    result = tick(evaluator, params, 1, sequence=9)
    assert result["action"] == "go"


def test_unreliable_source_and_invalid_candidate_stop_and_reset(params):
    evaluator = TemporalEvidence()
    establish(evaluator, params)
    for index, obs in enumerate(
        [
            {"valid": False, "reason": "vision_image_quality"},
            {"candidate": "unsupported"},
        ],
        start=2,
    ):
        result = tick(evaluator, params, index, obs=obs)
        assert result["action"] == "stop"
        assert result["evidence_frames"] == 0


def test_history_is_bounded_and_evicted_by_elapsed_time(params):
    params["history_ms"] = 180
    params["max_gap_ms"] = 120
    evaluator = TemporalEvidence()
    for index in range(25):
        result = tick(evaluator, params, index)
    assert result["action"] == "go"
    assert result["evidence_frames"] <= 4
    assert result["history_ms"] <= 180.001


def test_stationary_featureless_floor_does_not_require_camera_movement(params):
    gray = np.full((120, 160), 128, dtype=np.uint8)
    evaluator = TemporalEvidence()
    establish(evaluator, params, gray=gray)
    result = tick(evaluator, params, 2, gray=gray)
    assert result["action"] == "go"
    assert result["tracking"]["status"] == "unavailable"


def texture():
    return np.random.default_rng(812).integers(0, 255, (120, 160), dtype=np.uint8)


def test_floor_texture_motion_alone_is_not_obstacle_and_blur_loses_tracking(params):
    gray = texture()
    evaluator = TemporalEvidence()
    assert tick(evaluator, params, 0, gray=gray)["action"] == "stop"
    shifted = np.roll(gray, (1, 2), axis=(0, 1))
    result = tick(evaluator, params, 1, gray=shifted)
    assert result["action"] == "go"
    assert result["tracking"]["status"] == "ok"
    assert result["tracking"]["median_motion_px"] > 1
    blurred = np.full_like(gray, 128)
    result = tick(evaluator, params, 2, gray=blurred)
    assert result["action"] == "stop"
    assert result["reason"] == "navigation_tracking_lost"
    assert tick(evaluator, params, 3, gray=gray)["action"] == "stop"
    assert tick(evaluator, params, 4, gray=gray)["action"] == "go"


def test_tracking_failure_after_established_cannot_turn_into_go(params, monkeypatch):
    evaluator = TemporalEvidence()
    establish(evaluator, params)
    monkeypatch.setattr(evaluator._motion, "update", lambda *args: {"status": "lost"})
    result = tick(evaluator, params, 2, gray=texture())
    assert result["action"] == "stop"
    assert result["reason"] == "navigation_tracking_lost"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sequence": -1},
        {"sequence": True},
        {"sequence": 1.1},
        {"frame_id": ""},
        {"frame_id": 8},
        {"captured_s": float("nan")},
        {"captured_s": True},
        {"captured_s": "10"},
        {"context": {}},
        {"context": []},
    ],
)
def test_invalid_frame_identity_rejected(params, kwargs):
    with pytest.raises(ValueError, match="navigation_frame_identity"):
        tick(TemporalEvidence(), params, 0, **kwargs)


@pytest.mark.parametrize(
    "name,value",
    [
        ("enter_frames", 0),
        ("release_frames", 1.1),
        ("enter_frames", 257),
        ("enter_ms", -1),
        ("release_ms", 1001),
        ("history_ms", 10001),
        ("history_ms", 0),
        ("max_gap_ms", 0),
        ("max_gap_ms", 1001),
        ("max_uncertain_ms", -1),
        ("min_tracking_ratio", 0),
        ("min_tracking_ratio", 1.1),
        ("slow_speed_factor", 0),
        ("slow_speed_factor", 1.1),
        ("enter_frames", None),
        ("enter_frames", True),
        ("enter_frames", float("inf")),
    ],
)
def test_invalid_policy_rejected(params, name, value):
    params[name] = value
    with pytest.raises(ValueError, match="navigation_temporal_params"):
        tick(TemporalEvidence(), params, 0)


def test_region_matching_warp_and_growth_are_diagnostic():
    old = [{"bbox": [10, 10, 20, 20]}]
    new = [{"bbox": [12, 12, 28, 28]}]
    transform = np.array([[1.0, 0, 2], [0, 1.0, 2]])
    linked, growth = _region_link(old, new, transform)
    assert linked is True
    assert growth == pytest.approx(1.6)
    assert _region_link(old, [{"bbox": [90, 90, 99, 99]}], None) == (False, 1.0)


def test_optical_invalid_shape_and_low_feature_initialization():
    motion = OpticalMotion()
    for bad in [None, np.ones((20, 20)), np.zeros((721, 10), dtype=np.uint8)]:
        assert motion.update(bad, 0.5)["status"] == "lost"
    assert motion.update(texture(), 0.5)["status"] == "initial"
    assert motion.update(np.zeros((100, 100), dtype=np.uint8), 0.5)["status"] == "lost"
    motion.established = True
    motion.previous = np.full((120, 160), 128, dtype=np.uint8)
    assert motion.update(texture(), 0.5)["status"] == "lost"


@pytest.mark.parametrize("branch", ["forward", "backward", "transform", "inliers"])
def test_optical_invalid_tracking_results_are_explicit(branch, monkeypatch):
    import cv2

    motion = OpticalMotion()
    gray = texture()
    motion.update(gray, 0.5)
    original = cv2.calcOpticalFlowPyrLK
    calls = 0

    def flow(*args, **kwargs):
        nonlocal calls
        calls += 1
        if (branch == "forward" and calls == 1) or (
            branch == "backward" and calls == 2
        ):
            return None, None, None
        return original(*args, **kwargs)

    monkeypatch.setattr(cv2, "calcOpticalFlowPyrLK", flow)
    if branch == "transform":
        monkeypatch.setattr(
            cv2, "estimateAffinePartial2D", lambda *a, **k: (None, None)
        )
    elif branch == "inliers":
        monkeypatch.setattr(
            cv2,
            "estimateAffinePartial2D",
            lambda *a, **k: (np.array([[1.0, 0, 0], [0, 1.0, 0]]), np.zeros((160, 1))),
        )
    assert motion.update(gray, 0.5)["status"] == "lost"


def write_replay(tmp_path: Path, *, raw_state=False):
    floor = np.ones((100, 100), dtype=bool)
    obstacle = np.zeros_like(floor)
    uncertain = np.zeros_like(floor)
    floor[10, 10] = False
    uncertain[10, 10] = True
    np.savez_compressed(
        tmp_path / "maps.npz", floor=floor, obstacle=obstacle, uncertain=uncertain
    )
    rows = []
    for index in range(3):
        row = {
            "frame_id": str(index),
            "sequence": index,
            "captured_s": index * 0.06,
            "context": {"epoch": 1},
            "maps": "maps.npz",
            "corridor": [0, 0, 1, 1],
        }
        if raw_state:
            row["raw_state"] = "unknown"
        rows.append(row)
    manifest = tmp_path / "input.jsonl"
    manifest.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return manifest


def test_replay_uses_source_maps_preserves_provenance_and_never_labels_truth(
    tmp_path, params
):
    manifest = write_replay(tmp_path)
    output = tmp_path / "results.jsonl"
    summary = replay(manifest, params, output)
    assert summary["frames"] == 3
    assert summary["actions"] == {"stop": 1, "go": 2}
    assert summary["ground_truth"] == "not_inferred"
    assert summary["robot_connected"] is False
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert rows[0]["baseline_reconstructed"] is True
    assert rows[0]["raw_state"] == "unknown"
    assert len(rows[0]["maps_sha256"]) == 64
    assert not any(key.startswith("_") for key in rows[0]["spatial"])


def test_replay_main_writes_summary_and_preserves_original_raw_state(tmp_path, params):
    manifest = write_replay(tmp_path, raw_state=True)
    policy = tmp_path / "params.json"
    policy.write_text(json.dumps(params))
    output = tmp_path / "results.jsonl"
    assert (
        main(
            [
                "--manifest",
                str(manifest),
                "--params",
                str(policy),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert output.with_suffix(".summary.json").is_file()
    assert (
        json.loads(output.read_text().splitlines()[0])["baseline_reconstructed"]
        is False
    )
    policy.write_text("[]")
    with pytest.raises(ValueError, match="replay_params"):
        main(
            [
                "--manifest",
                str(manifest),
                "--params",
                str(policy),
                "--output",
                str(output),
            ]
        )


@pytest.mark.parametrize(
    "maps_path", ["../other.npz", "/tmp/file.npz", "missing.npz", 1]
)
def test_replay_path_escape_and_missing_maps_rejected(tmp_path, params, maps_path):
    manifest = write_replay(tmp_path)
    row = json.loads(manifest.read_text().splitlines()[0])
    row["maps"] = maps_path
    manifest.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="replay_"):
        replay(manifest, params, tmp_path / "result.jsonl")


def test_replay_bad_npz_keys_and_invalid_observation(tmp_path, params):
    manifest = write_replay(tmp_path)
    np.savez_compressed(tmp_path / "maps.npz", labels=np.ones((10, 10)))
    with pytest.raises(ValueError, match="replay_maps_keys"):
        replay(manifest, params, tmp_path / "result.jsonl")
    manifest = write_replay(tmp_path)
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    rows[0]["valid"] = False
    rows[0]["reason"] = "vision_image_quality"
    rows[1]["source_gap"] = True
    rows[2]["raw_state"] = "not_a_state"
    manifest.write_text("\n".join(json.dumps(row) for row in rows))
    output = tmp_path / "result.jsonl"
    with pytest.raises(ValueError, match="replay_raw_state"):
        replay(manifest, params, output)
    first = json.loads(output.read_text().splitlines()[0])
    assert first["temporal"]["action"] == "stop"
    assert first["temporal"]["reason"] == "vision_image_quality"


def test_empty_replay_has_no_invented_performance_or_truth(tmp_path, params):
    manifest = tmp_path / "empty.jsonl"
    manifest.write_text("\n")
    summary = replay(manifest, params, tmp_path / "output.jsonl")
    assert summary["frames"] == 0
    assert summary["evaluation_ms"] == {"p50": None, "p95": None, "max": None}


def test_clear_label_without_positive_near_guard_cannot_count_for_release(params):
    evaluator = TemporalEvidence()
    first = tick(evaluator, params, 0, obs={"guard_clear": False})
    assert first["action"] == "stop"
    assert tick(evaluator, params, 1)["action"] == "stop"
    assert tick(evaluator, params, 2)["action"] == "go"


def test_obstacle_streak_ends_when_clear_returns(params):
    evaluator = TemporalEvidence()
    assert evaluator._streak("clear") == (0, 0.0)
    establish(evaluator, params)
    assert tick(evaluator, params, 2, "obstacle")["action"] == "slow"
    assert tick(evaluator, params, 3)["action"] == "slow"
    assert tick(evaluator, params, 4)["action"] == "go"


def test_replay_profile_cli_and_source_dimensions(tmp_path):
    manifest = write_replay(tmp_path, raw_state=True)
    output = tmp_path / "balanced.jsonl"
    assert (
        main(
            [
                "--manifest",
                str(manifest),
                "--profile",
                "balanced",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    summary = json.loads(output.with_suffix(".summary.json").read_text())
    assert summary["profile"]["id"] == "balanced"
    assert summary["profile"]["qualified"] is False
    assert len(summary["profile"]["effective_policy_sha256"]) == 64
    assert summary["source_dimensions"] == [{"width": 100, "height": 100}]
    assert json.loads(output.read_text().splitlines()[0])["source_dimensions"] == {
        "width": 100,
        "height": 100,
    }


def test_replay_cannot_overwrite_source_inputs(tmp_path, params):
    manifest = write_replay(tmp_path)
    with pytest.raises(ValueError, match="replay_output_overwrites_input"):
        replay(manifest, params, manifest)
    policy = tmp_path / "params.json"
    policy.write_text(json.dumps(params))
    with pytest.raises(ValueError, match="replay_output_overwrites_input"):
        main(
            [
                "--manifest",
                str(manifest),
                "--params",
                str(policy),
                "--output",
                str(policy),
            ]
        )


def test_replay_decompression_and_manifest_bounds(tmp_path, params):
    manifest = write_replay(tmp_path)
    np.savez_compressed(
        tmp_path / "maps.npz",
        **{
            key: np.ones((2200, 2200), dtype=bool)
            for key in ("floor", "obstacle", "uncertain")
        },
    )
    with pytest.raises(ValueError, match="replay_maps_bounds"):
        replay(manifest, params, tmp_path / "output.jsonl")
    manifest.write_text("x" * (65536 + 1))
    with pytest.raises(ValueError, match="replay_manifest_bounds"):
        replay(manifest, params, tmp_path / "output.jsonl")


def test_replay_large_file_is_rejected_before_numpy_load(tmp_path, params):
    manifest = write_replay(tmp_path)
    with (tmp_path / "maps.npz").open("wb") as stream:
        stream.truncate(32 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="replay_maps_bounds"):
        replay(manifest, params, tmp_path / "output.jsonl")


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("obstacle", "obstacle"),
        ("uncertain", "unknown"),
        ("floor", "clear"),
    ],
)
def test_replay_strict_map_baseline_is_explicit_reconstruction(
    tmp_path, params, kind, expected
):
    manifest = write_replay(tmp_path)
    floor = np.ones((100, 100), dtype=bool)
    obstacle = np.zeros_like(floor)
    uncertain = np.zeros_like(floor)
    if kind == "obstacle":
        floor[10:20, :] = False
        obstacle[10:20, :] = True
    elif kind == "uncertain":
        floor[10:20, :] = False
        uncertain[10:20, :] = True
    np.savez_compressed(
        tmp_path / "maps.npz", floor=floor, obstacle=obstacle, uncertain=uncertain
    )
    output = tmp_path / "result.jsonl"
    replay(manifest, params, output)
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert all(row["raw_state"] == expected for row in rows)
    assert all(row["baseline_reconstructed"] for row in rows)


def test_replay_outputs_cannot_clobber_source_npz(tmp_path, params):
    manifest = write_replay(tmp_path)
    with pytest.raises(ValueError, match="replay_output_format"):
        replay(manifest, params, tmp_path / "maps.npz")
    row = json.loads(manifest.read_text().splitlines()[0])
    row["maps"] = "other.jsonl"
    (tmp_path / "other.jsonl").write_bytes((tmp_path / "maps.npz").read_bytes())
    manifest.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="replay_maps_format"):
        replay(manifest, params, tmp_path / "output.jsonl")


def test_replay_semantic_score_maps_apply_shared_profile_threshold(tmp_path, params):
    manifest = write_replay(tmp_path)
    params["pixel_confidence"] = 0.75
    labels = np.full((100, 100), 3, dtype=np.uint8)
    confidence = np.full((100, 100), 0.76, dtype=np.float16)
    np.savez_compressed(tmp_path / "maps.npz", labels=labels, confidence=confidence)
    output = tmp_path / "balanced.jsonl"
    summary = replay(manifest, params, output)
    assert summary["actions"] == {"stop": 1, "go": 2}
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert rows[0]["spatial"]["floor_fraction"] == 1
    params["pixel_confidence"] = 0.8
    strict = replay(manifest, params, tmp_path / "cautious.jsonl")
    assert strict["actions"] == {"stop": 3}


def test_replay_rejects_incomplete_or_invalid_semantic_scores(tmp_path, params):
    manifest = write_replay(tmp_path)
    params["pixel_confidence"] = 0.75
    floor = np.ones((100, 100), dtype=bool)
    masks = {"floor": floor, "obstacle": ~floor, "uncertain": ~floor}
    np.savez_compressed(
        tmp_path / "maps.npz", **masks, labels=np.full((100, 100), 3, dtype=np.uint8)
    )
    with pytest.raises(ValueError, match="replay_semantic_pair"):
        replay(manifest, params, tmp_path / "out.jsonl")
    np.savez_compressed(
        tmp_path / "maps.npz",
        **masks,
        labels=np.full((100, 100), 3, dtype=np.uint8),
        confidence=np.full((100, 100), np.nan, dtype=np.float16),
    )
    with pytest.raises(ValueError, match="navigation_semantic_maps"):
        replay(manifest, params, tmp_path / "out.jsonl")
