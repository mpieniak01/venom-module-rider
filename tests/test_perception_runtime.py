"""Installation queue cancellation, bounded protocol and runtime identity checks."""

import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from venom_module_rider.errors import RiderError
from venom_module_rider.perception_catalog import DEPTH, FAST, SEGFORMER
from venom_module_rider.perception_install import ModelInstaller
from venom_module_rider.perception_runtime import ModelRuntime, source_overlay


class Catalog:
    root = Path("/tmp/fixture-not-written")

    def describe(self, model):
        return {
            "installation": "needs_review" if model == FAST else "not_installed",
            "selectable": model != FAST,
            "sha256": "a" * 64,
        }

    def definition(self, model):
        return {"qualification": "needs_review" if model == FAST else "supported"}

    def require(self, model):
        if model not in (FAST, SEGFORMER, DEPTH):
            raise RiderError("model_unknown")

    def ready(self, model):
        return {"sha256": "a" * 64, "backend": "fixture", "files": {}}


class Tasks:
    def __init__(self):
        self.admitted = True
        self.finished = []

    def create(self, row):
        return row["id"]

    async def admit(self, identity, handle):
        return self.admitted

    async def finish(self, identity, state, error):
        self.finished.append((identity, state, error))


class Process:
    returncode = None
    pid = 0

    def __init__(self, messages, code=0):
        self.stdout = asyncio.StreamReader()
        for message in messages:
            self.stdout.feed_data((json.dumps(message) + "\n").encode())
        self.stdout.feed_eof()
        self.code = code

    async def wait(self):
        self.returncode = self.code
        return self.returncode

    def terminate(self):
        self.code = -15

    def kill(self):
        self.code = -9


async def test_one_install_shared_admission_and_protocol(monkeypatch):
    tasks = Tasks()
    installer = ModelInstaller(Catalog(), host_tasks=tasks)
    child = Process(
        [
            {"state": "downloading", "progress": 10},
            {"state": "verifying", "progress": 90},
            {"state": "ready", "progress": 100},
        ]
    )
    calls = []

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    job = installer.submit(SEGFORMER)
    assert installer.submit(SEGFORMER)["id"] == job["id"]
    with pytest.raises(RiderError, match="model_install_busy"):
        installer.submit(DEPTH)
    with pytest.raises(RiderError, match="model_needs_review"):
        installer.submit(FAST)
    await installer.handles[job["id"]]
    assert installer.status(SEGFORMER)["state"] == "ready"
    assert tasks.finished[0][1] == "succeeded"
    assert calls[0][1]["env"].get("CUDA_VISIBLE_DEVICES") != ""
    assert calls[0][0][-3] == SEGFORMER
    assert calls[0][0][-2] == "--parent-pid"
    await installer.close()
    with pytest.raises(RiderError, match="module_closed"):
        installer.submit(DEPTH)


async def test_cancel_before_admission_can_retry():
    tasks = Tasks()
    tasks.admitted = False
    installer = ModelInstaller(Catalog(), host_tasks=tasks)
    first = installer.submit(SEGFORMER)
    handle = installer.handles[first["id"]]
    installer.cancel(SEGFORMER)
    with pytest.raises(asyncio.CancelledError):
        await handle
    await asyncio.sleep(0)
    assert installer.status(SEGFORMER)["state"] == "cancelled"
    second = installer.submit(SEGFORMER)
    assert second["id"] != first["id"]
    await asyncio.sleep(0)
    await installer.close()
    assert installer.status(SEGFORMER)["state"] == "cancelled"


@pytest.mark.parametrize(
    "messages,exit_code",
    [
        ([{"state": "invalid", "progress": 0}], 0),
        ([{"state": "ready", "progress": True}], 0),
        ([{"state": "verifying", "progress": 100}], 1),
    ],
)
async def test_failed_child_never_becomes_ready(monkeypatch, messages, exit_code):
    child = Process(messages, exit_code)

    async def spawn(*args, **kwargs):
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    installer = ModelInstaller(Catalog(), host_tasks=Tasks())
    job = installer.submit(SEGFORMER)
    await installer.handles[job["id"]]
    assert installer.status(SEGFORMER)["state"] == "failed"
    assert installer.status(SEGFORMER)["error"].startswith("model_")
    await installer.close()


