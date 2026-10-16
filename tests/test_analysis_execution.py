"""Real registry, host queue and owned OS workers; no Pi transport in these jobs."""

import asyncio
import io
import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from PIL import Image
from test_analysis_profiles import catalog, da2
from test_rider import domain, host_app
from venom_core.core.models import TaskStatus
from venom_core.core.orchestrator.task_manager import TaskManager
from venom_core.core.queue_manager import QueueManager
from venom_core.core.state_manager import StateManager

from venom_module_rider.analysis_contract import AnalysisSpec
from venom_module_rider.analysis_manager import AnalysisManager
from venom_module_rider.analysis_profiles import ProfileCatalog
from venom_module_rider.analysis_tasks import HostTasks
from venom_module_rider.errors import RiderError
from venom_module_rider.samples_registry import SamplesRegistry

BASE = "/api/v1/rider/analysis"


async def rig(tmp_path, monkeypatch):
    configured, _source = catalog(tmp_path, monkeypatch)
    da2(configured.resolve("da2-default"))
    configured = ProfileCatalog(
        Path(__file__).resolve().parents[1],
        assets_root=configured.assets,
        config=configured.config,
    )
    registry = SamplesRegistry(tmp_path / "store")
    session = await registry.start("collection", "history")
    stream = io.BytesIO()
    Image.new("RGB", (64, 48), "black").save(stream, "PNG")
    key = registry.offer("history", stream.getvalue(), "image/png")
    registry.capture(session["session_id"], "capture", key)
    await registry.end(session["session_id"])
    host = HostTasks.__new__(HostTasks)
    host.state = StateManager(str(tmp_path / "host-state.json"))
    host.queue = TaskManager(QueueManager(host.state))
    manager = AnalysisManager(registry, catalog=configured, host_tasks=host)
    return manager, session["session_id"], host


async def finish(manager, identity):
    async with asyncio.timeout(15):
        while identity in manager.tasks:
            await asyncio.sleep(0.01)
    return await manager.io(manager.store.get, identity)


async def close(manager, host):
    await manager.close()
    await manager.registry.close()
    await host.state.shutdown()


async def test_real_worker_quality_report_idempotency_and_host_task_no_pi(
    tmp_path,
    monkeypatch,
):
    manager, session, host = await rig(tmp_path, monkeypatch)
    try:
        spec = AnalysisSpec().model_dump(mode="json")
        row = await manager.submit("analysis", session, spec)
        final = await finish(manager, row["id"])
        assert final["state"] == "succeeded", final
        assert not manager.children and not manager.registry.writer_owners
        assert manager.registry.lease is None
        task = host.state.get_task(UUID(final["host_task_id"]))
        assert task.status == TaskStatus.COMPLETED
        report = json.loads(manager.store.artifact(row["id"], "report.json")[0])
        assert report["summary"]["rejected_inputs"] == 1
        assert report["summary"]["false_go"] is None
        assert report["model"] is None and report["model_load_ms"] is None
        assert report["rows"][0]["reason"] == "vision_image_quality"
        assert "inference" not in report["rows"][0]["timings_ms"]
        assert report["rows"][0]["timings_ms"]["persistence"] >= 0
        assert report["peak_vram_mib"] == 0
        # A lost response does not rerun; even deleted weights do not break GET/retry.
        manager.catalog.resolve("da2-default").model.joinpath(
            "model.safetensors"
        ).unlink()
        again = await manager.submit("analysis", session, spec)
        assert again["id"] == row["id"] and again["state"] == "succeeded"
        assert len(manager.store.list()) == 1
        with pytest.raises(RiderError, match="analysis_request_conflict"):
            await manager.submit("analysis", session, {**spec, "count": 1})
        with pytest.raises(RiderError, match="analysis_model_unavailable"):
            await manager.submit("new", session, spec)
    finally:
        await close(manager, host)


async def test_queue_pause_cancel_and_simultaneous_source_collection(
    tmp_path, monkeypatch
):
    manager, session, host = await rig(tmp_path, monkeypatch)
    await host.queue.pause()
    try:
        row = await manager.submit(
            "queued", session, AnalysisSpec().model_dump(mode="json")
        )
        for _ in range(100):
            status = await manager.io(manager.store.get, row["id"], context=False)
            if status["phase"] == "waiting_capacity":
                break
            await asyncio.sleep(0.01)
        assert status["state"] == "queued"
        # Same process remains able to collect; another process cannot take writer.
        second = await manager.registry.start("second-collection", "history")
        other = SamplesRegistry(manager.registry.store.root)
        try:
            with pytest.raises(RiderError, match="samples_registry_busy"):
                await other.start("other-host", "history")
            await manager.registry.end(second["session_id"])
            assert manager.registry.lease is not None  # queued analysis still owns IO
        finally:
            await other.close()
        await manager.cancel(row["id"])
        assert (await finish(manager, row["id"]))["state"] == "cancelled"
        assert not manager.children and manager.registry.lease is None
        assert host.queue.active_tasks == {}
    finally:
        await close(manager, host)


