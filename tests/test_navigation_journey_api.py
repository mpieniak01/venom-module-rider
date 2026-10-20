"""Original operator intent crosses one journey endpoint, never a timer renewal."""

import json

import httpx
import pytest
from pydantic import ValidationError
from test_navigation_trial_api import body, setup_proxy

from venom_module_rider.api.schemas import NavigationJourneyRequest
from venom_module_rider.client import expected_contract, json_timeout
from venom_module_rider.demo import envelope


def intent(**changes):
    return body(
        journey_version=1,
        operator_control_version=1,
        panel_id="journey-panel",
        operator_plan="Continue slowly with obstacle avoidance until STOP.",
        **changes,
    )


async def test_journey_preserves_intent_and_negotiated_pi_result(monkeypatch):
    calls = []
    status = {"journey": {"version": 1, "id": "click-1", "active": True}}

    def receive(request):
        calls.append(request)
        response = envelope({"status": status}, "rider.navigation_loop")
        return httpx.Response(200, content=response.body, headers=response.headers)

    service, client = await setup_proxy(monkeypatch, receive)
    original = intent()
    try:
        response = await client.post(
            "/api/v1/rider/navigation/journey",
            json=original,
            headers={"X-Rider-Session": service.session},
        )
        assert response.status_code == 200
        assert response.json()["data"]["status"] == status
        assert len(calls) == 1
        assert calls[0].url.path == "/api/navigation/journey"
        assert json.loads(calls[0].content) == original
        assert calls[0].extensions["timeout"]["read"] == 18.0
    finally:
        await client.aclose()
        await service.close()


@pytest.mark.parametrize(
    "change",
    [
        {"journey_version": True},
        {"journey_version": 2},
        {"journey_version": None},
        {"operator_control_version": None},
        {"operator_control_version": True},
        {"panel_id": " "},
        {"panel_id": None},
        {"operator_consent": False},
        {"lease_s": 3600},
        {"arming_mode": "operator"},
        {"ttl_ms": 501},
    ],
)
def test_journey_cannot_use_legacy_or_unbounded_arm_envelope(change):
    request = {**intent(), **change}
    with pytest.raises(ValidationError):
        NavigationJourneyRequest(**request)


@pytest.mark.parametrize(
    "missing",
    [
        "journey_version",
        "operator_control_version",
        "panel_id",
        "generation",
        "config_revision",
        "settings_revision",
    ],
)
def test_journey_required_identity_cannot_be_omitted(missing):
    request = intent()
    request.pop(missing)
    with pytest.raises(ValidationError):
        NavigationJourneyRequest(**request)


async def test_journey_stale_tab_never_reaches_pi(monkeypatch):
    calls = []
    service, client = await setup_proxy(monkeypatch, lambda req: calls.append(req))
    try:
        response = await client.post(
            "/api/v1/rider/navigation/journey",
            json=intent(),
            headers={"X-Rider-Session": "old-tab"},
        )
        assert response.status_code == 409 and calls == []
    finally:
        await client.aclose()
        await service.close()


async def test_failed_preparation_is_returned_once_without_trial_fallback(monkeypatch):
    calls = []

    def receive(request):
        calls.append(request)
        response = envelope(
            {
                "error": "navigation_journey_not_supported",
                "details": {"preparation": {"stage": "capability", "failed": True}},
            },
            "rider.navigation_loop",
            status=409,
        )
        return httpx.Response(409, content=response.body, headers=response.headers)

    service, client = await setup_proxy(monkeypatch, receive)
    try:
        response = await client.post(
            "/api/v1/rider/navigation/journey",
            json=intent(),
            headers={"X-Rider-Session": service.session},
        )
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "navigation_journey_not_supported"
        assert detail["details"]["preparation"]["stage"] == "capability"
        assert len(calls) == 1 and calls[0].url.path == "/api/navigation/journey"
    finally:
        await client.aclose()
        await service.close()


def test_journey_timeout_bounds_preparation_not_mission_duration():
    assert expected_contract("/api/navigation/journey") == ("rider.navigation_loop", 1)
    assert json_timeout("/api/navigation/journey", "POST", False).read == 18
    assert json_timeout("/api/stop", "POST", True).read == 1.5


def test_journey_preparation_identity_matches_pi_start_identifier():
    import hashlib

    from venom_module_rider.client import preparation_identity

    request_id = "a" * 128
    result = preparation_identity({"request_id": request_id, "journey_version": 1})
    assert result["operator_request_id"] == (
        "journey-" + hashlib.sha256(request_id.encode()).hexdigest()[:32] + "-start"
    )
