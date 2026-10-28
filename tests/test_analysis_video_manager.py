"""Full manager protocol commits complete video chunks, never partial success."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from test_analysis_video import video_fixture

from venom_module_rider.analysis_contract import AnalysisSpec
from venom_module_rider.analysis_inputs import inputs_for
from venom_module_rider.analysis_manager import AnalysisManager
from venom_module_rider.analysis_metrics import StreamingMetrics
from venom_module_rider.errors import RiderError
from venom_module_rider.samples_archive import canonical
from venom_module_rider.samples_registry import SamplesRegistry


async def prepared(tmp_path):
    store, sid, _ = video_fixture(tmp_path, count=257)
    registry = SamplesRegistry(store.root, store=store)
    manager = AnalysisManager(registry)
    row = manager.store.create(
        "run",
        sid,
        {"input_source": "video"},
        {"id": "da2-default", "files": {}},
        {"sha256": "test"},
    )
    assert manager.store.running(row["id"])
    row["queue_ms"] = 0
    inputs = inputs_for(store.root, row["context"])

    def messages():
        chunk = []
        chunks = []
        metrics = StreamingMetrics("manifest")
        for index, sample in enumerate(inputs):
            result = {
                "sample_id": sample["sample_id"],
                "raw_sha256": sample["raw"]["sha256"],
                "source_index": index,
                "source_time_s": sample["source_time_s"],
                "state": "unknown",
                "rejected_input": True,
                "timings_ms": {},
                "artifact_names": [],
            }
            yield {
                "type": "frame",
                "done": index + 1,
                "row": result,
                "artifacts": [],
                "sent_mono": time.perf_counter(),
            }
            metrics.add(sample, result)
            chunk.append(result)
            if len(chunk) == 128 or index + 1 == len(inputs):
                yield {"type": "rows", "done": index + 1, "rows": chunk}
                chunks.append(f"rows-{len(chunks):05d}.json")
                chunk = []
        yield {
            "type": "complete",
            "report": {
                "run_id": row["id"],
                "input_digest": row["context"]["input_digest"],
                "rows": [],
                "row_chunks": chunks,
                "summary": metrics.finish(),
                "input_kind": "video",
            },
        }

    manager.priority = SimpleNamespace(
        phase_io=lambda process: None, phase_compute=lambda process: True
    )

    async def send(process, message):
        pass

    manager._send = send
    return manager, row, messages


class Reader:
    def __init__(self, messages):
        self.messages = iter(messages)

    async def readline(self):
        await asyncio.sleep(0)
        try:
            return canonical(next(self.messages)) + b"\n"
        except StopIteration:
            return b""


async def test_protocol_commits_every_chunk_progress_and_final_report(tmp_path):
    manager, row, messages = await prepared(tmp_path)
    try:
        await manager._protocol(
            SimpleNamespace(stdout=Reader(messages())), row, time.monotonic()
        )
        final = manager.store.finish(row["id"], "succeeded")
        assert final["done"] == final["total"] == 257
        assert final["state"] == "succeeded"
        report = json.loads(manager.store.artifact(row["id"], "report.json")[0])
        chunks = [
            json.loads(manager.store.artifact(row["id"], name)[0])
            for name in report["row_chunks"]
        ]
        assert list(map(len, chunks)) == [128, 128, 1]
        assert [item["source_index"] for chunk in chunks for item in chunk] == list(
            range(257)
        )
        assert list(manager.registry.store.blobs.iterdir()) == []
    finally:
        await manager.close()
        await manager.registry.close()


@pytest.mark.parametrize(
    "fault", ["missing_chunk", "changed_identity", "changed_source", "source_bytes"]
)
async def test_protocol_rejects_missing_or_mismatched_video_results(tmp_path, fault):
    manager, row, messages = await prepared(tmp_path)
    if fault == "source_bytes":
        path = manager.registry.store.video.path(row["session_id"], "capture.mjpeg")
        path.write_bytes(b"changed-source")

    def damaged():
        for message in messages():
            if fault == "missing_chunk" and message["type"] == "rows":
                continue
            if fault == "changed_identity" and message["type"] == "rows":
                message["rows"][0]["source_index"] = 9999
            if (
                fault == "changed_source"
                and message["type"] == "frame"
                and message["done"] == 2
            ):
                message["row"]["raw_sha256"] = "0" * 64
            yield message

    try:
        with pytest.raises(RiderError):
            await manager._protocol(
                SimpleNamespace(stdout=Reader(damaged())), row, time.monotonic()
            )
        assert manager.store.get(row["id"])["state"] != "succeeded"
        assert "report.json" not in {
            item["name"] for item in manager.store.artifacts(row["id"])
        }
    finally:
        await manager.close()
        await manager.registry.close()


async def test_video_cancellation_stops_mid_protocol_without_complete_report(tmp_path):
    manager, row, messages = await prepared(tmp_path)

    async def protocol():
        await manager._protocol(
            SimpleNamespace(stdout=Reader(messages())), row, time.monotonic()
        )

    handle = asyncio.create_task(protocol())
    manager.tasks[row["id"]] = handle
    manager.started.add(row["id"])
    try:
        while manager.store.get(row["id"])["done"] < 3:
            await asyncio.sleep(0.001)
        await manager.cancel(row["id"])
        with pytest.raises(asyncio.CancelledError):
            await handle
        final = manager.store.finish(row["id"], "cancelled")
        assert final["done"] < final["total"]
        assert final["state"] == "cancelled"
        assert "report.json" not in {
            item["name"] for item in manager.store.artifacts(row["id"])
        }
    finally:
        manager.tasks.clear()
        manager.started.clear()
        await manager.close()
        await manager.registry.close()


def test_legacy_sample_fingerprint_omits_new_default_source_field(tmp_path):
    from test_analysis_store import rig

    samples, sid, store = rig(tmp_path)
    old = store.create("old", sid, {}, {}, {})
    spec = AnalysisSpec().model_dump(mode="json")
    assert "input_source" not in old["context"]["spec"]
    again = store.create("old", sid, spec, {}, {})
    assert again["id"] == old["id"]


async def test_real_worker_layers_commit_without_source_rgb_duplicates(
    tmp_path, monkeypatch
):
    from test_analysis_video import run_mock

    store, sid, _ = video_fixture(tmp_path, count=2, textured=True)
    registry = SamplesRegistry(store.root, store=store)
    manager = AnalysisManager(registry)
    row = manager.store.create(
        "layer-run",
        sid,
        {"input_source": "video"},
        {"id": "da2-default", "files": {}},
        {},
    )
    manager.store.running(row["id"])
    row["queue_ms"] = 0
    frames, chunks, report = run_mock(row, store, monkeypatch)
    messages = []
    for frame in frames:
        frame["sent_mono"] = time.perf_counter()
        messages.append(frame)
    messages.append({"type": "rows", "done": 2, "rows": chunks[0]})
    messages.append({"type": "complete", "report": report})
    manager.priority = SimpleNamespace(
        phase_io=lambda process: None, phase_compute=lambda process: True
    )

    async def send(process, message):
        pass

    manager._send = send
    try:
        await manager._protocol(
            SimpleNamespace(stdout=Reader(messages)), row, time.monotonic()
        )
        final = manager.store.finish(row["id"], "succeeded")
        assert final["state"] == "succeeded"
        assert final["done"] == 2
        names = {item["name"] for item in manager.store.artifacts(row["id"])}
        assert names == {
            "0000-mask.png",
            "0001-mask.png",
            "rows-00000.json",
            "report.json",
        }
        assert list(store.blobs.iterdir()) == []
        assert len(store.video.path(sid, "capture.mjpeg").read_bytes()) > 0
    finally:
        await manager.close()
        await registry.close()