async def test_live_priority_wait_cancel_and_durable_restart_mark(
    tmp_path, monkeypatch
):
    manager, session, host = await rig(tmp_path, monkeypatch)
    manager.priority.busy = lambda: True
    try:
        row = await manager.submit(
            "waiting", session, AnalysisSpec().model_dump(mode="json")
        )
        await asyncio.sleep(0.1)
        assert (await manager.io(manager.store.get, row["id"]))[
            "phase"
        ] == "waiting_live"
        assert not manager.children
        await manager.close()
        assert manager.store.get(row["id"])["state"] == "cancelled"
        with pytest.raises(RiderError, match="analysis_closed"):
            await manager.submit(
                "closed", session, AnalysisSpec().model_dump(mode="json")
            )
    finally:
        await close(manager, host)


async def test_worker_source_corruption_fails_without_replacing_original_or_old_report(
    tmp_path,
    monkeypatch,
):
    manager, session, host = await rig(tmp_path, monkeypatch)
    try:
        first = await manager.submit(
            "first", session, AnalysisSpec().model_dump(mode="json")
        )
        assert (await finish(manager, first["id"]))["state"] == "succeeded"
        report_before = manager.store.artifact(first["id"], "report.json")[0]
        original = manager.store.get(first["id"])["context"]["inputs"][0]
        path = manager.registry.store.blobs / (original["raw"]["sha256"] + ".bin")
        path.write_bytes(b"corrupt external source")
        second = await manager.submit(
            "second", session, AnalysisSpec().model_dump(mode="json")
        )
        final = await finish(manager, second["id"])
        assert final["state"] == "failed" and final["error"] == "samples_source_corrupt"
        assert (
            path.read_bytes() == b"corrupt external source"
        )  # never silently repaired
        assert manager.store.artifact(first["id"], "report.json")[0] == report_before
        assert not manager.children
    finally:
        await close(manager, host)


async def test_analysis_api_offline_recovery_compare_cancel_and_guards(
    tmp_path, monkeypatch
):
    manager, session, host = await rig(tmp_path, monkeypatch)
    service, pi = domain()
    app = host_app(service)
    app.state.rider_analysis = manager
    initial_trace = list(pi.trace)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            assert (await client.get(BASE + "/profiles")).status_code == 200
            response = await client.post(
                BASE + "/runs", json={"request_id": "api", "session_id": session}
            )
            assert response.status_code == 202, response.text
            identity = response.json()["id"]
            assert (await finish(manager, identity))["state"] == "succeeded"
            recovered = await client.get(BASE + "/requests/api")
            assert recovered.json()["id"] == identity
            assert "context" not in recovered.json()
            full = await client.get(BASE + "/runs/" + identity + "?context=true")
            assert len(full.json()["context"]["inputs"]) == 1
            assert (await client.get(BASE + "/runs")).json()["runs"][0][
                "id"
            ] == identity
            assert (
                await client.get(BASE + "/runs/" + identity + "/artifacts")
            ).status_code == 200
            report = await client.get(
                BASE + "/runs/" + identity + "/artifacts/report.json"
            )
            assert (
                report.status_code == 200
                and report.headers["cache-control"] == "no-store"
            )
            assert (
                await client.post(BASE + "/runs/" + identity + "/cancel", json={})
            ).json()["state"] == "succeeded"
            compared = await client.get(
                BASE + "/compare", params={"first": identity, "second": identity}
            )
            assert compared.json()["aligned_inputs_and_labels"]
            assert (
                compared.json()["quality_delta_second_minus_first"]["false_go_rate"]
                is None
            )
            assert (
                await client.post(
                    BASE + "/runs",
                    json={
                        "request_id": "bad",
                        "session_id": session,
                        "spec": {"profile": "http://download"},
                    },
                )
            ).status_code in {404, 422}
            assert (
                await client.post(
                    BASE + "/runs",
                    json={"request_id": "cross", "session_id": session},
                    headers={"Origin": "http://foreign.invalid"},
                )
            ).status_code == 403
            assert pi.trace == initial_trace and service.client is None
            assert pi.state == "DISARMED" and not pi.camera_on
        finally:
            await service.close()
            await close(manager, host)


async def test_host_abort_stops_actual_child_and_keeps_sources(tmp_path, monkeypatch):
    manager, session, host = await rig(tmp_path, monkeypatch)
    ready = asyncio.Event()

    async def held(process, row, started):
        ready.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "_protocol", held)
    try:
        row = await manager.submit(
            "abort", session, AnalysisSpec().model_dump(mode="json")
        )
        await asyncio.wait_for(ready.wait(), 5)
        child = manager.children[row["id"]]
        assert Path(f"/proc/{child.pid}").exists()
        result = await host.queue.abort_task(UUID(row["host_task_id"]))
        assert result["success"]
        final = await finish(manager, row["id"])
        assert final["state"] == "cancelled"
        assert child.returncode is not None and not manager.children
        assert manager.registry.store.original(
            final["context"]["inputs"][0]["sample_id"]
        )[0]
        assert manager.registry.lease is None
    finally:
        await close(manager, host)


