"""Whole-film analysis reads immutable ranges, with mocked models and no hardware IO."""

import hashlib
import io
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PIL import Image

from venom_module_rider import analysis_worker
from venom_module_rider.analysis_inputs import (
    MAX_VIDEO_FRAMES,
    VideoInputs,
    freeze_video,
    inputs_for,
    payload_for,
)
from venom_module_rider.analysis_metrics import StreamingMetrics, adjacent, summarize
from venom_module_rider.analysis_store import AnalysisStore
from venom_module_rider.errors import RiderError
from venom_module_rider.samples_archive import canonical
from venom_module_rider.samples_store import SampleStore


def video_fixture(tmp_path, count=3, *, textured=False, sequences=None, times=None):
    store = SampleStore(tmp_path)
    session = store.start("record", "history", {"record_video": True})
    sid = session["session_id"]
    store.video.start(sid)
    image = Image.new("RGB", (64, 48), "black")
    if textured:
        for y in range(48):
            for x in range(64):
                image.putpixel(
                    (x, y), ((x * 9) % 255, (y * 11) % 255, ((x + y) * 7) % 255)
                )
    stream = io.BytesIO()
    image.save(stream, "JPEG")
    payload = stream.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    lines = []
    for index in range(count):
        sequence = sequences[index] if sequences is not None else index + 1
        received = times[index] if times is not None else index * 0.2
        previous = (
            times[index - 1] if times is not None and index else (index - 1) * 0.2
        )
        lines.append(
            canonical(
                {
                    "index": index,
                    "offset": index * len(payload),
                    "bytes": len(payload),
                    "sha256": digest,
                    "received_monotonic_s": received,
                    "received_at": 1000 + received,
                    "gap_before_s": None if not index else received - previous,
                    "sequence_gap": None
                    if not index or sequences is None
                    else sequences[index] - sequences[index - 1] - 1,
                    "context": {
                        "frame_id": f"camera-{sequence}",
                        "sequence": sequence,
                        "source_session": "camera-a",
                    },
                }
            )
            + b"\n"
        )
    store.video.path(sid, "capture.mjpeg").write_bytes(payload * count)
    store.video.path(sid, "frames.jsonl").write_bytes(b"".join(lines))
    meta = store.video.summary(sid)
    meta.update(
        frames=count,
        bytes=count * len(payload),
        index_bytes=sum(map(len, lines)),
        state="complete",
        ended_at=1001,
    )
    store.video._save(sid, meta)
    store.end(sid)
    return store, sid, payload


def create(store, sid, request="one", **spec):
    return AnalysisStore(store).create(
        request,
        sid,
        {"input_source": "video", **spec},
        {"id": "da2-default", "files": {}},
        {"sha256": "code"},
    )


