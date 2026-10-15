"""Exact capture integrity, bounded queues/storage and fail-open diagnostics."""

import hashlib
import io
import json
import threading
import zipfile

import numpy as np
import pytest

from venom_module_rider import depth_trace
from venom_module_rider.depth_detector import PerceptionSettings
from venom_module_rider.depth_trace import DepthTraceRecorder

CORRIDOR = (0.35, 0.8, 0.65, 0.95)
SETTINGS = PerceptionSettings(engine="depth_plane")
PAYLOAD = b"\xff\xd8\xffexact-encoded-camera-input"


def assessment(state="clear"):
    return {
        "state": state,
        "model_sha256": "a" * 64,
        "run_id": "run-a",
        "frame_id": "frame-a",
        "sequence": 7,
        "config_revision": 15,
        "_preview": b"not-the-input",
        "_errors": np.ones((48, 64)),
    }


@pytest.fixture
def recorder(tmp_path):
    result = DepthTraceRecorder(tmp_path)
    yield result
    result.close()


def load_capture(path):
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == {"frame.bin", "depth.npz", "manifest.json"}
        metadata = json.loads(archive.read("manifest.json"))
        payload = archive.read("frame.bin")
        with np.load(io.BytesIO(archive.read("depth.npz")), allow_pickle=False) as maps:
            depth = maps["depth"]
    return metadata, payload, depth


def test_disabled_recorder_has_no_worker_or_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("RIDER_DEPTH_TRACE_DIR", raising=False)
    monkeypatch.setattr(depth_trace, "_recorder", None)
    assert (
        depth_trace.submit(PAYLOAD, np.ones((48, 64)), assessment(), CORRIDOR, SETTINGS)
        is None
    )
    assert depth_trace._recorder is None
    assert list(tmp_path.iterdir()) == []