async def test_lost_http_cancellation_keeps_one_server_owned_admission(
    tmp_path, monkeypatch
):
    manager, session, host = await rig(tmp_path, monkeypatch)
    original = manager.catalog.resolve("da2-default").qualify
    import threading

    release = threading.Event()

    def qualify():
        release.wait(3)
        return original()

    profile = manager.catalog.resolve("da2-default")

    class Catalog:
        module = manager.catalog.module

        def resolve(self, name):
            return self.profile

    catalog = Catalog()

    class Selected:
        backend = profile.backend
        python = profile.python
        sdk = profile.sdk
        model = profile.model
        resolution = profile.resolution

        def describe(self):
            return profile.describe()

        def qualify(self):
            return qualify()

    catalog.profile = Selected()
    manager.catalog = catalog
    try:
        request = asyncio.create_task(
            manager.submit("lost", session, AnalysisSpec().model_dump(mode="json"))
        )
        await asyncio.sleep(0.1)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        release.set()
        async with asyncio.timeout(10):
            while manager.admissions:
                await asyncio.sleep(0.01)
        row = manager.store.by_request("lost")
        assert (await finish(manager, row["id"]))["state"] == "succeeded"
        same = await manager.submit(
            "lost", session, AnalysisSpec().model_dump(mode="json")
        )
        assert same["id"] == row["id"] and len(manager.store.list()) == 1
    finally:
        release.set()
        await close(manager, host)


async def test_resource_limit_ends_owned_child_without_unloading_models(
    tmp_path, monkeypatch
):
    from venom_module_rider import analysis_manager

    manager, session, host = await rig(tmp_path, monkeypatch)
    monkeypatch.setattr(analysis_manager, "MAX_RSS", 1)
    try:
        row = await manager.submit(
            "resource", session, AnalysisSpec().model_dump(mode="json")
        )
        final = await finish(manager, row["id"])
        assert (
            final["state"] == "failed" and final["error"] == "analysis_resource_limit"
        )
        assert not manager.children and manager.registry.lease is None
    finally:
        await close(manager, host)


async def test_full_disk_preserves_previous_report_and_raw(tmp_path, monkeypatch):
    manager, session, host = await rig(tmp_path, monkeypatch)
    try:
        first = await manager.submit(
            "saved", session, AnalysisSpec().model_dump(mode="json")
        )
        assert (await finish(manager, first["id"]))["state"] == "succeeded"
        proof = manager.store.artifact(first["id"], "report.json")[0]

        def full(*args, **kwargs):
            raise RiderError("samples_disk_full", 507)

        monkeypatch.setattr(manager.store, "write", full)
        second = await manager.submit(
            "full", session, AnalysisSpec().model_dump(mode="json")
        )
        final = await finish(manager, second["id"])
        assert final["state"] == "failed" and final["error"] == "samples_disk_full"
        assert manager.store.artifact(first["id"], "report.json")[0] == proof
        assert manager.registry.store.original(
            final["context"]["inputs"][0]["sample_id"]
        )[0]
    finally:
        await close(manager, host)


async def test_startup_recovery_marks_lost_runs_without_starting_a_model(
    tmp_path, monkeypatch
):
    manager, session, host = await rig(tmp_path, monkeypatch)
    try:
        profile = manager.catalog.resolve("da2-default").qualify()
        row = manager.store.create(
            "interrupted", session, {}, profile, {"sha256": "previous"}
        )
        manager.store.running(row["id"])
        assert await manager.initialize()
        recovered = manager.store.get(row["id"])
        assert (
            recovered["state"] == "failed"
            and recovered["error"] == "analysis_interrupted"
        )
        assert manager.recovered and not manager.children and not manager.tasks
        competitor = AnalysisManager(
            manager.registry, catalog=manager.catalog, host_tasks=host
        )
        try:
            assert not await competitor.initialize()
            with pytest.raises(RiderError, match="analysis_executor_busy"):
                await competitor.submit(
                    "conflict", session, AnalysisSpec().model_dump(mode="json")
                )
        finally:
            await competitor.close()
    finally:
        await close(manager, host)


def test_comparison_does_not_score_different_label_revisions():
    from venom_module_rider.api.analysis_routes import compare_reports

    first = {
        "run_id": "a",
        "input_digest": "old-labels",
        "summary": {"false_go_rate": 0.5, "false_stop_rate": None, "flicker_rate": 0},
    }
    second = {
        "run_id": "b",
        "input_digest": "new-labels",
        "summary": {"false_go_rate": 0, "false_stop_rate": 0, "flicker_rate": 0.2},
    }
    result = compare_reports(first, second)
    assert not result["aligned_inputs_and_labels"]
    assert all(
        value is None for value in result["quality_delta_second_minus_first"].values()
    )
