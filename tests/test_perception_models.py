"""Catalog identity, tampering and safe transaction checks, without robot or ML."""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from venom_module_rider.analysis_profiles import ProfileCatalog
from venom_module_rider.errors import RiderError
from venom_module_rider.perception_catalog import DEPTH, FAST, SEGFORMER, SOURCES
from venom_module_rider.perception_service import PerceptionService
from venom_module_rider.vision_loop import VisionLoop

DIGEST = hashlib.sha256(b"weights").hexdigest()


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.delenv("RIDER_VISION_MODEL", raising=False)
    monkeypatch.delenv("RIDER_DEPTH_MODEL", raising=False)
    monkeypatch.setitem(SOURCES[SEGFORMER], "sha256", DIGEST)
    monkeypatch.setitem(SOURCES[DEPTH], "sha256", DIGEST)
    monkeypatch.setattr(
        "venom_module_rider.perception_runtime.ModelRuntime.warm",
        lambda self, perception: None,
    )
    return ProfileCatalog(assets_root=tmp_path).live()


def provision(catalog, model=SEGFORMER):
    path = catalog.path(model)
    path.mkdir(parents=True)
    files = {}
    for name, content in {
        "model.safetensors": b"weights",
        "config.json": b"{}",
        "preprocessor_config.json": b"{}",
    }.items():
        (path / name).write_bytes(content)
        files[name] = hashlib.sha256(content).hexdigest()
    (path / "manifest.json").write_text(
        json.dumps(
            {"model_id": model, "revision": SOURCES[model]["revision"], "files": files}
        )
    )
    return catalog.save_receipt(model)


def test_only_allowlisted_reviewed_models(catalog):
    assert catalog.describe(FAST)["installation"] == "needs_review"
    assert not catalog.describe(FAST)["selectable"]
    with pytest.raises(RiderError, match="model_unknown"):
        catalog.path("../../private")
    assert catalog.describe(SEGFORMER)["installation"] == "not_installed"


def test_receipt_and_content_both_required(catalog):
    artifact = provision(catalog)
    assert catalog.describe(SEGFORMER)["selectable"]
    assert catalog.ready(SEGFORMER) == artifact
    catalog.receipt_path(SEGFORMER).unlink()
    assert not catalog.describe(SEGFORMER)["selectable"]
    assert catalog.describe(SEGFORMER)["reason"] == "model_verification_required"
    catalog.save_receipt(SEGFORMER)
    (catalog.path(SEGFORMER) / "model.safetensors").write_bytes(b"tampered")
    assert catalog.describe(SEGFORMER)["reason"] == "model_hash_mismatch"
    with pytest.raises(RiderError, match="model_hash_mismatch"):
        catalog.ready(SEGFORMER)


def test_manifest_cannot_escape_asset_root(catalog):
    provision(catalog)
    path = catalog.path(SEGFORMER) / "manifest.json"
    metadata = json.loads(path.read_text())
    metadata["files"]["../secrets"] = DIGEST
    path.write_text(json.dumps(metadata))
    assert catalog.describe(SEGFORMER)["reason"] == "model_not_installed"


def test_existing_depth_manifest_is_preserved(catalog):
    artifact = provision(catalog, DEPTH)
    assert catalog.describe(DEPTH)["selectable"]
    assert artifact["backend"] == "da2"
    assert catalog.definition(DEPTH)["drive_supported"]
    assert not catalog.definition(SEGFORMER)["drive_supported"]


class Domain:
    session = "fixture"
    epoch = 1

    def __init__(self, *, fail_ack=False, provider="venom"):
        self.policy = {
            "revision": 2,
            "mode": "observe",
            "perception": {"engine": "segformer"},
            "verified": False,
            "calibration_id": "",
            "model_sha256": "",
        }
        self.fail_ack = fail_ack
        self.provider = provider
        self.posts = []

    def check_session(self, session):
        if session != self.session:
            raise RiderError("stale_device_session")

    def transport(self):
        return SimpleNamespace(device=SimpleNamespace(id="fixture"))

    async def pipeline(self, **_):
        return {
            "profile": {"analysis_location": self.provider, "local_engine": "canny"},
            "revision": 3,
            "capabilities": {"analysis_locations": ["local", "venom"]},
        }

    async def read(self, path, *args, **kwargs):
        if path.endswith("local-models"):
            return {"data": {"models": [{"id": "local-canny"}]}}
        return {"data": self.policy}

    async def mutate(self, path, body, session):
        self.check_session(session)
        self.posts.append((path, body))
        if body["revision"] != self.policy["revision"]:
            raise RiderError("model_selection_conflict")
        self.policy = {
            **self.policy,
            "revision": self.policy["revision"] + 1,
            "perception": {k: v for k, v in body.items() if k != "revision"},
        }
        if self.fail_ack:
            raise RiderError("pi_timeout", 504)
        return {"data": {"ok": True, "persisted": True, "config": self.policy}}


