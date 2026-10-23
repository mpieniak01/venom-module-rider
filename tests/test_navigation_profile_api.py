"""Pair capability presentation and mutation admission, without a real Pi."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from venom_module_rider.api.vision_routes import configure, profile_presentation
from venom_module_rider.errors import RiderError
from venom_module_rider.navigation_profiles import catalog


def test_host_capability_masks_pi_support_without_mutating_cached_snapshot():
    config = {"behavior_catalog": catalog(), "revision": 3}
    original = deepcopy(config)
    for status in (
        {},
        {"temporal_navigation_v1": False},
        {"temporal_navigation_v1": 1},
    ):
        result = profile_presentation(config, status)
        assert result["behavior_catalog"]["supported"] is False
        assert config == original
    assert (
        profile_presentation(config, {"temporal_navigation_v1": True})[
            "behavior_catalog"
        ]["supported"]
        is True
    )


@pytest.mark.parametrize("model", ["balanced", "legacy"])
async def test_unsupported_host_rejects_new_profiles_but_allows_legacy(model):
    calls = []

    async def json_body():
        return {"revision": 0, "values": {"behavior": {"profile_id": model}}}

    async def mutate(*args):
        calls.append(args)
        return {"data": {"config": {"behavior_catalog": catalog()}}}

    state = SimpleNamespace(
        rider_vision=SimpleNamespace(status=lambda: {}),
        rider=SimpleNamespace(mutate=mutate),
    )
    request = SimpleNamespace(
        json=json_body, app=SimpleNamespace(state=state), headers={}
    )
    if model == "balanced":
        with pytest.raises(RiderError, match="temporal_navigation_update_required"):
            await configure(request)
        assert not calls
    else:
        result = await configure(request)
        assert len(calls) == 1
        assert result["data"]["config"]["behavior_catalog"]["supported"] is False
