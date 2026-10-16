"""Analysis lifecycle and artifacts use the same real SQLite/source registry."""

import json
from dataclasses import replace

import pytest
from pydantic import ValidationError
from test_samples_store import frame, save, setup

from venom_module_rider import analysis_store
from venom_module_rider.analysis_contract import AnalysisSpec
from venom_module_rider.analysis_store import AnalysisStore
from venom_module_rider.errors import RiderError


def rig(tmp_path):
    samples, session = setup(tmp_path)
    save(samples, session, frame("white", context={"frame_id": "first"}), "one")
    save(samples, session, frame("black", context={"frame_id": "second"}), "two")
    return samples, session, AnalysisStore(samples)


def create(store, session, request="request", **values):
    return store.create(
        request, session, values, {"fingerprint": "model-a"}, {"sha256": "code-a"}
    )


def complete(store, row):
    identity = row["id"]
    assert store.running(identity)
    store.progress(identity, row["total"], "saving")
    report = {
        "run_id": identity,
        "input_digest": row["context"]["input_digest"],
        "summary": {"frames": row["total"]},
    }
    store.write(
        identity, "report.json", json.dumps(report).encode(), "application/json"
    )
    return store.finish(identity, "succeeded", result=report["summary"])


def test_runs_freeze_labels_sources_and_intent_across_retries(tmp_path):
    samples, session, store = rig(tmp_path)
    first = create(store, session)
    hashes = [row["raw"]["sha256"] for row in samples.samples(session)]
    sample_id = first["context"]["inputs"][0]["sample_id"]
    samples.label(
        sample_id,
        {"expected_state": "clear", "review": "approved"},
        expected_revision=0,
    )
    assert create(store, session) == first
    assert first["context"]["inputs"][0]["labels"] == []
    second = create(store, session, "different-run", corridor=(0.35, 0.82, 0.65, 0.95))
    assert second["id"] != first["id"]
    assert second["context"]["dataset_revision"] != first["context"]["dataset_revision"]
    assert second["context"]["inputs"][0]["labels"][0]["review"] == "approved"
    assert [row["raw"]["sha256"] for row in samples.samples(session)] == hashes
    with pytest.raises(RiderError, match="analysis_request_conflict"):
        create(store, session, count=1)
    assert len(store.list(session)) == 2
    assert "context" not in store.list()[0]


def test_input_window_is_explicit_and_empty_window_does_not_create_run(tmp_path):
    _samples, session, store = rig(tmp_path)
    row = create(store, session, offset=1, count=1)
    assert row["total"] == 1 and row["context"]["source_sample_count"] == 2
    assert row["context"]["inputs"][0]["frame_id"] == "second"
    with pytest.raises(RiderError, match="analysis_empty_window"):
        create(store, session, "empty", offset=2)
    assert len(store.list()) == 1


def test_cancel_is_requested_until_executor_stops_and_cannot_start_cancelled_job(
    tmp_path,
):
    _samples, session, store = rig(tmp_path)
    row = create(store, session)
    identity = row["id"]
    stopped = store.cancel(identity)
    assert stopped["state"] == "queued" and stopped["cancel_requested"]
    assert not store.running(identity)
    final = store.finish(identity, "cancelled")
    assert final["state"] == "cancelled"
    assert store.finish(identity, "failed") == final
    with pytest.raises(RiderError, match="analysis_not_writable"):
        store.write(identity, "partial.png", b"data", "image/png")


def test_success_requires_complete_committed_matching_report(tmp_path):
    samples, session, store = rig(tmp_path)
    row = create(store, session)
    with pytest.raises(RiderError, match="analysis_artifact_not_found"):
        store.finish(row["id"], "succeeded")
    done = complete(store, row)
    assert done["state"] == "succeeded" and done["result"]["frames"] == 2
    assert store.cancel(row["id"])["state"] == "succeeded"
    assert store.artifact(row["id"], "report.json")[1] == "application/json"
    assert store.artifacts(row["id"])[0]["name"] == "report.json"
    assert (
        samples.original(row["context"]["inputs"][0]["sample_id"])[0]
        == frame("white").payload
    )


@pytest.mark.parametrize(
    "bad",
    [
        b"invalid json",
        b"[]",
        b'{"summary":null}',
        b'{"run_id":"another","summary":{"frames":2}}',
    ],
)
def test_invalid_report_never_qualifies_as_completed(tmp_path, bad):
    _samples, session, store = rig(tmp_path)
    row = create(store, session)
    assert store.running(row["id"])
    store.progress(row["id"], row["total"], "saving")
    if bad == b'{"summary":null}':
        bad = json.dumps(
            {
                "run_id": row["id"],
                "input_digest": row["context"]["input_digest"],
                "summary": None,
            }
        ).encode()
    store.write(row["id"], "report.json", bad, "application/json")
    with pytest.raises(RiderError, match="analysis_report_incomplete"):
        store.finish(row["id"], "succeeded")
    assert store.get(row["id"])["state"] == "running"
    assert (
        store.finish(row["id"], "failed", error="bad_report")["error"] == "bad_report"
    )


def test_restart_marks_only_interrupted_runs_and_keeps_previous_output(tmp_path):
    samples, session, store = rig(tmp_path)
    done = complete(store, create(store, session))
    queued = create(store, session, "queued")
    running = create(store, session, "running")
    assert store.running(running["id"])
    reader = AnalysisStore(samples)
    assert reader.get(running["id"])["state"] == "running"
    reader.recover()  # the execution manager calls this only under its process lease
    assert reader.get(queued["id"])["error"] == "analysis_interrupted"
    assert reader.get(running["id"])["state"] == "failed"
    assert reader.get(done["id"])["state"] == "succeeded"
    assert reader.artifact(done["id"], "report.json")