@pytest.mark.parametrize("ack_lost", [False, True])
async def test_selection_commits_cas_clears_old_masks_without_start(catalog, ack_lost):
    artifact = provision(catalog)
    loop = VisionLoop()
    service = PerceptionService(loop, catalog=catalog)
    domain = Domain(fail_ack=ack_lost)
    loop.previews["fixture"] = "old-mask"
    loop.inference_cache["old"] = "old-result"
    loop.runs["old"] = "old-count"
    try:
        result = await service.select(
            domain, {"revision": 2, "model_id": SEGFORMER}, "fixture"
        )
        assert result["selection"] == {
            "revision": 3,
            "model_id": SEGFORMER,
            "artifact_sha256": artifact["sha256"],
        }
        assert not loop.previews and not loop.inference_cache and not loop.runs
        assert service.runtime.generation == 1
        assert domain.posts[0][0] == "/api/navigation/model-selection"
        assert len(domain.posts) == 1
        assert not service.switching
        snapshot = await service.snapshot(domain)
        assert snapshot["provider"] == "venom"
        assert snapshot["local_engine"] == "canny"
        assert snapshot["local_models"][0]["id"] == "local-canny"
    finally:
        await service.close()
        await loop.close()


async def test_reject_stale_or_local_provider_before_mutating(catalog):
    provision(catalog)
    loop = VisionLoop()
    service = PerceptionService(loop, catalog=catalog)
    domain = Domain(provider="local")
    try:
        with pytest.raises(RiderError, match="stale_device_session"):
            await service.select(domain, {"revision": 2, "model_id": SEGFORMER}, "old")
        with pytest.raises(RiderError, match="vision_remote_disabled"):
            await service.select(
                domain, {"revision": 2, "model_id": SEGFORMER}, "fixture"
            )
        assert not domain.posts and not service.switching
        with pytest.raises(RiderError, match="model_selection_invalid"):
            await service.select(
                domain, {"revision": True, "model_id": SEGFORMER}, "fixture"
            )
        service.switching = True
        from venom_module_rider.vision_contract import VisionFrame

        frame = VisionFrame(
            protocol_version=1,
            run_id="run",
            frame_id="frame",
            sequence=1,
            config_revision=2,
            age_ms=0.0,
            deadline_ms=300,
        )
        with pytest.raises(RiderError, match="model_busy"):
            await loop.decide("fixture", frame, b"fixture")
    finally:
        await service.close()
        await loop.close()


async def test_switch_waits_for_native_work(catalog):
    provision(catalog)
    loop = VisionLoop()
    service = PerceptionService(loop, catalog=catalog)
    domain = Domain()
    gate = asyncio.Event()
    loop.busy = asyncio.create_task(gate.wait())
    operation = asyncio.create_task(
        service.select(domain, {"revision": 2, "model_id": SEGFORMER}, "fixture")
    )
    try:
        for _ in range(100):
            if service.switching:
                break
            await asyncio.sleep(0.001)
        assert service.switching and not domain.posts
        with pytest.raises(RiderError, match="model_busy"):
            await service.select(
                domain, {"revision": 2, "model_id": SEGFORMER}, "fixture"
            )
        gate.set()
        await operation
        assert len(domain.posts) == 1
    finally:
        gate.set()
        await operation
        await loop.close()


async def test_legacy_loaded_model_is_visible_without_changing_selection(catalog):
    provision(catalog)
    loop = VisionLoop()
    loop.detector = SimpleNamespace(
        status=lambda: {
            "loaded": True,
            "configured": True,
            "sha256": DIGEST,
            "device": "cpu",
        }
    )
    service = PerceptionService(loop, catalog=catalog)
    domain = Domain()
    try:
        snapshot = await service.snapshot(domain)
        assert snapshot["diagnostics"]["runtime"]["active_model_id"] == SEGFORMER
        assert snapshot["diagnostics"]["runtime"]["state"] == "active"
        assert snapshot["models"][0]["loaded"] is True
        assert snapshot["selection"]["artifact_sha256"] == ""
        assert not domain.posts
    finally:
        await service.close()
        await loop.close()


