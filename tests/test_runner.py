import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "rider_runner", Path(__file__).parents[1] / "scripts/run_demo.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_demo_cannot_use_an_environment_device(monkeypatch):
    monkeypatch.setenv(
        "RIDER_DEVICES_JSON",
        '[{"id":"robot","label":"Robot","url":"http://robot.invalid","mode":"device"}]',
    )
    devices, default, demo = runner.targets(None, None, 18880)
    assert demo and default == "demo" and devices[0]["url"] == "http://127.0.0.1:18880"
    with pytest.raises(ValueError):
        runner.targets(None, "robot", 18880)


def test_live_requires_explicit_validated_file(tmp_path, monkeypatch):
    monkeypatch.delenv("RIDER_DEMO_ONLY", raising=False)
    path = tmp_path / "devices.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "robot",
                    "label": "Robot",
                    "url": "http://robot.invalid",
                    "mode": "device",
                }
            ]
        )
    )
    devices, default, demo = runner.targets(path, None, 18880)
    assert not demo and default == "" and devices[0]["id"] == "robot"
    assert runner.targets(path, "robot", 18880)[1] == "robot"
    with pytest.raises(ValueError):
        runner.targets(path, "missing", 18880)
    monkeypatch.setenv("RIDER_DEMO_ONLY", "true")
    with pytest.raises(Exception, match="invalid_device_configuration"):
        runner.targets(path, "robot", 18880)


@pytest.mark.parametrize("profile", [None, "llm_off"])
def test_isolated_demo_keeps_llm_disabled(profile):
    assert runner.resolve_runtime_profile(True, profile) == "llm_off"


@pytest.mark.parametrize("profile", ["full", "light"])
def test_isolated_demo_rejects_llm_runtime_override(profile):
    with pytest.raises(ValueError, match="isolated demo requires"):
        runner.resolve_runtime_profile(True, profile)


@pytest.mark.parametrize(
    "requested,expected",
    [(None, "full"), ("full", "full"), ("light", "light"), ("llm_off", "llm_off")],
)
def test_configured_host_uses_operator_runtime_profile(requested, expected):
    assert runner.resolve_runtime_profile(False, requested) == expected


def test_invalid_runtime_profile_is_rejected():
    with pytest.raises(ValueError, match="Unsupported runtime profile"):
        runner.resolve_runtime_profile(False, "unknown")


def test_audio_is_opt_in_for_configured_host():
    assert runner.resolve_audio_enabled(False) is False
    assert runner.resolve_audio_enabled(False, True) is True
    assert runner.resolve_audio_enabled(True) is False
    with pytest.raises(ValueError, match="explicitly configured device host"):
        runner.resolve_audio_enabled(True, True)
