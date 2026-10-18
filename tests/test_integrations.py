"""263R passive inventory; all contacts use fixtures, never real hardware."""

import asyncio
import copy
import importlib
import json
import os
import subprocess
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from venom_core.api.schemas.integrations import IntegrationEntry, IntegrationObservation
from venom_core.services.integration_hooks import apply_module_observations

from venom_module_rider.config import Device
from venom_module_rider.integrations import MAX_OBSERVATION_AGE_S, observe_integration
from venom_module_rider.service import RiderService


@pytest.fixture
def service():
    targets = (
        Device("one", "First robot", "https://one.invalid"),
        Device("two", "Second robot", "https://two.invalid"),
    )
    domain = RiderService(
        targets, client_factory=Mock(side_effect=AssertionError("no client"))
    )
    # Existing-service fixture: selection has already happened before inventory.
    domain.device = targets[0]
    return domain


def app_for(service=None):
    app = FastAPI()
    if service is not None:
        app.state.rider = service
    return app


def observe(domain, status="online", age=0):
    domain.connection_observation = (time.monotonic() - age, time.time() - age, status)


async def test_two_devices_keep_unselected_unobserved_and_never_change_domain(service):
    observe(service)
    service.cache["private"] = (time.monotonic(), {"data": {"token": "private"}})
    before = (
        service.device,
        service.session,
        copy.deepcopy(service.cache),
        service.epoch,
    )
    for method in (
        "select",
        "restore",
        "read",
        "status",
        "control",
        "transport",
        "close",
    ):
        setattr(service, method, Mock(side_effect=AssertionError(method)))
    first, second = (await observe_integration(app_for(service)))["resources"]
    assert first["id"] == "one" and first["selected"]
    assert first["state"] == "connected" and first["reason_code"] == "device_online"
    assert first["source"] == "device_cache" and first["configuration"] == "configured"
    assert second["state"] == "unverified" and not second["selected"]
    assert second["source"] == "configuration" and second["observed_at"] is None
    assert second["reason_code"] == "device_not_selected"
    assert before == (service.device, service.session, service.cache, service.epoch)
    assert not service.factory.called
    assert not service.inflight and not service.commands and not service.stops


@pytest.mark.parametrize(
    "status,age,state,reason,connectivity",
    [
        ("online", 0, "connected", "device_online", "ok"),
        ("degraded", 0, "connected", "device_online", "degraded"),
        ("offline", 0, "offline", "device_offline", "down"),
        ("online", 61, "stale", "device_observation_stale", "unknown"),
        ("offline", 61, "stale", "device_observation_stale", "unknown"),
        ("online", -2, "stale", "device_observation_stale", "unknown"),
    ],
)
async def test_cached_connectivity_uses_actual_evidence(
    service, status, age, state, reason, connectivity
):
    observe(service, status, age)
    result = await observe_integration(app_for(service))
    validated = IntegrationObservation.model_validate(result)
    row = validated.resources[0]
    assert row.state == state and row.reason_code == reason
    assert validated.connectivity.value == connectivity
    assert (
        abs(
            datetime.fromisoformat(row.observed_at).timestamp()
            - service.connection_observation[1]
        )
        < 0.001
    )
    assert row.available_capabilities is None
    assert row.supported_capabilities == [
        "manual_control",
        "camera",
        "vision",
        "samples",
        "navigation",
    ]
    if age > 60:
        assert row.reported_capabilities is None


async def test_wrapper_refresh_does_not_refresh_contact_time(service):
    observe(service, age=20)
    app = app_for(service)
    first = await observe_integration(app)
    await asyncio.sleep(0.002)
    second = await observe_integration(app)
    assert first["resources"] == second["resources"]
    assert first["observed_at"] != second["observed_at"]


@pytest.mark.parametrize("service_present", [True, False])
async def test_empty_configuration_is_complete_zero(monkeypatch, service_present):
    monkeypatch.setenv("RIDER_DEVICES_JSON", "[]")
    app = app_for(RiderService(()) if service_present else None)
    result = await observe_integration(app)
    assert result["resources"] == [] and result["resources_complete"]
    assert result["configuration"] == "unconfigured"
    assert result["reason_code"] == "device_not_configured"
    assert result["connectivity"] == "unknown"
    IntegrationObservation.model_validate(result)
    assert not hasattr(app.state, "rider") or app.state.rider.client is None


