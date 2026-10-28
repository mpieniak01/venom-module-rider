"""Hardware-free producer fixture for delegated pulses and audio playback."""

import hmac
import math
import os
import time
import uuid

_AUDIO_CONTRACT = "rider.audio"


def attach(state):
    state.agent_epoch = uuid.uuid4().hex
    state.agent_seq = -1


def manual(state):
    state.agent_epoch = uuid.uuid4().hex
    state.agent_seq = -1


async def response(state, path, body, request, envelope):
    """Route simulated audio and playback operations within the demo domain."""
    if path == "/api/control/agent/state":
        safety = state.safety()
        safety.update(agent_epoch=state.agent_epoch, agent_last_seq=state.agent_seq)
        return envelope({"safety": safety, "server_time": time.time()})
    if path == "/api/stop":
        manual(state)
        return state.control({"action": "stop", **body})
    if path not in {
        "/api/control/agent/pulse",
        "/api/voice/playback",
        "/api/voice/playback/cancel",
    }:
        return None
    token = os.getenv("RIDER_AGENT_TOKEN", "")
    supplied = request.headers.get("Authorization", "").encode()
    if not token or not hmac.compare_digest(supplied, ("Bearer " + token).encode()):
        return envelope({"error": "agent_access_denied"}, status=403)
    if path == "/api/control/agent/pulse":
        fields = {
            "generation",
            "agent_epoch",
            "seq",
            "ts",
            "ttl_ms",
            "settings_revision",
            "request_id",
            "intent_id",
            "channel",
            "direction",
        }
        if (
            not isinstance(body, dict)
            or set(body) != fields
            or not isinstance(body["channel"], str)
            or body["channel"] not in {"chat", "voice"}
            or type(body["seq"]) is not int
            or not 0 <= body["seq"] < 2**63
            or type(body["ts"]) not in {int, float}
            or not math.isfinite(body["ts"])
            or type(body["ttl_ms"]) is not int
            or not 1 <= body["ttl_ms"] <= 500
            or type(body["settings_revision"]) is not int
            or body["settings_revision"] < 0
            or not isinstance(body["direction"], str)
            or any(
                not isinstance(body[key], str) or not 1 <= len(body[key]) <= 128
                for key in ("generation", "agent_epoch", "request_id", "intent_id")
            )
        ):
            return envelope({"error": "agent_payload_invalid"}, status=400)
        if state.state != "ARMED":
            return envelope({"error": "motion_not_armed"}, status=409)
        if (
            body["generation"] != state.generation
            or body["agent_epoch"] != state.agent_epoch
            or body["seq"] <= state.agent_seq
        ):
            return envelope({"error": "agent_intent_stale"}, status=409)
        if state.safety()["moving"]:
            return envelope({"error": "manual_priority"}, status=409)
        if (
            body["settings_revision"] != state.controls["revision"]
            or body["direction"] not in state.controls["directions"]
        ):
            return envelope({"error": "manual_settings_stale"}, status=409)
        if not 0 <= time.time() - body["ts"] <= body["ttl_ms"] / 1000:
            return envelope({"error": "expired_command"}, status=409)
        state.agent_seq = body["seq"]
        duration = state.controls["directions"][body["direction"]]["duration"]
        state.deadline = time.monotonic() + duration
        ack = {
            "action": "move",
            "source": "agent",
            "execution_context": "agent",
            "channel": body["channel"],
            "request_id": body["request_id"],
            "accepted": True,
            "physical_completion": "not_applicable",
        }
        state.ack = ack
        state.history.insert(0, ack)
        return envelope(
            {"ack": ack, "safety": state.safety(), "quality": {"simulated": True}}
        )
    if path.endswith("/cancel"):
        return envelope(
            {"cancelled": True, "quality": {"simulated": True}}, _AUDIO_CONTRACT
        )
    raw = await request.body()
    if (
        not 0 < len(raw) <= 2 * 1024 * 1024
        or request.headers.get("Content-Type") != "audio/wav"
    ):
        return envelope(
            {"error": "playback_input_invalid"}, _AUDIO_CONTRACT, status=413
        )
    return envelope(
        {
            "state": "played",
            "quality": {"simulated": True},
            "playback_id": request.headers.get("X-Rider-Playback-Id"),
        },
        _AUDIO_CONTRACT,
    )