def png():
    np = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    output = io.BytesIO()
    Image.fromarray(
        np.random.default_rng(4).integers(30, 230, (64, 96, 3), dtype=np.uint8)
    ).save(output, format="PNG")
    return output.getvalue()


def test_overlay_preserves_original_source_geometry():
    Image = pytest.importorskip("PIL.Image")
    output = io.BytesIO()
    Image.new("RGB", (512, 512), "red").save(output, format="JPEG")
    corrected = source_overlay(png(), output.getvalue())
    assert Image.open(io.BytesIO(corrected)).size == (96, 64)
    assert source_overlay(png(), corrected) == corrected


def frame(model=SEGFORMER, **updates):
    from venom_module_rider.vision_contract import VisionFrame

    values = dict(
        protocol_version=1,
        run_id="test",
        frame_id="frame",
        sequence=1,
        config_revision=2,
        age_ms=0.0,
        deadline_ms=500,
        perception={
            "engine": "segformer",
            "model_id": model,
            "artifact_sha256": "a" * 64,
        },
    )
    values.update(updates)
    return VisionFrame(**values)


def test_runtime_one_model_and_verified_identity(monkeypatch):
    runtime = ModelRuntime(Catalog())
    detector = SimpleNamespace(
        detect=lambda payload, corridor: {
            "state": "clear",
            "reason": "floor_candidate",
        },
        status=lambda: {"loaded": True},
    )
    monkeypatch.setattr(
        "venom_module_rider.perception_runtime.build_detector", lambda *args: detector
    )
    result = runtime.detect(b"fixture", frame())
    assert result["model_id"] == SEGFORMER
    assert result["drive_supported"] is False
    assert runtime.status()["active_model_id"] == SEGFORMER
    assert runtime.diagnostics()["inference_ms"] is not None
    runtime.expected["pi"] = {
        "revision": 2,
        "model_id": SEGFORMER,
        "artifact_sha256": "a" * 64,
    }
    runtime.validate("pi", frame())
    with pytest.raises(RiderError, match="model_selection_conflict"):
        runtime.validate("pi", frame(config_revision=3))
    bad = frame().model_copy(
        update={
            "perception": frame().perception.model_copy(
                update={"artifact_sha256": "b" * 64}
            )
        }
    )
    with pytest.raises(RiderError, match="model_hash_mismatch"):
        runtime.detect(b"fixture", bad)
    bad = frame().model_copy(
        update={
            "perception": frame().perception.model_copy(
                update={"engine": "depth_plane"}
            )
        }
    )
    with pytest.raises(RiderError, match="model_engine_mismatch"):
        runtime.detect(b"fixture", bad)
    runtime.reset()
    assert runtime.status()["state"] == "unloaded"
    assert runtime.generation == 1


async def test_explicit_semantic_cannot_grant_drive(monkeypatch):
    from venom_module_rider.perception_service import PerceptionService
    from venom_module_rider.vision_loop import VisionLoop

    runtime_detector = SimpleNamespace(
        detect=lambda payload, corridor: {
            "state": "clear",
            "reason": "floor_candidate",
        },
        status=lambda: {"loaded": True, "sha256": "d" * 64},
    )
    monkeypatch.setattr(
        "venom_module_rider.perception_runtime.build_detector",
        lambda *args: runtime_detector,
    )
    catalog = Catalog()
    catalog.describe = lambda identity: {"selectable": True, "sha256": "a" * 64}
    loop = VisionLoop()
    models = PerceptionService(loop, catalog=catalog)
    try:
        for sequence in range(1, 4):
            result = await loop.decide(
                "pi",
                frame(
                    sequence=sequence,
                    frame_id="frame" + str(sequence),
                    mode="drive",
                    verified=True,
                    calibration_id="accepted",
                    model_sha256="d" * 64,
                ),
                b"fixture",
            )
        assert result["action"] == "stop"
        assert result["drive_supported"] is False
        assert result["model_id"] == SEGFORMER
        assert result["artifact_sha256"] == "a" * 64
    finally:
        await models.close()
        await loop.close()