@pytest.mark.parametrize(
    "raw",
    [
        "{bad",
        "{}",
        '[{"id":"invalid","label":"X","url":"https://user:secret@localhost"}]',
    ],
)
async def test_bad_configuration_has_no_fabricated_robot(monkeypatch, raw):
    monkeypatch.setenv("RIDER_DEVICES_JSON", raw)
    app = app_for()
    result = await observe_integration(app)
    assert result["resources"] == [] and not result["resources_complete"]
    assert result["reason_code"] == "device_configuration_invalid"
    assert result["configuration"] == "unknown"
    assert not hasattr(app.state, "rider")
    IntegrationObservation.model_validate(result)


async def test_idle_configuration_is_read_without_constructing_service(monkeypatch):
    monkeypatch.delenv("RIDER_DEMO_ONLY", raising=False)
    monkeypatch.setenv(
        "RIDER_DEVICES_JSON",
        json.dumps(
            [{"id": "idle", "label": "Idle robot", "url": "https://private.invalid"}]
        ),
    )
    monkeypatch.setattr(
        RiderService, "__init__", Mock(side_effect=AssertionError("no init"))
    )
    app = app_for()
    result = await observe_integration(app)
    assert result["resources_complete"] and result["configuration"] == "configured"
    row = result["resources"][0]
    assert not row["selected"] and row["state"] == "unverified"
    assert row["observed_at"] is None
    assert not hasattr(app.state, "rider")
    assert "private.invalid" not in json.dumps(result)


async def test_selected_without_contact_is_unknown_even_when_last_seen_is_set(service):
    service.last_seen = time.time()
    result = await observe_integration(app_for(service))
    row = result["resources"][0]
    assert row["state"] == "unverified" and row["reason_code"] == "not_observed"
    assert row["observed_at"] is None and result["connectivity"] == "unknown"


async def test_demo_is_explicit_and_not_motion_admission(service):
    demo = Device("demo", "Demo", "http://127.0.0.1", "demo")
    service.devices = {demo.id: demo}
    service.device = demo
    observe(service)
    result = await observe_integration(app_for(service))
    assert result["resources"][0]["mode"] == "demo"
    assert result["reason_code"] == "demo_observation"
    assert result["resources"][0]["available_capabilities"] is None


@pytest.mark.parametrize("field", ["closed", "switching"])
async def test_inactive_service_does_not_reuse_connectivity(service, field):
    observe(service)
    setattr(service, field, True)
    row = (await observe_integration(app_for(service)))["resources"][0]
    assert row["state"] == "unverified" and row["observed_at"] is None


async def test_inventory_is_bounded_and_does_not_claim_complete_total(service):
    service.devices = {
        str(i): Device(str(i), "Robot", "https://private.invalid") for i in range(65)
    }
    result = await observe_integration(app_for(service))
    assert len(result["resources"]) == 64 and not result["resources_complete"]
    IntegrationObservation.model_validate(result)


@pytest.mark.parametrize("wall", [float("nan"), float("inf"), -1, 1e99])
async def test_invalid_contact_timestamp_never_looks_connected(service, wall):
    service.connection_observation = (time.monotonic(), wall, "online")
    row = (await observe_integration(app_for(service)))["resources"][0]
    assert row["state"] == "unverified" and row["observed_at"] is None


