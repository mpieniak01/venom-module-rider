"""Loopback-only Pi contract fixture. It imports no robot, UART or GPIO driver."""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image, ImageDraw

CONTRACTS = json.loads(
    (Path(__file__).parent / "fixtures/pi-contracts.json").read_text()
)


def envelope(data, family="rider.safety", version=1, *, status=200, operation=None):
    return JSONResponse(
        {
            "meta": {
                "envelope_version": 1,
                "contract_id": family,
                "contract_version": version,
                "host": "pi",
                "source": "demo",
                "observed_at": data.get("observed_at"),
                "generated_at": time.time(),
                "server_time": time.time(),
                "stale": False,
                "offline": False,
                "partial": False,
            },
            "ok": status < 400,
            "data": data,
            "error": {"code": data["error"]} if status >= 400 else None,
            "operation": operation,
        },
        status_code=status,
        media_type="application/vnd.rider.api+json;version=1",
    )


class DemoPi:
    def __init__(self):
        self.controls = copy.deepcopy(CONTRACTS["settings"])
        self.library = copy.deepcopy(CONTRACTS["face"])
        self.runtime = {"running": False, **copy.deepcopy(self.library)}
        self.generation = uuid.uuid4().hex
        self.instance = "demo-pi"
        self.state = "DISARMED"
        self.seq = -1
        self.deadline = 0.0
        self.height: dict[str, Any] = {"value": 95, "ts": None}
        self.balance: dict[str, Any] = {"enabled": True, "ts": None}
        self.camera_on = False
        self.lcd_on = False
        self.goals = {"s2_camera_preview": False, "s5_voice": False, "s0_core": True}
        self.trace: list[dict] = []
        self.history: list[dict] = []
        self.ack = None
        self.offline = False
        self.stale = False

    def safety(self):
        now = time.time()
        moving = self.deadline > time.monotonic()
        if not moving:
            self.deadline = 0.0
        return {
            "contract_version": 1,
            "state": self.state,
            "ready": not self.stale,
            "can_move": self.state == "ARMED" and not self.stale,
            "panel_motion_enabled": self.state == "ARMED" and not self.stale,
            "test_motion_enabled": False,
            "owner": "manual" if self.state == "ARMED" else None,
            "execution_context": "panel" if self.state == "ARMED" else None,
            "arming_mode": "operator",
            "generation": self.generation,
            "instance_id": self.instance,
            "last_seq": self.seq,
            "observed_at": now - 10 if self.stale else now,
            "stale": self.stale,
            "hardware_ready": True,
            "read_only": self.state != "ARMED",
            "moving": moving,
            "deadline": self.deadline,
            "output": {"lx": 0.1 if moving else 0, "az": 0},
            "target": {"lx": 0.1 if moving else 0, "az": 0},
            "last_ack": self.ack,
            "lease_remaining_s": None,
            "manual_settings": copy.deepcopy(self.controls),
            "actuators": {"height": self.height, "balance": self.balance},
            "capabilities": {
                name: {
                    "available": name != "action",
                    "stop_verified": name in {"move", "turn"},
                    "verification": "fixture",
                    "physical_completion": "not_applicable",
                    **({"parameter": descriptor} if descriptor else {}),
                }
                for name, descriptor in {
                    "move": None,
                    "turn": None,
                    "action": None,
                    **self.controls["parameter_schema"]["actuator_fields"],
                }.items()
            },
        }

    def control(self, body):
        action = body.get("action")
        if action in {"stop", "disarm"}:
            self.state = "DISARMED"
            self.deadline = 0.0
            self.generation = uuid.uuid4().hex
            self.seq = -1
        else:
            if (
                body.get("generation") != self.generation
                or type(body.get("seq")) is not int
                or body["seq"] <= self.seq
            ):
                return envelope({"error": "stale_generation_or_sequence"}, status=409)
            if (
                not isinstance(body.get("ts"), (int, float))
                or time.time() - body["ts"] > body.get("ttl_ms", 0) / 1000
                or body["ts"] > time.time() + 0.1
            ):
                return envelope({"error": "expired_command"}, status=409)
            if action == "clear_estop":
                self.state = "DISARMED"
                self.generation = uuid.uuid4().hex
                self.deadline = 0.0
            elif action == "arm":
                if body.get("operator_consent") is not True or not body.get(
                    "operator_plan"
                ):
                    return envelope({"error": "operator_consent_required"}, status=400)
                self.state = "ARMED"
            elif action in {"move", "height", "balance"}:
                if self.state != "ARMED":
                    return envelope({"error": "motion_not_armed"}, status=409)
                if body.get("settings_revision") != self.controls["revision"]:
                    return envelope({"error": "manual_settings_stale"}, status=409)
                if action == "move":
                    if body.get("mode") == "release":
                        self.deadline = 0.0
                    else:
                        duration = (
                            self.controls["control_modes"]["hold_timeout_ms"] / 1000
                            if body.get("mode") == "hold"
                            else self.controls["directions"][body["direction"]][
                                "duration"
                            ]
                        )
                        self.deadline = time.monotonic() + duration
                elif action == "height":
                    descriptor = self.controls["parameter_schema"]["actuator_fields"][
                        "height"
                    ]
                    value = body.get("height")
                    if (
                        type(value) is not int
                        or not descriptor["minimum"] <= value <= descriptor["maximum"]
                    ):
                        return envelope({"error": "invalid_height"}, status=400)
                    self.height = {"value": value, "ts": time.time()}
                else:
                    if type(body.get("enabled")) is not bool:
                        return envelope({"error": "invalid_balance"}, status=400)
                    self.balance = {"enabled": body["enabled"], "ts": time.time()}
            self.seq = body["seq"]
        self.ack = {
            "request_id": body.get("request_id"),
            "action": action,
            "source": body.get("source"),
            "seq": body.get("seq"),
            "command_generation": body.get("generation"),
            "observed_at": time.time(),
            "accepted": True,
            "status": "executed",
            "reason": "fixture_accepted",
            "physical_completion": "not_applicable",
        }
        self.history.insert(0, self.ack)
        self.history = self.history[:10]
        return envelope(
            {
                "safety": self.safety(),
                "ack": self.ack,
                "confirmed": True,
                "pending": False,
                "request_id": body.get("request_id"),
                "physical_completion": "not_applicable",
            },
            operation={
                "status": "executed",
                "confirmed": True,
                "actuation": False,
                "physical_completion": "not_applicable",
                "evidence": "fixture",
                "request_id": body.get("request_id"),
            },
        )

    def settings(self, body):
        if body.get("revision") != self.controls["revision"]:
            return envelope({"error": "manual_settings_conflict"}, status=409)
        if self.safety()["moving"]:
            return envelope({"error": "manual_settings_requires_idle"}, status=409)
        candidate = copy.deepcopy(self.controls)
        candidate["values"].update(body.get("values") or {})
        for direction, patch in (body.get("directions") or {}).items():
            if direction not in candidate["directions"]:
                return envelope({"error": "manual_direction"}, status=400)
            candidate["directions"][direction].update(patch)
        for row in candidate["directions"].values():
            if (
                any(
                    type(v) not in (int, float) or not math.isfinite(v)
                    for v in row.values()
                )
                or not 0 <= row["minimum"] <= row["maximum"] <= 1
                or row["speed"] != 0
                and not row["minimum"] <= row["speed"] <= row["maximum"]
                or not 0 < row["step"] <= max(row["maximum"] - row["minimum"], 1)
                or not 0.01 <= row["duration"] <= 1
            ):
                return envelope(
                    {"error": "manual_settings_direction_range"}, status=400
                )
        profile = body.get("profile")
        if profile:
            action = profile.get("action")
            id_ = profile.get("id")
            if action == "create":
                if len(candidate["profiles"]) >= 16:
                    return envelope({"error": "manual_profile_limit"}, status=400)
                id_ = uuid.uuid4().hex
                candidate["profiles"][id_] = {
                    "name": profile.get("name", "Demo"),
                    "values": copy.deepcopy(candidate["values"]),
                    "directions": copy.deepcopy(candidate["directions"]),
                }
                candidate["active_profile"] = id_
            elif id_ not in candidate["profiles"]:
                return envelope({"error": "manual_profile_unknown"}, status=400)
            elif action == "apply":
                row = candidate["profiles"][id_]
                candidate["values"] = copy.deepcopy(row["values"])
                candidate["directions"] = copy.deepcopy(row["directions"])
                candidate["active_profile"] = id_
            elif action == "save":
                candidate["profiles"][id_].update(
                    values=copy.deepcopy(candidate["values"]),
                    directions=copy.deepcopy(candidate["directions"]),
                    name=profile.get("name", candidate["profiles"][id_]["name"]),
                )
                candidate["active_profile"] = id_
            elif action == "rename":
                candidate["profiles"][id_]["name"] = profile.get("name", "Demo")
            elif action == "delete":
                del candidate["profiles"][id_]
                if candidate["active_profile"] == id_:
                    candidate["active_profile"] = None
            else:
                return envelope({"error": "manual_profile_action"}, status=400)
        candidate["revision"] += 1
        candidate["observed_at"] = time.time()
        active = candidate["profiles"].get(candidate["active_profile"])
        candidate["profile_modified"] = bool(
            active
            and any(active[key] != candidate[key] for key in ("values", "directions"))
        )
        self.controls = candidate
        return envelope(copy.deepcopy(candidate), "rider.calibration")

    def resource(self, name):
        state = (
            self.camera_on
            if name == "camera"
            else self.lcd_on
            if name == "lcd"
            else False
        )
        return {
            "name": name,
            "contract_version": 2,
            "state": "on" if state else "off",
            "known": True,
            "free": not state,
            "holders": [],
            "consumers": [],
            "activity": {
                "state": "on" if state else "off",
                "enabled": state,
                "on": state,
                "known": True,
                "observed_at": time.time(),
            },
            "observed_at": time.time(),
            "generation": 0,
        }

    def image(self, spec=None, *, camera=False):
        image = Image.new("RGB", (320, 180) if camera else (240, 240), "#0d1c2e")
        draw = ImageDraw.Draw(image)
        if camera:
            draw.rectangle((32, 30, 286, 142), outline="#61c8d4", width=3)
            draw.text((45, 65), "DEMO - no physical camera", fill="#cce3ee")
        else:
            initial = (spec or {}).get("initial") or {}
            dx = float(initial.get("eyes.dx", 0)) * 10
            dy = float(initial.get("eyes.dy", 0)) * 10
            skin = (spec or {}).get("skin", "none")
            if skin != "none":
                draw.ellipse((22, 12, 218, 229), fill="#b4c6d5")
            blink = float(initial.get("eyes.blink", 0))
            for x in (79, 161):
                draw.ellipse((x - 22, 72, x + 22, 122), fill="#e1f9ff")
                draw.ellipse(
                    (x - 9 + dx, 85 + dy, x + 9 + dx, 111 + dy), fill="#30b9d1"
                )
                if blink > 0.5:
                    draw.line((x - 22, 98, x + 22, 98), fill="#0d1c2e", width=9)
            curve = float(initial.get("mouth.curve", 0.3))
            if curve < 0:
                draw.arc((78, 165, 162, 210), 190, 350, fill="#7edce5", width=5)
            else:
                draw.arc((78, 145, 162, 190), 10, 170, fill="#7edce5", width=5)
        output = io.BytesIO()
        image.save(output, format="PNG")
        return Response(
            output.getvalue(),
            media_type="image/png",
            headers={
                "Cache-Control": "no-store",
                "X-Rider-Source": "demo",
                "X-Rider-Frame-Id": str(time.monotonic_ns()),
                "X-Rider-Observed-At": str(time.time()),
                "X-Rider-Stale": "false",
            },
        )


