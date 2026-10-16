"""A report may only score what the archived labels and sequence establish."""

from copy import deepcopy

import pytest

from venom_module_rider.analysis_metrics import (
    adjacent,
    approved_label,
    percentile,
    summarize,
)


def inputs():
    return [
        {
            "sample_id": str(i),
            "raw": {"sha256": str(i) * 64},
            "run_id": "run",
            "sequence": i,
            "frame_id": "frame-" + str(i),
            "source_device_id": "robot",
            "labels": [],
        }
        for i in range(3)
    ]


def output(samples, states):
    return [
        {
            "sample_id": sample["sample_id"],
            "raw_sha256": sample["raw"]["sha256"],
            "state": state,
            "obstacle_side": "unknown",
            "timings_ms": {"inference": float(i)},
        }
        for i, (sample, state) in enumerate(zip(samples, states, strict=True))
    ]


def label(sample, expected="clear", side="none", review="approved", author="operator"):
    sample["labels"].append(
        {
            "expected_state": expected,
            "obstacle_side": side,
            "review": review,
            "author": {"kind": author},
        }
    )


def test_missing_truth_is_na_not_perfect_safety_or_accuracy():
    samples = inputs()
    rows = output(samples, ["clear", "obstacle", "unknown"])
    result = summarize(samples, rows, "filename-unverified")
    assert result["false_go"] is None and result["false_stop"] is None
    assert result["side_errors"] is None and result["confusion"] is None
    assert result["approved_labels"] == 0 and result["unlabelled_or_unreviewed"] == 3
    assert result["temporal_pairs"] == 0 and result["flicker_rate"] is None
    assert result["unknown"] == 1
    assert result["historical_transport_ms"] is None
    assert result["physical_motion_ms"] is None
    assert result["drive_supported"] is False


def test_only_clear_truth_cannot_claim_zero_false_go_for_obstacles():
    samples = inputs()
    label(samples[0])
    result = summarize(
        samples, output(samples, ["unknown", "clear", "clear"]), "receipt"
    )
    assert result["false_go"] is None and result["false_go_rate"] is None
    assert result["false_stop"] == 1 and result["false_stop_rate"] == 1
    assert result["clear_labels"] == 1 and result["obstacle_labels"] == 0


def test_only_obstacle_truth_cannot_claim_zero_false_stop_for_floor():
    samples = inputs()
    label(samples[0], "obstacle", "left")
    result = summarize(samples, output(samples, ["clear", "clear", "clear"]), "receipt")
    assert result["false_stop"] is None
    assert result["false_go"] == 1 and result["false_go_rate"] == 1
    assert result["side_labelled"] == 1 and result["side_errors"] == 1


def test_predictions_and_unreviewed_revisions_never_become_operator_truth():
    samples = inputs()
    label(samples[0], author="auto")  # malformed external label still cannot qualify
    label(samples[1], review="pending")
    label(samples[2])
    label(samples[2], review="pending")
    assert all(approved_label(row) is None for row in samples)
    result = summarize(samples, output(samples, ["clear"] * 3), "receipt")
    assert result["eligible_labels"] == 0 and result["false_go"] is None


def test_unknown_approved_truth_is_unscored_and_correct_side_is_measured():
    samples = inputs()
    label(samples[0], "unknown")
    label(samples[1], "obstacle", "right")
    rows = output(samples, ["clear", "obstacle", "unknown"])
    rows[1]["obstacle_side"] = "right"
    result = summarize(samples, rows, "receipt")
    assert result["approved_labels"] == 2 and result["eligible_labels"] == 1
    assert result["side_errors"] == 0 and result["false_go"] == 0
    assert result["false_stop"] is None
    assert result["timings_ms"]["inference"] == {"p50": 1.0, "p95": 2.0}


def test_flicker_requires_actual_adjacent_different_source_frames():
    samples = inputs()
    rows = output(samples, ["clear", "obstacle", "obstacle"])
    result = summarize(samples, rows, "receipt")
    assert result["temporal_pairs"] == 2 and result["state_flips"] == 1
    assert result["flicker_rate"] == 0.5
    for changed in [
        {"run_id": "different"},
        {"sequence": 7},
        {"frame_id": samples[0]["frame_id"]},
        {"raw": samples[0]["raw"]},
        {"source_device_id": "other"},
    ]:
        assert not adjacent(samples[0], {**samples[1], **changed}, "receipt")
    assert not adjacent(samples[0], samples[1], "filename-unverified")


def test_metrics_reject_a_different_window_or_source_hash():
    samples = inputs()
    rows = output(samples, ["clear"] * 3)
    with pytest.raises(ValueError, match="inputs_mismatch"):
        summarize(samples, rows[:1], "receipt")
    for field in ["sample_id", "raw_sha256"]:
        wrong = deepcopy(rows)
        wrong[0][field] = "other"
        with pytest.raises(ValueError, match="inputs_mismatch"):
            summarize(samples, wrong, "receipt")


def test_stage_percentiles_keep_missing_as_na_and_measured_zero_as_zero():
    assert percentile([], 0.95) is None
    assert percentile([None, True, float("nan")], 0.95) is None
    assert percentile([0.0, 1.0, 2.0, 3.0], 0.5) == 1.0
    assert percentile([0.0, 1.0, 2.0, 3.0], 0.95) == 3.0
    assert percentile([0.0], 0.95) == 0.0


def test_raw_header_integral_sequence_and_missing_frame_ids_are_distinguished():
    samples = inputs()
    assert adjacent(
        {**samples[0], "sequence": 0.0}, {**samples[1], "sequence": 1.0}, "receipt"
    )
    for value in [True, "0", float("nan"), 0.5, -1.0]:
        assert not adjacent({**samples[0], "sequence": value}, samples[1], "receipt")
    assert not adjacent({**samples[0], "frame_id": None}, samples[1], "receipt")
    assert not adjacent(samples[0], {**samples[1], "frame_id": ""}, "receipt")