async def test_capabilities_need_fresh_explicit_cache_and_do_not_grant_action(service):
    observe(service)
    now = time.monotonic()
    service.cache.update(
        {
            "/api/control/state": (
                now,
                {
                    "data": {
                        "safety": {
                            "state": "ARMED",
                            "can_move": True,
                            "capabilities": {"move": {"available": True}},
                            "private_token": "never-export",
                        }
                    }
                },
            ),
            "/api/resource/camera/pipeline": (
                now,
                {
                    "data": {
                        "contract_id": "rider.camera_pipeline",
                        "token": "never-export",
                    }
                },
            ),
        }
    )
    result = await observe_integration(app_for(service))
    assert result["resources"][0]["reported_capabilities"] == [
        "manual_control",
        "camera",
    ]
    assert result["resources"][0]["available_capabilities"] is None
    assert "ARMED" not in json.dumps(result) and "never-export" not in json.dumps(
        result
    )
    for path, (_, payload) in list(service.cache.items()):
        service.cache[path] = (now - MAX_OBSERVATION_AGE_S - 1, payload)
    assert (await observe_integration(app_for(service)))["resources"][0][
        "reported_capabilities"
    ] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"data": None},
        {"data": {"safety": None}},
        {"data": {"safety": {"capabilities": []}}},
    ],
)
async def test_partial_cache_does_not_invent_capabilities(service, payload):
    observe(service)
    service.cache["/api/control/state"] = (time.monotonic(), payload)
    row = (await observe_integration(app_for(service)))["resources"][0]
    assert row["reported_capabilities"] is None


async def test_private_fields_and_pasted_urls_are_not_exported(service, monkeypatch):
    target = Device(
        "one",
        "Bearer secret https://user:password@192.0.2.1",
        "https://private.invalid",
        agent_token_env="PRIVATE_TOKEN",
    )
    service.devices["one"] = service.device = target
    monkeypatch.setenv("PRIVATE_TOKEN", "do-not-read")
    service.session = "private-session"
    result = await observe_integration(app_for(service))
    assert result["resources"][0]["label"] == "Rider-Pi"
    encoded = json.dumps(result)
    for value in (
        "192.0.2.1",
        "private.invalid",
        "user:password",
        "PRIVATE_TOKEN",
        "do-not-read",
        "private-session",
    ):
        assert value not in encoded


async def test_passive_status_evidence_records_failures_and_cache_hits_keep_timestamp(
    service,
):
    responses = {"fail": False}

    async def json_response(path):
        if responses["fail"]:
            from venom_module_rider.errors import RiderError

            raise RiderError("pi_unreachable", 503)
        return {"data": {}, "meta": {}}

    service.client = SimpleNamespace(json=AsyncMock(side_effect=json_response))
    service.device = service.devices["one"]
    assert (await service.status("navigation"))["connection"]["status"] == "online"
    original = service.connection_observation
    assert original is not None
    # This second fixture status uses cache only, unlike the forced control route.
    service.read = lambda path, ttl, **kwargs: _cached(service.cache[path][1])
    await service.status("navigation")
    assert service.connection_observation == original
    service.read = RiderService.read.__get__(service)
    responses["fail"] = True
    assert (await service.status("navigation"))["connection"]["status"] == "offline"
    result = await observe_integration(app_for(service))
    assert result["resources"][0]["state"] == "offline"
    assert (
        service.last_seen == original[1]
        or service.last_seen <= service.connection_observation[1]
    )


async def _cached(payload):
    return copy.deepcopy(payload)


@pytest.mark.parametrize("previous", ["offline", "degraded"])
async def test_direct_existing_read_can_update_contact_after_cached_failure(
    service, previous
):
    observe(service, previous, age=10)
    service.client = SimpleNamespace(
        json=AsyncMock(return_value={"data": {}, "meta": {}})
    )
    await service.read("/healthz", 0)
    result = await observe_integration(app_for(service))
    assert result["resources"][0]["state"] == "connected"
    assert result["resources"][0]["source"] == "device_cache"
    assert result["connectivity"] == "ok"


async def test_selection_and_close_retire_previous_device_observations(service):
    observe(service)
    service.read_observations["/healthz"] = (time.monotonic(), time.time(), True)
    await service.select(None, service.session)
    assert service.connection_observation is None and not service.read_observations
    assert all(
        row["state"] == "unverified"
        for row in (await observe_integration(app_for(service)))["resources"]
    )
    observe(service)
    service.read_observations["/healthz"] = (time.monotonic(), time.time(), True)
    await service.close()
    assert service.connection_observation is None and not service.read_observations