async def test_policy_edit_keeps_preview_and_rejects_prior_revision(monkeypatch):
    from venom_module_rider.perception_service import PerceptionService
    from venom_module_rider.vision_loop import VisionLoop

    detector = SimpleNamespace(
        detect=lambda *args: {"state": "unknown", "reason": "vision_low_confidence"},
        status=lambda: {"loaded": True},
    )
    monkeypatch.setattr(
        "venom_module_rider.perception_runtime.build_detector", lambda *args: detector
    )
    loop = VisionLoop()
    models = PerceptionService(loop, catalog=Catalog())
    policy = {"revision": 2, "perception": frame().perception.model_dump()}
    try:
        models.observe_policy("pi", policy)
        models.observe_policy("pi", {**policy, "revision": 3})
        models.observe_policy(
            "pi", policy
        )  # An older concurrent GET cannot roll back identity.
        result = await loop.decide("pi", frame(config_revision=3), b"fixture")
        assert result["config_revision"] == 3
        with pytest.raises(RiderError, match="model_selection_conflict"):
            await loop.decide("pi", frame(), b"fixture")
    finally:
        await models.close()
        await loop.close()


async def test_late_inference_cannot_publish_after_policy_edit(monkeypatch):
    import threading

    from venom_module_rider.perception_service import PerceptionService
    from venom_module_rider.vision_loop import VisionLoop

    started, release = threading.Event(), threading.Event()

    def detect(*args):
        started.set()
        assert release.wait(1)
        return {"state": "clear", "reason": "floor_candidate", "_preview": b"old"}

    loop = VisionLoop()
    models = PerceptionService(loop, catalog=Catalog())
    monkeypatch.setattr(models.runtime, "detect", detect)
    policy = {"revision": 2, "perception": frame().perception.model_dump()}
    models.observe_policy("pi", policy)
    operation = asyncio.create_task(loop.decide("pi", frame(), b"fixture"))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.001)
        assert started.is_set()
        models.observe_policy("pi", {**policy, "revision": 3})
        release.set()
        with pytest.raises(RiderError, match="model_selection_conflict"):
            await operation
        assert not loop.previews
    finally:
        release.set()
        await models.close()
        await loop.close()


async def test_double_cancel_close_and_retry_wait_for_reaped_child(monkeypatch):
    terminated, reaped, spawned = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class SlowProcess:
        returncode = None
        pid = 0
        stdout = None

        def __init__(self):
            self.stdout = asyncio.StreamReader()

        def terminate(self):
            terminated.set()

        def kill(self):
            reaped.set()

        async def wait(self):
            await reaped.wait()
            self.returncode = -15
            return self.returncode

    child = SlowProcess()
    calls = []

    async def spawn(*args, **kwargs):
        calls.append(args)
        spawned.set()
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    installer = ModelInstaller(Catalog(), host_tasks=Tasks())
    first = installer.submit(SEGFORMER)
    await spawned.wait()
    installer.cancel(SEGFORMER)
    await terminated.wait()
    installer.cancel(SEGFORMER)  # Must not interrupt the first cleanup.
    retry = installer.submit(SEGFORMER)
    assert retry["id"] == first["id"]
    with pytest.raises(RiderError, match="model_install_busy"):
        installer.submit(DEPTH)
    closing = asyncio.create_task(installer.close())
    await asyncio.sleep(0)
    assert not closing.done()
    assert len(calls) == 1 and len(installer.children) == 1
    reaped.set()
    await closing
    assert (
        not installer.children and installer.status(SEGFORMER)["state"] == "cancelled"
    )


def test_released_runtime_never_reloads_from_a_frame_but_explicit_warmup_can(
    monkeypatch,
):
    from unittest.mock import Mock

    runtime = ModelRuntime(Catalog())
    detector = SimpleNamespace(
        detect=lambda *_args, **_kwargs: {
            "state": "clear",
            "reason": "floor_candidate",
        },
        status=lambda: {"loaded": True},
    )
    build = Mock(return_value=detector)
    monkeypatch.setattr("venom_module_rider.perception_runtime.build_detector", build)
    runtime.release()
    with pytest.raises(RiderError, match="model_released"):
        runtime.detect(b"fixture", frame())
    build.assert_not_called()
    assert runtime.status()["state"] == "released"
    runtime.warm(frame().perception)
    build.assert_called_once()
    assert runtime.status()["state"] == "active"
