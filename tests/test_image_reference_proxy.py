"""Display measurements are proxied to Pi without editing navigation policy."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI

from venom_module_rider.api.vision_routes import router


async def test_read_and_save_reference_use_pi_domain_and_original_session():
    state = {
        "version": 1,
        "revision": 4,
        "values": None,
        "purpose": "visual_reference_only",
    }
    domain = SimpleNamespace(
        read=AsyncMock(return_value={"data": state}),
        mutate=AsyncMock(return_value={"data": {**state, "revision": 5}}),
    )
    app = FastAPI()
    app.include_router(router)
    app.state.rider = domain
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        response = await client.get("/navigation/image-reference")
        assert response.json()["data"] == state
        domain.read.assert_awaited_once_with(
            "/api/navigation/image-reference", 0.2, force=True
        )
        values = {"revision": 4, "values": {"visible": True}}
        response = await client.post(
            "/navigation/image-reference",
            json=values,
            headers={"X-Rider-Session": "original-session"},
        )
        assert response.json()["data"]["revision"] == 5
        domain.mutate.assert_awaited_once_with(
            "/api/navigation/image-reference", values, "original-session"
        )