async def test_new_core_loader_normalizes_resource_identity_and_preserves_metadata(
    service,
):
    observe(service, "offline")
    app = app_for(service)
    app.state.optional_module_diagnostics = [
        {
            "module_id": "rider",
            "state": "loaded",
            "integration_provider_import": (
                "venom_module_rider.integrations:observe_integration"
            ),
        }
    ]
    item = IntegrationEntry(
        id="module:rider", label="Rider-Pi", scope="external", kind="module"
    )
    await apply_module_observations(app, [item])
    assert item.resources_complete and item.resource_count == 2
    assert item.active_resource_count is None and item.connectivity.value == "down"
    assert item.resources[0].id.startswith("module:rider:")
    assert (
        item.resources[0].selected and item.resources[0].configuration == "configured"
    )
    assert (
        item.resources[0].mode == "device"
        and item.resources[0].source == "device_cache"
    )


async def test_failed_fresh_read_is_not_hidden_by_successful_cache_hits(service):
    failure = {"enabled": False}

    async def response(path):
        if failure["enabled"]:
            from venom_module_rider.errors import RiderError

            raise RiderError("pi_unreachable", 503)
        return {"data": {}, "meta": {}}

    service.client = SimpleNamespace(json=AsyncMock(side_effect=response))
    await service.status("overview")
    last_seen = service.last_seen
    failure["enabled"] = True
    # Only control is refreshed now; cached health/settings/telemetry cannot
    # establish fresh contact after the transport stopped responding.
    assert (await service.status("overview"))["connection"]["status"] == "degraded"
    result = await observe_integration(app_for(service))
    assert result["resources"][0]["state"] == "offline"
    assert service.last_seen == last_seen


async def test_mixed_fresh_reads_are_degraded_without_unknown_device_leak(service):
    async def response(path):
        if path == "/api/control/state":
            from venom_module_rider.errors import RiderError

            raise RiderError("pi_unreachable", 503)
        return {"data": {}, "meta": {}}

    service.client = SimpleNamespace(json=AsyncMock(side_effect=response))
    await service.status("overview")
    result = await observe_integration(app_for(service))
    assert result["connectivity"] == "degraded"
    assert result["resources"][0]["state"] == "connected"
    assert result["resources"][1]["state"] == "unverified"


@pytest.mark.parametrize(
    "runtime_version,package_version",
    [("1.5.0", "99.0.0"), ("1.6.0", "1.0.0")],
)
def test_real_core_loader_checks_runtime_abi_before_rider_import(
    runtime_version, package_version
):
    root = Path(__file__).parents[1]
    code = textwrap.dedent(
        """
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        from fastapi import FastAPI
        from venom_core.services import module_registry

        runtime_version, package_version, manifest = sys.argv[1:]
        settings = SimpleNamespace(
            API_OPTIONAL_MODULES=f"manifest:{manifest}",
            CORE_MODULE_API_VERSION="1.0.0",
            CORE_RUNTIME_VERSION=runtime_version,
            APP_VERSION=package_version,
        )
        app = FastAPI()
        assert not any(name.startswith("venom_module_rider") for name in sys.modules)
        original_import = module_registry.importlib.import_module
        with patch.object(
            module_registry.importlib, "import_module", wraps=original_import
        ) as imports:
            included = module_registry.include_optional_api_routers(app, settings)

        rider_imports = [
            call.args[0]
            for call in imports.call_args_list
            if call.args[0].startswith("venom_module_rider")
        ]
        diagnostic = app.state.optional_module_diagnostics[0]
        if runtime_version == "1.5.0":
            assert included == []
            assert rider_imports == []
            assert not any(
                name.startswith("venom_module_rider") for name in sys.modules
            )
            assert diagnostic["state"] == "unavailable"
            assert diagnostic["reason_code"] == "incompatible_module"
        else:
            assert included == ["rider"]
            assert "venom_module_rider.api.routes" in rider_imports
            assert "venom_module_rider.api.routes" in sys.modules
            assert diagnostic["state"] == "loaded"
            assert diagnostic["reason_code"] == "router_loaded"
        """
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            runtime_version,
            package_version,
            str(root / "module.json"),
        ],
        cwd=root,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr


