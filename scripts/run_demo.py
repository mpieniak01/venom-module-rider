#!/usr/bin/env python3
"""Run a hardware-free R1 demo in the existing Venom checkout/environment."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def free_port(port):
    with socket.socket() as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", port)) != 0


def main():
    module = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-root", type=Path, default=module.parent.parent)
    parser.add_argument("--api-port", type=int, default=8800)
    parser.add_argument("--web-port", type=int, default=3300)
    parser.add_argument("--pi-port", type=int, default=18880)
    args = parser.parse_args()
    core = args.core_root.resolve()
    python = core / ".venv/bin/python"
    web = core / "web-next"
    if not python.exists() or not (web / "node_modules/next").exists():
        parser.error("Use the existing Venom environment and frontend dependencies.")
    if module != core / "modules" / module.name:
        parser.error(
            "Keep this module checkout inside the selected core modules/ collection."
        )
    ports = (args.api_port, args.web_port, args.pi_port)
    if len(set(ports)) != 3 or not all(free_port(port) for port in ports):
        parser.error("Choose three distinct available loopback ports.")
    logs = core / "logs"
    logs.mkdir(exist_ok=True)
    for script in (
        "prepare-optional-module-node-paths.mjs",
        "generate-optional-modules.mjs",
    ):
        subprocess.run(["node", "scripts/" + script], cwd=web, check=True)
    api_url = f"http://127.0.0.1:{args.api_port}"
    web_url = f"http://127.0.0.1:{args.web_port}"
    flags = {
        name: "false"
        for name in (
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
        )
    }
    flags.update(
        VENOM_RUNTIME_PROFILE="llm_off",
        ENVIRONMENT_ROLE="dev",
        STORAGE_PREFIX="rider-dev",
        CACHE_NAMESPACE="rider-dev",
        QUEUE_NAMESPACE="rider-dev",
        API_OPTIONAL_MODULES="manifest:" + str(module / "module.json"),
        RIDER_DEMO_ONLY="true",
        RIDER_DEFAULT_DEVICE="demo",
        RIDER_DEVICES_JSON=json.dumps(
            [
                {
                    "id": "demo",
                    "label": "Rider-Pi demo",
                    "mode": "demo",
                    "url": f"http://127.0.0.1:{args.pi_port}",
                }
            ]
        ),
        RIDER_ALLOWED_ORIGINS=web_url + f",http://localhost:{args.web_port}",
        PYTHONPATH=str(core) + ":" + str(module),
    )
    api_env = {**os.environ, **flags}
    # A real-device access token does not belong to the isolated demo.
    api_env.pop("RIDER_ACCESS_TOKEN", None)
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
                "pi",
                [
                    str(python),
                    "-m",
                    "venom_module_rider.demo",
                    "--port",
                    str(args.pi_port),
                ],
                core,
                api_env,
            ),
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
                    "mode": "demo_only",
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
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{args.pi_port}/healthz", timeout=1
                    ):
                        pass
                    ready = True
                    print(
                        f"R1 demo ready: http://localhost:{args.web_port}/rider",
                        flush=True,
                    )
                    print(
                        "Hardware-free Pi fixture; "
                        "LLM and physical IoT bridge disabled.",
                        flush=True,
                    )
                except (OSError, TimeoutError):
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
