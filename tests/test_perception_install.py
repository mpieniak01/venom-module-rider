"""Host provisioning files/receipts and API dispatch with no network or robot."""

import sys
from types import SimpleNamespace

import httpx
import pytest
import test_perception_models as fixtures
from fastapi import FastAPI
from test_perception_models import Domain, provision

from venom_module_rider.api.routes import router
from venom_module_rider.perception_catalog import SEGFORMER, SOURCES
from venom_module_rider.perception_worker import download, installation_lease, smoke

catalog = fixtures.catalog


def test_pinned_download_atomic_manifest_and_smoke_receipt(
    catalog, tmp_path, monkeypatch
):
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    cache = tmp_path / "cache"
    cache.mkdir()
    for name, data in (
        ("model.safetensors", b"weights"),
        ("config.json", b"{}"),
        ("preprocessor_config.json", b"{}"),
    ):
        (cache / name).write_bytes(data)
    calls = []

    def hub(repo, name, revision):
        calls.append((repo, revision))
        return str(cache / name)

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(hf_hub_download=hub)
    )
    with installation_lease(catalog, SEGFORMER):
        download(catalog, SEGFORMER)
    assert len(calls) == 3
    assert all(revision == SOURCES[SEGFORMER]["revision"] for _, revision in calls)
    assert catalog.describe(SEGFORMER)["reason"] == "model_verification_required"
    detector = SimpleNamespace(
        detect=lambda *args: {"state": "unknown", "_preview": b"jpeg"}
    )
    monkeypatch.setattr(
        "venom_module_rider.perception_worker.build_detector", lambda *args: detector
    )
    smoke(catalog, SEGFORMER)
    assert catalog.describe(SEGFORMER)["selectable"]
    # A retry verifies installed bytes instead of overwriting them.
    download(catalog, SEGFORMER)
    assert len(calls) == 3


def test_download_hash_mismatch_cleans_only_staging(catalog, tmp_path, monkeypatch):
    source = tmp_path / "bad-source"
    source.write_bytes(b"wrong bytes")
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(hf_hub_download=lambda *args, **kwargs: str(source)),
    )
    with installation_lease(catalog, SEGFORMER):
        with pytest.raises(ValueError, match="model_hash_mismatch"):
            download(catalog, SEGFORMER)
    target = catalog.path(SEGFORMER)
    assert not target.exists()
    assert not target.with_name(target.name + ".staging").exists()


def test_interrupted_staging_retry_and_cross_process_lease(catalog):
    target = catalog.path(SEGFORMER)
    staging = target.with_name(target.name + ".staging")
    staging.mkdir(parents=True)
    (staging / "partial").write_bytes(b"partial")
    with installation_lease(catalog, SEGFORMER):
        assert not staging.exists()
        with pytest.raises(BlockingIOError):
            with installation_lease(catalog, SEGFORMER):
                pytest.fail("second owner acquired")


async def test_host_catalog_survives_pi_outage(catalog):
    from venom_module_rider.errors import RiderError
    from venom_module_rider.perception_service import PerceptionService
    from venom_module_rider.vision_loop import VisionLoop

    provision(catalog)
    loop = VisionLoop()
    manager = PerceptionService(loop, catalog=catalog)

    class Offline(Domain):
        async def read(self, *args, **kwargs):
            raise RiderError("pi_unreachable", 503)

        async def pipeline(self, **kwargs):
            raise RiderError("pi_unreachable", 503)

    try:
        data = await manager.snapshot(Offline())
        assert data["selection"] is None
        assert data["selection_error"] == "pi_unreachable"
        assert data["provider"] is None
        assert data["models"][0]["selectable"] is True
    finally:
        await manager.close()
        await loop.close()


async def test_operator_and_pi_alias_use_one_service(monkeypatch):
    monkeypatch.setattr(
        "venom_module_rider.api.routes.ensure_module_mutation_allowed",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "venom_module_rider.api.compute_routes.authenticate", lambda request: "fixture"
    )
    monkeypatch.setattr(
        "venom_module_rider.api.perception_routes.authenticate",
        lambda request: "fixture",
    )
    calls = []

    class Manager:
        installer = SimpleNamespace(
            submit=lambda identity: {"id": "job", "model_id": identity},
            cancel=lambda identity: {"id": "job", "state": "cancelled"},
        )

        async def snapshot(self, domain):
            calls.append("get")
            return {"models": []}

        async def select(self, domain, body, session):
            domain.check_session(session)
            calls.append(body)
            return {"selection": body}

    app = FastAPI()
    app.state.rider = Domain()
    app.state.rider_models = Manager()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        for url in ("/perception/models", "/compute/vision/models"):
            response = await client.get("/api/v1/rider" + url)
            assert response.status_code == 200
        for url in ("/perception/selection", "/compute/vision/model"):
            response = await client.post(
                "/api/v1/rider" + url,
                json={"revision": 1, "model_id": SEGFORMER},
                headers={"X-Rider-Session": "fixture"},
            )
            assert response.status_code == 200
        for url in (
            "/perception/models/" + SEGFORMER + "/install",
            "/perception/models/" + SEGFORMER + "/install/cancel",
        ):
            response = await client.post(
                "/api/v1/rider" + url, json={}, headers={"X-Rider-Session": "fixture"}
            )
            assert response.status_code in (200, 202)
        response = await client.post(
            "/api/v1/rider/perception/models/" + SEGFORMER + "/install",
            json={},
            headers={"X-Rider-Session": "old"},
        )
        assert response.status_code == 409
    assert len(calls) == 4
