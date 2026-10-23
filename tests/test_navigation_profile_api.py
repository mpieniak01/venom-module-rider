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


@pytest.mark.parametrize("missing", [True, False])
async def test_legacy_identity_cannot_omit_or_change_saved_model_tuning(missing):
    from venom_module_rider.api.vision_routes import validate_model_policy
    from venom_module_rider.vision_contract import VisionFrame

    perception = {
        "engine": "segformer",
        "semantic_models": {
            "segformer-b0-ade20k": {"version": 1, "surface": "floor_rug"}
        },
    }

    async def read(*_):
        return {"data": {"revision": 2, "perception": perception}}

    domain = SimpleNamespace(
        transport=lambda: SimpleNamespace(device=SimpleNamespace(id="pi")), read=read
    )
    models = SimpleNamespace(runtime=SimpleNamespace(expected={}))
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(rider=domain, rider_models=models))
    )
    frame = VisionFrame(
        protocol_version=1,
        run_id="r",
        frame_id="f",
        sequence=1,
        config_revision=2,
        age_ms=0.0,
        deadline_ms=300,
        perception={} if missing else perception,
    )
    if missing:
        with pytest.raises(RiderError, match="tuning_conflict"):
            await validate_model_policy(request, "pi", frame)
    else:
        await validate_model_policy(request, "pi", frame)


@pytest.mark.parametrize("values", [None, [], "invalid"])
async def test_configuration_rejects_unstructured_values(values):
    async def json_body():
        return {"revision": 0, "values": values}

    request = SimpleNamespace(json=json_body)
    with pytest.raises(RiderError, match="configuration_invalid"):
        await configure(request)


@pytest.mark.parametrize(
    "saved,incoming,revision",
    [
        ("legacy", "fluid", 2),
        ("fluid", "legacy", 2),
        ("balanced", "balanced", 1),
        ("fluid", "fluid", 2),
    ],
)
async def test_frame_profile_must_match_authoritative_policy(saved, incoming, revision):
    from venom_module_rider.api.vision_routes import validate_model_policy
    from venom_module_rider.navigation_profiles import DEFAULT_BEHAVIOR
    from venom_module_rider.vision_contract import VisionFrame

    async def read(*_):
        return {
            "data": {
                "revision": 2,
                "behavior": {**DEFAULT_BEHAVIOR, "profile_id": saved},
            }
        }

    domain = SimpleNamespace(
        transport=lambda: SimpleNamespace(device=SimpleNamespace(id="pi")), read=read
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(rider=domain, rider_models=object()))
    )
    frame = VisionFrame(
        protocol_version=1,
        run_id="r",
        frame_id="f",
        sequence=1,
        config_revision=revision,
        age_ms=0.0,
        deadline_ms=300,
        behavior={**DEFAULT_BEHAVIOR, "profile_id": incoming},
    )
    if saved != incoming or revision != 2:
        with pytest.raises(RiderError, match="navigation_behavior_conflict"):
            await validate_model_policy(request, "pi", frame)
    else:
        await validate_model_policy(request, "pi", frame)
