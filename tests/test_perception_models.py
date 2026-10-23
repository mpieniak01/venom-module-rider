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