def create_app(pi: DemoPi | None = None):
    app = FastAPI(title="Rider Pi fixture — no hardware")
    state = pi or DemoPi()
    app.state.pi = state

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "DELETE"])
    async def dispatch(path: str, request: Request):
        path = "/" + path
        body = await request.json() if request.method == "POST" else {}
        state.trace.append({"path": path, "method": request.method, "body": body})
        state.trace = state.trace[-2000:]
        if path == "/__demo__/trace":
            return {
                "items": state.trace,
                "state": state.state,
                "camera_on": state.camera_on,
            }
        if path == "/__demo__/fault":
            state.offline = body.get("offline", False)
            state.stale = body.get("stale", False)
            return {"ok": True}
        if state.offline:
            return envelope({"error": "demo_offline"}, status=503)
        if path == "/api/control/state":
            return envelope(
                {
                    "server_time": time.time(),
                    "safety": state.safety(),
                    "height": state.height,
                    "balance": state.balance,
                    "ack": state.ack,
                }
            )
        if path in {"/api/control", "/api/safety"}:
            return state.control(body)
        if path == "/api/calibration/controls":
            return (
                state.settings(body)
                if body
                else envelope(copy.deepcopy(state.controls), "rider.calibration")
            )
        if path == "/state":
            sensors = {
                name: {
                    "value": value,
                    "unit": unit,
                    "ready": True,
                    "stale": state.stale,
                    "observed_at": time.time(),
                    "quality": "fixture",
                }
                for name, value, unit in (
                    ("battery", 0.89, "ratio"),
                    ("roll", -0.8, "sdk_raw"),
                    ("pitch", -3.2, "sdk_raw"),
                    ("yaw", 8.4, "sdk_raw"),
                )
            }
            return envelope(
                {
                    "devices": {
                        "xgo": {
                            "source": "demo",
                            "instance_id": state.instance,
                            "sensor_quality": {"sensors": sensors, "ready": True},
                            "fw": "DEMO",
                            "observed_at": time.time(),
                        }
                    },
                    "camera": state.resource("camera"),
                },
                "rider.telemetry",
            )
        if path == "/healthz":
            return envelope(
                {
                    "ok": True,
                    "uptime_s": 120,
                    "bus": {"last_msg_age_s": 0.1, "last_heartbeat_age_s": 0.2},
                    "camera": state.resource("camera"),
                    "lcd": state.resource("lcd"),
                },
                "rider.health",
            )
        if path == "/sysinfo":
            stamps = [time.time() - (29 - i) * 2 for i in range(30)]
            cpu = [round(14 + 2 * math.sin(i), 1) for i in range(30)]
            return envelope(
                {
                    "cpu_pct": cpu[-1],
                    "cpu_peak_pct": 19,
                    "mem_pct": 24,
                    "mem_used_mb": 240,
                    "mem_total_mb": 1000,
                    "disk_used_gb": 8,
                    "disk_total_gb": 32,
                    "disk_pct": 25,
                    "temp_c": 42,
                    "load1": 0.3,
                    "load5": 0.2,
                    "load15": 0.2,
                    "hist_ts": stamps,
                    "hist_cpu": cpu,
                    "hist_cpu_peak": [19] * 30,
                    "hist_mem": [24] * 30,
                    "sampling": {"period_s": 2},
                    "os_release": "Demo Linux",
                    "ts": time.time(),
                },
                "rider.system",
            )
        if path.startswith("/api/resource/"):
            name = path.split("/")[-1]
            if body:
                on = body.get("action") == "on"
                if name == "camera":
                    state.camera_on = on
                if name == "lcd":
                    state.lcd_on = on
            return envelope(state.resource(name), "rider.resources")
        if path.startswith("/camera/"):
            return (
                state.image(camera=True)
                if state.camera_on
                else envelope(
                    {"error": "camera_off"},
                    status=404,
                )
            )
        if path == "/api/logic/catalog":
            return envelope(
                {
                    "contract_version": 2,
                    "units": CONTRACTS["catalog"]["units"],
                    "features": [
                        copy.deepcopy(row)
                        for row in CONTRACTS["catalog"]["features"]
                        if row["name"] in state.goals
                    ],
                },
                "rider.catalog",
                2,
            )
        if path == "/api/logic/state":
            return envelope(
                {
                    "contract_version": 2,
                    "features": {
                        name: {
                            "enabled": enabled,
                            "state": "running" if enabled else "stopped",
                        }
                        for name, enabled in state.goals.items()
                    },
                },
                "rider.tasks",
                2,
            )
        if path.startswith("/api/logic/feature/"):
            name = path.split("/")[-1]
            if name not in state.goals:
                return envelope({"error": "unknown_goal"}, status=404)
            state.goals[name] = body["enabled"]
            if name == "s2_camera_preview":
                state.camera_on = body["enabled"]
            return envelope(
                {"name": name, "enabled": body["enabled"]}, "rider.tasks", 2
            )
        if path == "/api/motion/queue":
            return envelope(
                {"kind": "request_history", "items": state.history, "ack": state.ack}
            )
        if path == "/api/services/graph":
            return envelope(
                {
                    "nodes": [
                        {**row, "state": {"active": "active", "source": "demo"}}
                        for row in CONTRACTS["catalog"]["units"]
                    ],
                    "edges": [],
                    "contract_version": 2,
                },
                "rider.service_graph",
                2,
            )
        if path.startswith("/api/services/") and path.endswith("/logs"):
            return envelope(
                {"lines": ["DEMO: no hardware service"], "source": "demo"},
                "rider.services",
                2,
            )
        if path == "/api/face-studio/gestures":
            if body:
                key = body.get("id") or uuid.uuid4().hex
                gesture = {**body, "id": key, "builtin": False}
                state.library["gestures"] = [
                    row for row in state.library["gestures"] if row["id"] != key
                ] + [gesture]
                state.library["revision"] += 1
                return envelope(
                    {"id": key, "gesture": gesture, "actuation": False},
                    "rider.face_studio",
                )
            return envelope(state.library, "rider.face_studio")
        if path.startswith("/api/face-studio/gestures/"):
            key = path.split("/")[-1]
            state.library["gestures"] = [
                row for row in state.library["gestures"] if row["id"] != key
            ]
            return envelope({"actuation": False}, "rider.face_studio")
        if path == "/api/face-studio/frame":
            return state.image(body)
        if path == "/api/face-studio/runtime":
            if body:
                expected = body.pop("if_revision", None)
                if expected != state.library["settings_revision"]:
                    return envelope({"error": "face_settings_conflict"}, status=409)
                state.library["settings"].update(body)
                state.library["settings_revision"] = hashlib.sha256(
                    json.dumps(state.library["settings"], sort_keys=True).encode()
                ).hexdigest()
            return envelope(
                {
                    "running": state.runtime["running"],
                    "settings": state.library["settings"],
                    "settings_revision": state.library["settings_revision"],
                    "descriptors": state.library["descriptors"],
                },
                "rider.face",
            )
        if path == "/api/face-studio/runtime/play":
            state.runtime["running"] = True
            state.lcd_on = body.get("sink") == "lcd"
            return envelope({"running": True, "actuation": False}, "rider.face")
        if path == "/api/face-studio/runtime/stop":
            state.runtime["running"] = False
            state.lcd_on = False
            return envelope({"running": False, "actuation": False}, "rider.face")
        if path == "/api/face-studio/runtime/event":
            return envelope({"accepted": True, "actuation": False}, "rider.face")
        if path == "/api/face-studio/runtime/frame":
            return (
                state.image()
                if state.runtime["running"]
                else envelope(
                    {"error": "face_not_running"},
                    status=404,
                )
            )
        return envelope({"error": "unknown_fixture_route"}, status=404)

    return app


def main():
    import uvicorn

    parser = argparse.ArgumentParser(
        description="Loopback-only, hardware-free Pi fixture"
    )
    parser.add_argument("--port", type=int, default=18880)
    args = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