def test_module_api_lazily_uses_opt_in_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("RIDER_DEPTH_TRACE_DIR", str(tmp_path / "captures"))
    monkeypatch.setattr(depth_trace, "_recorder", None)
    try:
        trace_id = depth_trace.submit(
            PAYLOAD, np.ones((48, 64)), assessment(), CORRIDOR, SETTINGS
        )
        assert trace_id is not None
        result = depth_trace._recorder
        assert result is not None
        result.close()
        metadata, _, _ = load_capture(next((tmp_path / "captures").glob("*.zip")))
        assert metadata["trace_id"] == trace_id
    finally:
        if depth_trace._recorder is not None:
            depth_trace._recorder.close()


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_capture_is_exact_immutable_and_fully_correlated(recorder, monkeypatch, dtype):
    started, release = threading.Event(), threading.Event()
    original = recorder._persist

    def wait_then_persist(capture):
        started.set()
        assert release.wait(2)
        original(capture)

    monkeypatch.setattr(recorder, "_persist", wait_then_persist)
    source = np.linspace(0.1, 20, 48 * 64, dtype=dtype).reshape(48, 64)
    expected = source.copy()
    metadata_input = assessment()
    payload = bytearray(PAYLOAD)
    trace_id = recorder.submit(payload, source, metadata_input, CORRIDOR, SETTINGS)
    assert trace_id is not None and started.wait(2)
    source[:] = 0
    payload[:] = b"x" * len(payload)
    metadata_input["run_id"] = "changed-after-submit"
    release.set()
    recorder.close()
    metadata, saved_payload, saved_depth = load_capture(
        next(recorder.directory.glob("*.zip"))
    )
    assert metadata["trace_id"] == trace_id
    assert metadata["assessment"]["run_id"] == "run-a"
    assert metadata["assessment"]["frame_id"] == "frame-a"
    assert metadata["assessment"]["sequence"] == 7
    assert metadata["assessment"]["config_revision"] == 15
    assert metadata["assessment"]["model_sha256"] == "a" * 64
    assert metadata["settings"] == SETTINGS.model_dump(mode="json")
    assert metadata["corridor"] == list(CORRIDOR)
    assert not any(k.startswith("_") for k in metadata["assessment"])
    assert saved_payload == PAYLOAD
    np.testing.assert_array_equal(saved_depth, expected)
    assert saved_depth.dtype == dtype
    assert metadata["depth"]["sha256"] == hashlib.sha256(expected.tobytes()).hexdigest()
    assert metadata["raw"]["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()
    assert metadata["raw"]["bytes"] == len(PAYLOAD)
    assert metadata["depth"]["shape"] == [48, 64]
    assert metadata["depth"]["dtype"] == str(np.dtype(dtype))
    assert not list(recorder.directory.glob("*.tmp"))


@pytest.mark.parametrize(
    "change",
    [
        {"payload": b""},
        {"payload": b"x" * (depth_trace.MAX_RAW_BYTES + 1)},
        {"depth": np.ones((721, 32))},
        {"depth": np.ones((32, 1281))},
        {"depth": np.ones((31, 32))},
        {"depth": np.ones((48, 64, 1))},
        {"depth": np.full((48, 64), np.nan)},
        {"depth": np.zeros((48, 64))},
        {"depth": np.ones((48, 64), dtype=np.int64)},
        {"assessment": {"state": "unknown"}},
        {
            "assessment": {
                "state": "clear",
                "large": "x" * depth_trace.MAX_METADATA_BYTES,
            }
        },
        {"assessment": {"state": "clear", "invalid": object()}},
        {"corridor": (0.8, 0.8, 0.3, 0.95)},
        {"corridor": (0.35, float("inf"), 0.65, 0.95)},
        {"corridor": (0.35, 0.8)},
    ],
    ids=[
        "empty",
        "raw-large",
        "height",
        "width",
        "small",
        "ndim",
        "nan",
        "zero",
        "dtype",
        "unknown",
        "metadata-large",
        "metadata-invalid",
        "roi-order",
        "roi-inf",
        "roi-size",
    ],
)
def test_invalid_or_unbounded_inputs_never_start_worker(recorder, change):
    args = dict(
        payload=PAYLOAD,
        depth=np.ones((48, 64)),
        assessment=assessment(),
        corridor=CORRIDOR,
        settings=SETTINGS,
    )
    args.update(change)
    assert recorder.submit(**args) is None
    assert recorder._thread is None
    assert list(recorder.directory.iterdir()) == []


def test_clear_rate_limit_does_not_suppress_alarm(recorder):
    trace_id = recorder.submit(
        PAYLOAD, np.ones((48, 64)), assessment(), CORRIDOR, SETTINGS
    )
    assert trace_id is not None
    recorder._queue.join()
    assert (
        recorder.submit(PAYLOAD, np.ones((48, 64)), assessment(), CORRIDOR, SETTINGS)
        is None
    )
    assert (
        recorder.submit(
            PAYLOAD, np.ones((48, 64)), assessment("obstacle"), CORRIDOR, SETTINGS
        )
        is not None
    )
    recorder.close()
    assert len(list(recorder.directory.glob("clear-*.zip"))) == 1
    assert len(list(recorder.directory.glob("alarm-*.zip"))) == 1


def test_queue_is_bounded_alarm_preempts_pending_capture(recorder, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = recorder._persist
    now = [0.0]
    recorder.clock = lambda: now[0]

    def blocked(capture):
        started.set()
        assert release.wait(2)
        original(capture)

    monkeypatch.setattr(recorder, "_persist", blocked)
    first = recorder.submit(
        PAYLOAD, np.ones((48, 64)), assessment(), CORRIDOR, SETTINGS
    )
    assert started.wait(2)
    for index in [1, 2]:
        now[0] = float(index)
        assert recorder.submit(
            PAYLOAD, np.ones((48, 64)), assessment(), CORRIDOR, SETTINGS
        )
    assert recorder._queue.qsize() == 2
    now[0] = 3.0
    assert (
        recorder.submit(PAYLOAD, np.ones((48, 64)), assessment(), CORRIDOR, SETTINGS)
        is None
    )
    alarm = recorder.submit(
        PAYLOAD, np.ones((48, 64)), assessment("obstacle"), CORRIDOR, SETTINGS
    )
    assert alarm and recorder._queue.qsize() == 2
    release.set()
    recorder.close()
    ids = {load_capture(p)[0]["trace_id"] for p in recorder.directory.glob("*.zip")}
    assert alarm in ids and first in ids
    assert len(ids) == 3


def test_storage_has_sixteen_fixed_slots_and_alarm_retention(recorder):
    now = [0.0]
    recorder.clock = lambda: now[0]
    for state, count in [("obstacle", 15), ("clear", 7)]:
        for _ in range(count):
            now[0] += 2
            assert recorder.submit(
                PAYLOAD, np.ones((48, 64)), assessment(state), CORRIDOR, SETTINGS
            )
            recorder._queue.join()
    recorder.close()
    assert len(list(recorder.directory.glob("alarm-*.zip"))) == 12
    assert len(list(recorder.directory.glob("clear-*.zip"))) == 4
    assert len(list(recorder.directory.iterdir())) == 16
    assert all(
        p.stat().st_size <= depth_trace.MAX_ARCHIVE_BYTES
        for p in recorder.directory.iterdir()
    )


def test_writer_error_isolated_and_later_capture_survives(recorder, monkeypatch):
    original = recorder._persist
    monkeypatch.setattr(
        recorder, "_persist", lambda capture: (_ for _ in ()).throw(OSError("full"))
    )
    assert recorder.submit(
        PAYLOAD, np.ones((48, 64)), assessment("obstacle"), CORRIDOR, SETTINGS
    )
    recorder._queue.join()
    assert recorder.failures == 1 and recorder.last_error == "OSError"
    monkeypatch.setattr(recorder, "_persist", original)
    assert recorder.submit(
        PAYLOAD, np.ones((48, 64)), assessment("obstacle"), CORRIDOR, SETTINGS
    )
    recorder.close()
    assert len(list(recorder.directory.glob("*.zip"))) == 1
    assert (
        recorder.submit(PAYLOAD, np.ones((48, 64)), assessment(), CORRIDOR, SETTINGS)
        is None
    )


def test_failed_replace_removes_temporary_archive(recorder, monkeypatch):
    monkeypatch.setattr(
        depth_trace.os,
        "replace",
        lambda *args: (_ for _ in ()).throw(OSError("blocked")),
    )
    assert recorder.submit(
        PAYLOAD, np.ones((48, 64)), assessment("obstacle"), CORRIDOR, SETTINGS
    )
    recorder.close()
    assert recorder.failures == 1
    assert list(recorder.directory.iterdir()) == []


def test_archive_limit_is_enforced_before_filesystem_write(recorder, monkeypatch):
    monkeypatch.setattr(depth_trace, "MAX_ARCHIVE_BYTES", 1)
    assert recorder.submit(
        PAYLOAD, np.ones((48, 64)), assessment("obstacle"), CORRIDOR, SETTINGS
    )
    recorder.close()
    assert recorder.failures == 1 and recorder.last_error == "ValueError"
    assert list(recorder.directory.iterdir()) == []


def test_busy_submit_drops_without_waiting(recorder):
    with recorder._lock:
        assert (
            recorder.submit(
                PAYLOAD, np.ones((48, 64)), assessment(), CORRIDOR, SETTINGS
            )
            is None
        )
    assert recorder._thread is None


def test_alarm_eviction_preserves_queued_alarm_ahead_of_clear(recorder, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = recorder._persist
    now = [0.0]
    recorder.clock = lambda: now[0]

    def blocked(capture):
        started.set()
        assert release.wait(2)
        original(capture)

    monkeypatch.setattr(recorder, "_persist", blocked)
    assert recorder.submit(PAYLOAD, np.ones((48, 64)), assessment(), CORRIDOR, SETTINGS)
    assert started.wait(2)
    first_alarm = recorder.submit(
        PAYLOAD, np.ones((48, 64)), assessment("obstacle"), CORRIDOR, SETTINGS
    )
    now[0] = 1.0
    dropped_clear = recorder.submit(
        PAYLOAD, np.ones((48, 64)), assessment(), CORRIDOR, SETTINGS
    )
    next_alarm = recorder.submit(
        PAYLOAD, np.ones((48, 64)), assessment("obstacle"), CORRIDOR, SETTINGS
    )
    release.set()
    recorder.close()
    ids = {load_capture(p)[0]["trace_id"] for p in recorder.directory.glob("*.zip")}
    assert first_alarm in ids and next_alarm in ids
    assert dropped_clear not in ids