@pytest.mark.parametrize("model", [SEGFORMER, DEPTH])
async def test_explicit_observation_reloads_released_selection_without_policy_write(
    catalog, monkeypatch, model
):
    import copy

    artifact = provision(catalog, model)
    loop = VisionLoop()
    service = PerceptionService(loop, catalog=catalog)
    domain = Domain()
    domain.policy.update(verified=True, calibration_id="accepted", model_sha256=DIGEST)
    domain.policy["perception"].update(
        model_id=model,
        engine=catalog.definition(model)["engine"],
        artifact_sha256=artifact["sha256"],
    )
    original = copy.deepcopy(domain.policy)
    calls = []

    def warm(perception):
        calls.append(perception.model_id)
        service.runtime.identity = model, artifact["sha256"]
        service.runtime.state = "active"

    monkeypatch.setattr(service.runtime, "warm", warm)
    try:
        service.runtime.reset()
        await service.prepare_observation(domain, "fixture")
        await service.prepare_observation(domain, "fixture")
        assert calls == [model]
        service.runtime.reset()
        await service.prepare_observation(domain, "fixture")
        assert calls == [model, model]
        assert domain.policy == original and not domain.posts
        assert not hasattr(loop, "resources_suspended")
        assert not service.switching and loop.busy is None and loop.depth_busy is None
    finally:
        await service.close()
        await loop.close()


async def test_observation_load_failure_does_not_change_policy_or_hide_retry(
    catalog, monkeypatch
):
    artifact = provision(catalog)
    loop = VisionLoop()
    service = PerceptionService(loop, catalog=catalog)
    domain = Domain()
    domain.policy["perception"].update(
        model_id=SEGFORMER, artifact_sha256=artifact["sha256"]
    )

    def fail(_perception):
        raise RiderError("vision_cuda_unavailable", 503)

    monkeypatch.setattr(service.runtime, "warm", fail)
    try:
        with pytest.raises(RiderError, match="stale_device_session"):
            await service.prepare_observation(domain, "old")
        with pytest.raises(RiderError, match="vision_cuda_unavailable"):
            await service.prepare_observation(domain, "fixture")
        assert not domain.posts and not service.switching
        assert loop.busy is None and loop.depth_busy is None
        monkeypatch.setattr(service.runtime, "warm", lambda _perception: None)
        await service.prepare_observation(domain, "fixture")
    finally:
        await service.close()
        await loop.close()


async def test_observation_does_not_resume_after_policy_changes_during_warmup(
    catalog, monkeypatch
):
    artifact = provision(catalog)
    loop = VisionLoop()
    service = PerceptionService(loop, catalog=catalog)
    domain = Domain()
    domain.policy["perception"].update(
        model_id=SEGFORMER, artifact_sha256=artifact["sha256"]
    )
    monkeypatch.setattr(
        service.runtime, "warm", lambda _: domain.policy.update(revision=3)
    )
    try:
        with pytest.raises(RiderError, match="model_selection_conflict"):
            await service.prepare_observation(domain, "fixture")
        assert not domain.posts and not service.switching
    finally:
        await service.close()
        await loop.close()


@pytest.mark.parametrize("enabled", [True, False])
async def test_goal_prepares_model_only_for_explicit_observation_start(enabled):
    from unittest.mock import AsyncMock

    from venom_module_rider.service import RiderService

    domain = RiderService(())
    events = []

    async def prepare(session):
        assert session == domain.session
        events.append("load")

    async def mutate(path, body, session):
        assert path == "/api/logic/feature/s15_venom_perception"
        assert body == {"enabled": enabled} and session == domain.session
        events.append("goal")
        return {"data": {"ok": True}}

    domain.read = AsyncMock(
        return_value={
            "data": {"features": [{"name": "s15_venom_perception", "can_move": False}]},
            "meta": {},
        }
    )
    domain.mutate = mutate
    domain.observation_preparer = prepare
    try:
        await domain.goal("s15_venom_perception", {"enabled": enabled}, domain.session)
        assert events == (["load", "goal"] if enabled else ["goal"])
    finally:
        await domain.close()


async def test_goal_does_not_start_observation_if_model_loading_fails():
    from unittest.mock import AsyncMock

    from venom_module_rider.service import RiderService

    domain = RiderService(())
    domain.read = AsyncMock(
        return_value={
            "data": {"features": [{"name": "s15_venom_perception", "can_move": False}]},
            "meta": {},
        }
    )
    domain.mutate = AsyncMock()
    domain.observation_preparer = AsyncMock(
        side_effect=RiderError("vision_cuda_unavailable", 503)
    )
    try:
        with pytest.raises(RiderError, match="vision_cuda_unavailable"):
            await domain.goal("s15_venom_perception", {"enabled": True}, domain.session)
        domain.mutate.assert_not_awaited()
    finally:
        await domain.close()


async def test_analysis_prepare_route_loads_without_starting_device_goals():
    from unittest.mock import AsyncMock

    import httpx
    from fastapi import FastAPI

    from venom_module_rider.api.perception_routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.rider = domain = object()
    app.state.rider_models = SimpleNamespace(prepare_observation=AsyncMock())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        result = await client.post(
            "/perception/prepare", headers={"X-Rider-Session": "fixture-session"}
        )
        assert result.status_code == 200 and result.json() == {"ok": True}
        app.state.rider_models.prepare_observation.assert_awaited_once_with(
            domain, "fixture-session"
        )
