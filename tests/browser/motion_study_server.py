"""Isolated ASGI fixture. No credentials, hardware, models or external transport."""

from __future__ import annotations

import hashlib
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from venom_module_rider.api.routes import router
from venom_module_rider.client import PiClient
from venom_module_rider.config import Device
from venom_module_rider.demo import DemoPi, create_app
from venom_module_rider.evidence_contract import validate_evidence
from venom_module_rider.motion_capture import profile, record
from venom_module_rider.motion_contract import MotionCommand, MotionIntent
from venom_module_rider.samples_registry import SamplesRegistry
from venom_module_rider.service import RiderService

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "docs_dev" / "_reports" / "motion-study-browser"


async def seed(service, registry, name, magnitude, qualified=False, firmware=False):
    session = await registry.start(
        name, "fixture", {"scene_group": "motion-study-v1:wood"}
    )
    sid = session["session_id"]
    base = time.monotonic()
    request = {
        "action": "move",
        "request_id": name,
        "settings_revision": 0,
        "device_settings": True,
        "direction": "forward",
        "mode": "pulse",
    }
    snap = record(service, "profile", profile(service, request), [name, "profile"])
    snap["received_mono"] = base - 0.5
    intent = MotionIntent(
        action="move",
        request_id=name,
        direction="forward",
        mode="pulse",
        intensity=0.1,
        duration_s=0.15,
        settings_revision=0,
    )
    event = MotionCommand(
        operation_id=hashlib.md5(name.encode(), usedforsecurity=False).hexdigest(),
        phase="requested",
        intent=intent,
        profile_ref=snap["record_id"],
        outcome="pending",
    )
    requested = record(
        service, "command", event.model_dump(mode="json"), [name, "requested"]
    )
    requested["received_mono"] = base
    event.phase = "result"
    event.outcome = "accepted"
    result = record(service, "command", event.model_dump(mode="json"), [name, "result"])
    result["received_mono"] = base + 0.035
    rows = [snap, requested, result]
    unit = "deg" if qualified else "sdk_raw"
    for i in range(8):
        t = -0.34 + i * 0.34
        observed = 1000.0 + i * 0.34
        quality = {
            "ready": True,
            "stale": False,
            "offline": False,
            "simulated": True,
            "physical_validation": "unverified",
            "sensors": {
                axis: {
                    "ready": True,
                    "stale": False,
                    "offline": False,
                    "instance_id": "fixture-boot",
                    "unit": unit,
                    "observed_at": observed,
                }
                for axis in ("roll", "pitch", "yaw")
            },
        }
        if firmware:
            quality["source"] = "motion-executor"
            quality["telemetry"] = {
                "firmware": "R-1.1.6",
                "contract_id": "rider.telemetry",
                "contract_version": 1,
                "uart_owner": "motion-executor",
            }
        if qualified:
            quality["orientation"] = {
                "convention": "rider-body-zyx-v1",
                "revision": 1,
                "verified": True,
                "qualification_ref": "synthetic-known-poses-v1",
                "instance_id": "fixture-boot",
                "unit": unit,
            }
        rows.append(
            validate_evidence(
                {
                    "record_id": hashlib.sha256((name + str(i)).encode()).hexdigest(),
                    "kind": "imu",
                    "device_id": "fixture",
                    "source_session": service.session,
                    "source_instance_id": "fixture-boot",
                    "source_observed_at": observed,
                    "received_mono": base + t,
                    "received_at": 2000.0 + t,
                    "values": {
                        "roll": magnitude * [0, 1, 6, -4, 2, -1, 0.3, 0][i],
                        "pitch": magnitude * [0, 2, 4, -2, 1, 0, 0, 0][i],
                        "yaw": i * 2.0,
                        "accel_x": i * 0.1,
                        "accel_y": 0.0,
                        "accel_z": 9.81,
                    },
                    "units": {
                        "roll": unit,
                        "pitch": unit,
                        "yaw": unit,
                        "accel_x": "m/s^2",
                        "accel_y": "m/s^2",
                        "accel_z": "m/s^2",
                    },
                    "quality": quality,
                }
            )
        )
    rows.sort(key=lambda r: r["received_mono"])
    await registry.read(registry.store.evidence.append, sid, rows)
    await registry.end(sid)
    return sid


@asynccontextmanager
async def lifespan(app):
    with tempfile.TemporaryDirectory(prefix="rider-motion-fixture-") as folder:
        pi = DemoPi()
        transport = httpx.ASGITransport(app=create_app(pi))
        device = Device("fixture", "Fixture", "http://127.0.0.1:18880", "demo")
        service = RiderService(
            (device,), client_factory=lambda d: PiClient(d, transport=transport)
        )
        await service.select("fixture", service.session)
        await service.status("calibration")
        registry = SamplesRegistry(Path(folder))
        service.samples = registry
        app.state.rider = service
        app.state.rider_samples = registry
        app.state.fixture_sessions = {
            "A": await seed(service, registry, "fixture-A", 1, True),
            "B": await seed(service, registry, "fixture-B", 0.5, True),
            "raw": await seed(service, registry, "fixture-raw", 2),
            "firmware": await seed(
                service, registry, "fixture-firmware", 1, firmware=True
            ),
        }
        try:
            yield
        finally:
            await service.close()
            await registry.close()


app = FastAPI()
app.include_router(router)
# Override the host lifecycle: this fixture has no Venom model/bootstrap services.
app.router.lifespan_context = lifespan


@app.get("/")
async def index():
    return FileResponse(ASSETS / "index.html")


@app.get("/fixture")
async def fixture():
    return {
        "sessions": app.state.fixture_sessions,
        "config": app.state.rider.config(),
        "status": await app.state.rider.status("calibration"),
    }


app.mount("/assets", StaticFiles(directory=ASSETS, check_dir=False), name="assets")