def test_full_video_exceeds_old_limits_without_extraction_or_large_snapshot(tmp_path):
    store, sid, payload = video_fixture(tmp_path, count=1301)
    before = {
        str(path.relative_to(store.root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in store.video.folder(sid).iterdir()
    }
    first = create(store, sid, offset=999, count=1)
    second = create(
        store,
        sid,
        "changed",
        perception={"engine": "depth_plane", "residual_limit": 0.08},
    )
    assert first["total"] == 1301
    assert second["total"] == 1301
    assert first["id"] != second["id"]
    assert first["context"]["input_digest"] == second["context"]["input_digest"]
    assert "inputs" not in first["context"]
    assert len(canonical(first["context"])) < 15000
    inputs = inputs_for(store.root, first["context"])
    assert isinstance(inputs, VideoInputs)
    assert len(inputs) == 1301
    sample = inputs[1200]
    assert sample["source_index"] == 1200
    assert sample["source_time_s"] == 240
    assert sample["sequence"] == sample["source_sequence"] == 1201
    assert payload_for(store.root, sample) == payload
    assert list(store.blobs.iterdir()) == []
    assert before == {
        str(path.relative_to(store.root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in store.video.folder(sid).iterdir()
    }


def test_video_rejects_recording_empty_limit_symlink_and_changed_source(tmp_path):
    store, sid, _ = video_fixture(tmp_path)
    session, descriptor, inputs = freeze_video(store, sid)
    with pytest.raises(IndexError):
        inputs[99]
    with pytest.raises(RiderError, match="analysis_video_frame_limit") as error:
        VideoInputs(store.root, {**descriptor, "frames": MAX_VIDEO_FRAMES + 1})
    assert error.value.status == 413
    source = store.video.path(sid, "capture.mjpeg")
    original = source.read_bytes()
    source.write_bytes(b"bad" + original[3:])
    with pytest.raises(RiderError, match="analysis_video_source_changed"):
        VideoInputs(store.root, descriptor)
    source.write_bytes(original)
    folder = store.video.folder(sid)
    moved = folder.with_name(folder.name + "-actual")
    folder.rename(moved)
    folder.symlink_to(moved, target_is_directory=True)
    with pytest.raises(RiderError):
        VideoInputs(store.root, descriptor)


def test_video_gaps_keep_source_sequence_and_reset_temporal_adjacency(tmp_path):
    store, sid, _ = video_fixture(tmp_path, sequences=[1, 3, 4], times=[0, 0.2, 1.5])
    _, _, inputs = freeze_video(store, sid)
    assert [row["sequence"] for row in inputs] == [1, 3, 4]
    assert not adjacent(inputs[0], inputs[1], "manifest")
    assert not adjacent(inputs[1], inputs[2], "manifest")


def run_mock(row, store, monkeypatch, *, fail_after=None, semantic_factory=None):
    from test_analysis_worker import PlaneModel

    from venom_module_rider import cuda_execution

    monkeypatch.setattr(cuda_execution, "require_cuda", lambda: "fixture-only")
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(is_initialized=lambda: False),
            set_num_threads=lambda value: None,
            set_num_interop_threads=lambda value: None,
        ),
    )
    frames, chunks, events = [], [], []

    def emit(_output, message):
        events.append(message["type"])
        if message["type"] == "frame":
            frames.append(deepcopy(message))
            if fail_after is not None and len(frames) == fail_after:
                raise RuntimeError("cancelled fixture")
        if message["type"] == "rows":
            chunks.append(deepcopy(message["rows"]))
            return {"saved": f"rows-{len(chunks) - 1:05d}.json"}
        if message["type"] == "complete":
            events.append(deepcopy(message["report"]))
        return {"timings_ms": {"persistence": 1.0}}

    monkeypatch.setattr(analysis_worker, "emit", emit)
    PlaneModel.loaded = 0
    analysis_worker.run(
        {
            "id": row["id"],
            "root": str(store.root),
            "context": row["context"],
            "profile": {
                "backend": row["context"]["profile"].get("backend", "da2"),
                "model": str(store.root),
                "resolution": 0,
            },
        },
        None,
        candidate_factory=PlaneModel,
        semantic_factory=semantic_factory,
    )
    return frames, chunks, events[-1]


def test_whole_film_mock_replay_streams_all_rows_and_compact_report(
    tmp_path, monkeypatch
):
    store, sid, _ = video_fixture(tmp_path, count=257)
    row = create(store, sid)
    frames, chunks, report = run_mock(row, store, monkeypatch)
    assert len(frames) == 257
    assert list(map(len, chunks)) == [128, 128, 1]
    assert report["rows"] == []
    assert report["row_chunks"] == [
        "rows-00000.json",
        "rows-00001.json",
        "rows-00002.json",
    ]
    assert report["summary"]["frames"] == 257
    assert report["summary"]["state_counts"] == {
        "clear": 0,
        "unknown": 257,
        "obstacle": 0,
        "rejected": 257,
    }
    assert report["summary"]["false_go"] is None
    assert "not closed-loop" in report["classification_scope"]
    assert chunks[-1][0]["source_index"] == 256
    assert chunks[-1][0]["source_received_at"] == 1051.2
    assert all(not message["artifacts"] for message in frames)
    assert len(canonical(report)) < 20000
    assert list(store.blobs.iterdir()) == []


def test_video_model_outputs_only_classification_layer_no_raw_or_depth_copies(
    tmp_path, monkeypatch
):
    store, sid, payload = video_fixture(tmp_path, count=2, textured=True)
    row = create(store, sid)
    frames, chunks, report = run_mock(row, store, monkeypatch)
    assert report["summary"]["frames"] == 2
    assert all(len(message["artifacts"]) == 1 for message in frames)
    assert [message["artifacts"][0]["name"] for message in frames] == [
        "0000-mask.png",
        "0001-mask.png",
    ]
    assert chunks[0][0]["raw_sha256"] == hashlib.sha256(payload).hexdigest()
    assert list(store.blobs.iterdir()) == []


def test_worker_stops_mid_film_without_emitting_success(tmp_path, monkeypatch):
    store, sid, _ = video_fixture(tmp_path, count=257)
    row = create(store, sid)
    with pytest.raises(RuntimeError, match="cancelled fixture"):
        run_mock(row, store, monkeypatch, fail_after=130)
    assert list(store.blobs.iterdir()) == []


def test_streaming_metrics_equal_original_metrics_for_labelled_samples():
    samples = []
    rows = []
    for index in range(7):
        samples.append(
            {
                "sample_id": str(index),
                "raw": {"sha256": str(index)},
                "labels": [],
                "sequence": index,
                "run_id": "r",
                "source_device_id": "d",
                "frame_id": str(index),
            }
        )
        rows.append(
            {
                "sample_id": str(index),
                "raw_sha256": str(index),
                "state": "unknown" if index % 2 else "clear",
                "timings_ms": {"decode": index + 1},
            }
        )
    stream = StreamingMetrics("receipt")
    for sample, row in zip(samples, rows, strict=True):
        stream.add(sample, row)
    result = stream.finish()
    assert {
        key: value for key, value in result.items() if key != "state_counts"
    } == summarize(samples, rows, "receipt")


def test_active_and_zero_frame_video_are_explicit_errors(tmp_path):
    store, sid, _ = video_fixture(tmp_path)
    with store.db() as db:
        db.execute("UPDATE sessions SET state='collecting' WHERE id=?", (sid,))
    with pytest.raises(RiderError, match="analysis_video_recording"):
        freeze_video(store, sid)
    store.end(sid)
    meta = store.video.summary(sid)
    meta["frames"] = 0
    store.video._save(sid, meta)
    with pytest.raises(RiderError, match="analysis_empty_video"):
        freeze_video(store, sid)


def test_semantic_video_uses_private_evidence_once_without_changing_classification(
    tmp_path, monkeypatch
):
    import numpy as np

    from venom_module_rider.perception_catalog import SEGFORMER

    store, sid, _ = video_fixture(tmp_path, count=2, textured=True)
    row = AnalysisStore(store).create(
        "semantic",
        sid,
        {"input_source": "video", "profile": SEGFORMER},
        {
            "id": SEGFORMER,
            "backend": "semantic",
            "files": {},
            "model_sha256": "fixture",
        },
        {},
    )
    calls = []

    class SemanticModel:
        metadata = {"backend": "fixture-semantic"}

        def __init__(self, *args):
            pass

        def detect(self, payload, corridor, *, evidence=False, semantic=None):
            calls.append(evidence)
            floor = np.ones((48, 64), dtype=bool)
            floor[:, 32:] = False
            labels = np.full((48, 64), 3, dtype=np.uint8)
            labels[:, 32:] = 28
            result = {
                "state": "clear",
                "reason": "fixture-semantic",
                "floor_fraction": 0.5,
            }
            if evidence:
                result["_navigation_maps"] = {
                    "floor": floor,
                    "obstacle": ~floor,
                    "labels": labels,
                    "gray": np.zeros_like(labels),
                    "confidence": np.ones_like(labels, dtype=np.float32),
                }
                result["_object_maps"] = {"gray": np.zeros_like(labels)}
            return result, b"unused RGB preview"

    frames, chunks, report = run_mock(
        row, store, monkeypatch, semantic_factory=SemanticModel
    )
    assert calls == [True, True]
    assert [item["row"]["state"] for item in frames] == ["clear", "clear"]
    assert report["summary"]["state_counts"]["clear"] == 2
    assert all(item["artifacts"][0]["name"].endswith("-mask.png") for item in frames)
    for chunk in chunks:
        canonical(chunk)  # Arrays/private maps would fail serialization.
        assert all(not any(key.startswith("_") for key in value) for value in chunk)


def test_video_worker_never_allows_persisted_gray_or_npz_evidence(
    tmp_path, monkeypatch
):
    store, sid, _ = video_fixture(tmp_path)
    row = create(store, sid)
    row["context"]["spec"]["capture_evidence"] = True
    with pytest.raises(ValueError, match="analysis_video_evidence_unsupported"):
        run_mock(row, store, monkeypatch)