def test_manifest_hook_is_opt_in_and_router_import_does_not_load_it():
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "module.json").read_text())
    assert manifest["backend"]["min_core_version"] == "1.6.0"
    assert (
        manifest["backend"]["integration_provider_import"]
        == "venom_module_rider.integrations:observe_integration"
    )
    # Fresh interpreter prevents prior tests importing the hook from obscuring
    # compatibility: importing the router must not import the optional adapter.
    code = (
        "import sys; import venom_module_rider.api.routes; "
        "assert 'venom_module_rider.integrations' not in sys.modules"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    assert callable(
        importlib.import_module("venom_module_rider.integrations").observe_integration
    )


async def test_unknown_cached_connection_state_never_looks_connected(service):
    service.connection_observation = (time.monotonic(), time.time(), "unexpected")
    row = (await observe_integration(app_for(service)))["resources"][0]
    assert row["state"] == "unverified" and row["observed_at"] is None


async def test_direct_failed_read_invalidates_previous_online_without_status(service):
    from venom_module_rider.errors import RiderError

    observe(service, "online", age=10)
    previous = service.connection_observation
    service.client = SimpleNamespace(
        json=AsyncMock(side_effect=RiderError("pi_unreachable", 503))
    )
    with pytest.raises(RiderError):
        await service.read("/healthz", 0)
    result = await observe_integration(app_for(service))
    assert result["resources"][0]["state"] == "offline"
    assert result["connectivity"] == "down"
    assert result["resources"][0]["reason_code"] == "device_offline"
    assert service.connection_observation == previous  # Provider is read-only.
    stamp = result["resources"][0]["observed_at"]
    assert (
        stamp
        == (await observe_integration(app_for(service)))["resources"][0]["observed_at"]
    )


async def test_mixed_direct_read_evidence_reports_degraded_without_status(service):
    now, wall = time.monotonic(), time.time()
    service.read_observations = {
        "/healthz": (now - 2, wall - 2, True),
        "/api/control/state": (now - 1, wall - 1, False),
    }
    result = await observe_integration(app_for(service))
    assert result["connectivity"] == "degraded"
    assert result["resources"][0]["state"] == "connected"
    assert datetime.fromisoformat(
        result["resources"][0]["observed_at"]
    ).timestamp() == pytest.approx(wall - 1)
    service.read_observations["/healthz"] = (now - 70, wall - 70, True)
    assert (await observe_integration(app_for(service)))["connectivity"] == "down"


@pytest.mark.parametrize(
    "label",
    [
        "ssh://private.internal",
        "2001:db8::1",
        "http://internal.local",
        "device@example.invalid",
    ],
)
async def test_pasted_addresses_are_not_exported_as_labels(service, label):
    device = Device("one", label, "https://private.invalid")
    service.devices["one"] = service.device = device
    row = (await observe_integration(app_for(service)))["resources"][0]
    assert row["label"] == "Rider-Pi"


@pytest.mark.parametrize(
    ("code", "status", "connected"),
    [
        ("camera_pipeline_unsupported", 404, True),
        ("navigation_config_unsupported", 404, True),
        ("unauthorized", 401, True),
        ("pi_unreachable", 503, False),
        ("pi_operation_timeout", 504, False),
    ],
)
async def test_direct_http_error_proves_contact_but_transport_failure_does_not(
    service, code, status, connected
):
    from venom_module_rider.errors import RiderError

    observe(service, "online", age=10)
    service.client = SimpleNamespace(
        json=AsyncMock(side_effect=RiderError(code, status))
    )
    with pytest.raises(RiderError, match=code):
        await service.read("/api/resource/camera/pipeline", 0)
    result = await observe_integration(app_for(service))
    assert result["connectivity"] == ("ok" if connected else "down")
    assert result["resources"][0]["state"] == ("connected" if connected else "offline")
    assert service.read_observations["/api/resource/camera/pipeline"][2] is connected
