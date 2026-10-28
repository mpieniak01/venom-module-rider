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
    devices, default, demo = runner.targets(None, None)
    assert demo and default == "demo" and devices == []
    with pytest.raises(ValueError):
        runner.targets(None, "robot")


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
    devices, default, demo = runner.targets(path, None)
    assert not demo and default == "" and devices[0]["id"] == "robot"
    assert runner.targets(path, "robot")[1] == "robot"
    with pytest.raises(ValueError):
        runner.targets(path, "missing")
    monkeypatch.setenv("RIDER_DEMO_ONLY", "true")
    with pytest.raises(Exception, match="invalid_device_configuration"):
        runner.targets(path, "robot")


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


def test_device_launcher_uses_builtin_demo_without_a_fixture_endpoint(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("RIDER_DEMO_ONLY", raising=False)
    path = tmp_path / "devices.json"
    production = {
        "id": "prod",
        "label": "Prod fixture",
        "mode": "device",
        "url": "http://127.0.0.1:18881",
    }
    path.write_text(json.dumps([production]))
    devices, selected, isolated = runner.targets(path, "demo")
    assert selected == "demo" and not isolated
    assert [d["id"] for d in devices] == ["prod"]
    assert runner.targets(path, "prod")[1] == "prod"
    path.write_text(json.dumps([{**production, "id": "demo"}]))
    with pytest.raises(ValueError, match="reserved"):
        runner.targets(path, None)


def test_launcher_starts_only_host_api_and_ui_without_a_demo_listener(
    tmp_path, monkeypatch
):
    core = tmp_path / "core"
    module = core / "modules/venom-module-rider"
    (module / "scripts").mkdir(parents=True)
    (core / ".venv/bin").mkdir(parents=True)
    (core / ".venv/bin/python").touch()
    (core / "web-next/node_modules/next").mkdir(parents=True)
    monkeypatch.setattr(runner, "__file__", str(module / "scripts/run_demo.py"))
    monkeypatch.setattr(runner.sys, "argv", ["run_demo.py", "--core-root", str(core)])
    monkeypatch.setattr(runner.signal, "signal", lambda *args: None)
    checked_ports, children, probes = [], [], []
    monkeypatch.setattr(
        runner, "free_port", lambda port: checked_ports.append(port) or True
    )
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: None)

    class Process:
        stopped = False

        def __init__(self, command, **kwargs):
            self.pid = len(children) + 100
            children.append((command, kwargs, self))

        def poll(self):
            return 0 if self.stopped else None

        def terminate(self):
            self.stopped = True

        def wait(self, **kwargs):
            return 0

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(runner.subprocess, "Popen", Process)

    class Opener:
        def open(self, url, **kwargs):
            probes.append(url)
            return Response()

    monkeypatch.setattr(runner, "local_api_opener", Opener)

    def finish(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(runner.time, "sleep", finish)
    with pytest.raises(KeyboardInterrupt):
        runner.main()
    assert checked_ports == [8800, 3300]
    assert len(children) == 2
    assert "uvicorn" in children[0][0]
    assert "node_modules/next/dist/bin/next" in children[1][0]
    assert json.loads(children[0][1]["env"]["RIDER_DEVICES_JSON"]) == []
    assert children[0][1]["env"]["RIDER_DEFAULT_DEVICE"] == "demo"
    api_env = children[0][1]["env"]
    assert len(api_env["RIDER_COMPUTE_TOKEN"]) >= 32
    assert len(api_env["RIDER_AGENT_TOKEN"]) >= 32
    assert api_env["RIDER_COMPUTE_TOKEN"] != api_env["RIDER_AGENT_TOKEN"]
    assert (
        children[1][1]["env"]["RIDER_COMPUTE_TOKEN"] == api_env["RIDER_COMPUTE_TOKEN"]
    )
    assert probes == [
        "http://127.0.0.1:8800/api/v1/rider/config",
        "http://127.0.0.1:3300/rider",
    ]
    assert all(process.stopped for _, _, process in children)


@pytest.mark.parametrize("value", ["0", "-1", "65536", "NaN"])
def test_launcher_rejects_invalid_ports(value):
    with pytest.raises(runner.argparse.ArgumentTypeError):
        runner.loopback_port(value)


def test_launcher_accepts_valid_loopback_port():
    assert runner.loopback_port("8800") == 8800