def test_progress_is_monotonic_and_stays_within_actual_input_count(tmp_path):
    _samples, session, store = rig(tmp_path)
    row = create(store, session)
    store.progress(row["id"], 0, "waiting_live")
    assert store.get(row["id"])["state"] == "queued"
    assert store.running(row["id"])
    assert not store.running(row["id"])
    store.progress(row["id"], 1, "inference")
    for done in [0, 3]:
        with pytest.raises(RiderError, match="analysis_progress_invalid"):
            store.progress(row["id"], done, "saving")
    for done, phase in [(True, "saving"), (1, "driving")]:
        with pytest.raises(RiderError, match="analysis_progress_invalid"):
            store.progress(row["id"], done, phase)


def test_artifacts_are_immutable_count_toward_shared_quota_and_detect_corruption(
    tmp_path,
):
    samples, session, store = rig(tmp_path)
    row = create(store, session)
    store.running(row["id"])
    payload = b"depth data" * 10000
    before = samples.usage()
    store.write(row["id"], "0000-depth.npy", payload, "application/octet-stream")
    assert samples.usage() >= before + len(payload)
    with pytest.raises(RiderError, match="analysis_artifact_exists"):
        store.write(
            row["id"], "0000-depth.npy", b"replacement", "application/octet-stream"
        )
    path = store.directory(row["id"]) / "0000-depth.npy"
    path.write_bytes(b"corrupt")
    with pytest.raises(RiderError, match="analysis_artifact_corrupt"):
        store.artifact(row["id"], path.name)


@pytest.mark.parametrize("name", ["../outside", "/absolute", "a/b", "a\\b", "", None])
def test_artifact_paths_never_leave_owned_directory(tmp_path, name):
    _samples, session, store = rig(tmp_path)
    row = create(store, session)
    store.running(row["id"])
    with pytest.raises(RiderError, match="analysis_artifact_path"):
        store.write(row["id"], name, b"data", "image/png")


def test_analysis_quotas_preserve_sources_and_no_uncommitted_run_appears(
    tmp_path, monkeypatch
):
    samples, session, store = rig(tmp_path)
    monkeypatch.setattr(analysis_store, "MAX_SNAPSHOT", 1)
    with pytest.raises(RiderError, match="analysis_snapshot_size"):
        create(store, session)
    assert store.list() == []
    monkeypatch.setattr(analysis_store, "MAX_SNAPSHOT", 8 * 1024**2)
    row = create(store, session)
    store.running(row["id"])
    monkeypatch.setattr(analysis_store, "MAX_RUN_BYTES", 1)
    with pytest.raises(RiderError, match="analysis_artifact_quota"):
        store.write(row["id"], "depth.png", b"data", "image/png")
    samples.limits = replace(samples.limits, total_bytes=1)
    with pytest.raises(RiderError, match="samples_quota_reached"):
        create(store, session, "full")
    assert len(store.list()) == 1 and len(samples.samples(session)) == 2


@pytest.mark.parametrize(
    "values",
    [
        {"corridor": (0.8, 0.8, 0.2, 0.9)},
        {"count": 201},
        {"threads": 3},
        {"quality_mode": "drive"},
        {"perception": {"engine": "segformer"}},
        {"corridor": (float("nan"), 0.8, 0.6, 0.9)},
    ],
)
def test_analysis_rejects_unbounded_or_actuator_like_parameters(values):
    with pytest.raises(ValidationError):
        AnalysisSpec.model_validate(values)


@pytest.mark.parametrize("window", [True, "3", 2, 0])
def test_window_is_an_explicit_supported_view_count(window):
    with pytest.raises(ValidationError):
        AnalysisSpec.model_validate({"window": window})


def test_artifact_format_size_and_run_count_limits_are_explicit(tmp_path, monkeypatch):
    samples, session, store = rig(tmp_path)
    monkeypatch.setattr(analysis_store, "MAX_RUNS", 1)
    row = create(store, session)
    with pytest.raises(RiderError, match="analysis_run_limit"):
        create(store, session, "too-many")
    store.running(row["id"])
    monkeypatch.setattr(analysis_store, "MAX_ARTIFACT_BYTES", 2)
    with pytest.raises(RiderError, match="analysis_artifact_size"):
        store.write(row["id"], "large.png", b"more than two bytes", "image/png")
    with pytest.raises(RiderError, match="analysis_artifact_format"):
        store.write(row["id"], "html", b"x", "text/html")
    assert store.artifacts(row["id"]) == [] and len(samples.samples(session)) == 2


@pytest.mark.parametrize("field", ["input_digest", "summary"])
def test_matching_report_must_bind_its_input_hashes_and_complete_count(tmp_path, field):
    _samples, session, store = rig(tmp_path)
    row = create(store, session)
    store.running(row["id"])
    store.progress(row["id"], row["total"], "saving")
    report = {
        "run_id": row["id"],
        "input_digest": row["context"]["input_digest"],
        "summary": {"frames": row["total"]},
    }
    report[field] = "different" if field == "input_digest" else {"frames": 1}
    store.write(
        row["id"], "report.json", json.dumps(report).encode(), "application/json"
    )
    with pytest.raises(RiderError, match="analysis_report_incomplete"):
        store.finish(row["id"], "succeeded")


def test_run_directory_ids_are_not_filesystem_paths(tmp_path):
    _samples, _session, store = rig(tmp_path)
    for value in ["../outside", "a" * 31, None]:
        with pytest.raises(RiderError, match="analysis_invalid_id"):
            store.get(value)
