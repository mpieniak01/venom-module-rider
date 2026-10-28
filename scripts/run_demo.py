#!/usr/bin/env python3
"""Run API/UI with in-process Demo or an explicit physical-device allowlist."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict
from pathlib import Path

from venom_module_rider.config import configured_devices


def targets(device_file, device_id):
    """Only physical targets need configuration; Demo is provided in-process."""
    if device_file is None:
        if device_id not in {None, "demo"}:
            raise ValueError("Demo supports only the demo device ID.")
        return [], "demo", True
    devices = [
        device
        for device in configured_devices(device_file.read_text())
        if device.mode != "demo"
    ]
    if any(device.id == "demo" for device in devices):
        raise ValueError("The demo ID is reserved for the built-in simulation.")
    if device_id and device_id not in {"demo", *(device.id for device in devices)}:
        raise ValueError("Selected ID is absent from the private device file.")
    return [asdict(device) for device in devices], device_id or "", False


def resolve_runtime_profile(demo_only, requested_profile=None):
    if requested_profile not in {None, "full", "light", "llm_off"}:
        raise ValueError("Unsupported runtime profile.")
    if demo_only:
        if requested_profile not in {None, "llm_off"}:
            raise ValueError("The isolated demo requires the llm_off runtime profile.")
        return "llm_off"
    return requested_profile or "full"


def loopback_port(value):
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "Port must be an integer from 1 to 65535."
        ) from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Port must be an integer from 1 to 65535.")
    return port


def free_port(port):
    with socket.socket() as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", port)) != 0


def resolve_audio_enabled(demo_only: bool, requested: bool = False) -> bool:
    if demo_only and requested:
        raise ValueError("Audio requires an explicitly configured device host.")
    return requested and not demo_only


def main():
    module = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-root", type=Path, default=module.parent.parent)
    parser.add_argument("--api-port", type=loopback_port, default=8800)
    parser.add_argument("--web-port", type=loopback_port, default=3300)
    parser.add_argument(
        "--device-file",
        type=Path,
        help="Explicit private JSON device allowlist; omits the fixture",
    )
    parser.add_argument("--device-id", help="Optional default device ID; never arms")
    parser.add_argument(
        "--runtime-profile",
        choices=("full", "light", "llm_off"),
        help="Runtime profile: device host defaults to full; demo uses llm_off",
    )
    parser.add_argument(
        "--enable-audio",
        action="store_true",
        help="Enable shared host STT/TTS for an explicitly configured device host",
    )
    args = parser.parse_args()
    devices, default_device, demo_only = targets(args.device_file, args.device_id)
    try:
        runtime_profile = resolve_runtime_profile(demo_only, args.runtime_profile)
        audio_enabled = resolve_audio_enabled(demo_only, args.enable_audio)
    except ValueError as exc:
        parser.error(str(exc))
    core = args.core_root.resolve()
    python = core / ".venv/bin/python"
    web = core / "web-next"
    if not python.exists() or not (web / "node_modules/next").exists():
        parser.error("Use the existing Venom environment and frontend dependencies.")
    if module != core / "modules" / module.name:
        parser.error(
            "Keep this module checkout inside the selected core modules/ collection."
        )
    ports = (args.api_port, args.web_port)
    if len(set(ports)) != len(ports) or not all(free_port(port) for port in ports):
        parser.error("Choose distinct available loopback ports.")
    logs = core / "logs"
    logs.mkdir(exist_ok=True)
    for script in (
        "prepare-optional-module-node-paths.mjs",
        "generate-optional-modules.mjs",
    ):
        subprocess.run(["node", "scripts/" + script], cwd=web, check=True)
    api_url = f"http://127.0.0.1:{args.api_port}"
    web_url = f"http://127.0.0.1:{args.web_port}"
    flags = dict.fromkeys(
        (
            "ENABLE_AUTO_DOCUMENTATION",
            "ENABLE_AUTO_GARDENING",
            "ENABLE_HEALTH_CHECKS",
            "ENABLE_AUDIO_INTERFACE",
            "ENABLE_IOT_BRIDGE",
            "ENABLE_ACADEMY",
            "ENABLE_HF_INTEGRATION",
            "ENABLE_IMAGE_GENERATION",
            "ENABLE_CHRONOS",
            "ENABLE_MODEL_ROUTING",
            "ENABLE_INTENT_EMBEDDING_ROUTER",
            "ENABLE_RUNTIME_RETENTION_CLEANUP",
            "LLM_WARMUP_ON_STARTUP",
        ),
        "false",
    )
    flags.update(
        VENOM_RUNTIME_PROFILE=runtime_profile,
        ENABLE_AUDIO_INTERFACE="true" if audio_enabled else "false",
        ENVIRONMENT_ROLE="dev",
        STORAGE_PREFIX="rider-dev",
        CACHE_NAMESPACE="rider-dev",
        QUEUE_NAMESPACE="rider-dev",
        API_OPTIONAL_MODULES="manifest:" + str(module / "module.json"),
        RIDER_DEMO_ONLY="true" if demo_only else "false",
        RIDER_DEFAULT_DEVICE=default_device,
        RIDER_SELECTION_FILE=str(core / "data/modules/rider/panel-selection.json"),
        RIDER_DEVICES_JSON=json.dumps(devices),
        RIDER_ALLOWED_ORIGINS=web_url + f",http://localhost:{args.web_port}",
        PYTHONPATH=str(core) + ":" + str(module),
    )
    api_env = {**os.environ, **flags}
    # A real-device access token does not belong to the isolated demo.
    if demo_only:
        api_env.pop("RIDER_ACCESS_TOKEN", None)
        api_env["RIDER_COMPUTE_TOKEN"] = secrets.token_urlsafe(32)
        api_env["RIDER_AGENT_TOKEN"] = secrets.token_urlsafe(32)
    web_env = {
        **api_env,
        "API_PROXY_TARGET": api_url,
        "NEXT_PUBLIC_WS_BASE": f"ws://127.0.0.1:{args.api_port}",
        "NEXT_PUBLIC_FEATURE_RIDER": "true",
        "NEXT_PUBLIC_ENVIRONMENT_ROLE": "dev",
        "NEXT_TELEMETRY_DISABLED": "1",
        "NEXT_PLAYWRIGHT_DISABLE_SSR_API": "1",
    }
    web_env.pop("NEXT_PUBLIC_API_BASE", None)
    web_env.pop("NEXT_PUBLIC_API_URL", None)
    children = []
    streams = []
    stopping = False

    def stop_signal(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop_signal)
    signal.signal(signal.SIGTERM, stop_signal)
    try:
        specifications = [
            (
                "api",
                [
                    str(python),
                    "-m",
                    "uvicorn",
                    "venom_core.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.api_port),
                ],
                core,
                api_env,
            ),
            (
                "web",
                [
                    "node",
                    "node_modules/next/dist/bin/next",
                    "dev",
                    "--webpack",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(args.web_port),
                ],
                web,
                web_env,
            ),
        ]
        for name, command, cwd, env in specifications:
            log = (logs / f"r1-demo-{name}.log").open("w")
            streams.append(log)
            process = subprocess.Popen(
                command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT
            )
            children.append(process)
        (logs / "r1-demo-processes.json").write_text(
            json.dumps(
                {
                    "launcher_pid": os.getpid(),
                    "child_pids": [p.pid for p in children],
                    "ports": ports,
                    "mode": "demo_only" if demo_only else "configured_devices",
                    "core_root": str(core),
                },
                indent=2,
            )
        )
        deadline = time.monotonic() + 60
        ready = False
        while not stopping:
            if any(process.poll() is not None for process in children):
                raise RuntimeError("Demo process stopped; inspect logs/r1-demo-*.log.")
            if not ready:
                try:
                    with urllib.request.urlopen(
                        api_url + "/api/v1/rider/config", timeout=1
                    ):
                        pass
                    with urllib.request.urlopen(web_url + "/rider", timeout=2):
                        pass
                    ready = True
                    print(
                        f"R1 panel ready: http://localhost:{args.web_port}/rider",
                        flush=True,
                    )
                    print(
                        (
                            "Built-in motion simulation; "
                            "LLM and physical IoT bridge disabled."
                        )
                        if demo_only
                        else (
                            "Configured Pi via API; "
                            "no automatic ARM or hardware activation."
                        ),
                        flush=True,
                    )
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError(
                            "Demo startup failed; inspect logs/r1-demo-*.log."
                        ) from None
            time.sleep(0.5)
    finally:
        for process in children:
            if process.poll() is None:
                process.terminate()
        for process in children:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for stream in streams:
            stream.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
