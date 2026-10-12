"""Bind authenticated voice intent before the inference queue, using host context."""

import time
from dataclasses import dataclass

from venom_core.services.conversation_providers import ConversationContext

from .conversation import create_provider
from .conversation_tools import parse_request
from .errors import RiderError


@dataclass(frozen=True)
class PreparedTurn:
    context: ConversationContext
    revision: int
    urgent: bool
    transcript: str | None = None


def capability(app):
    provider = (
        create_provider(app)
        if getattr(getattr(app, "state", None), "rider", None) is not None
        else None
    )
    config = provider.config() if provider else {"revision": 0}
    ready = bool(provider and config.get("enabled") and config.get("device_id"))
    return {
        "installed": True,
        "configured": ready,
        "ready": ready,
        "available": ready,
        "runtime": {
            "provider": "venom_conversation",
            "model": "typed_rider_tools",
            "policy_revision": config["revision"],
        },
        "quality": {"simulated": False},
        "languages": ["pl", "en", "de"],
        "reason": None if ready else "conversation_disabled",
    }


async def prepare(app, data, metadata, language):
    """Called at admission, before capability probes, queueing or model planning."""
    from .engines import EngineError

    if metadata.get("client_id") != "voice":
        raise EngineError("conversation_actor_invalid", 403)
    audio_input = metadata.get("task") == "audio.transcribe"
    if audio_input and not capability(app)["available"]:
        return None
    provider = create_provider(app)
    registry = getattr(app.state, "conversation_providers", None)
    if registry is None:
        raise EngineError("conversation_disabled", 503)
    config = provider.config()
    text = "" if audio_input else data.decode("utf-8")
    _, call = parse_request(text, addressed=True)
    urgent = bool(call and call.name == "stop")
    if not audio_input and not urgent:
        turn = metadata.get("conversation_turn")
        if not isinstance(turn, PreparedTurn):
            raise EngineError("conversation_input_scope_required", 409)
        if turn.revision != config["revision"]:
            raise EngineError("conversation_intent_expired", 409)
        if turn.transcript != text.strip():
            raise EngineError("conversation_transcript_mismatch", 409)
        return turn
    now = metadata.get("received_monotonic", time.monotonic())
    context = ConversationContext(
        task_id=metadata["job_id"],
        conversation_id="pi-" + metadata["session_id"],
        actor="voice",
        created_at=now,
        deadline=min(now + 30, metadata["deadline_monotonic"]),
        language=language,
        target_device_id=metadata["device_id"],
    )
    revision = config["revision"]
    try:
        await registry.prepare(text, context)
    except BaseException:
        await registry.finish(context)
        raise
    return PreparedTurn(context, revision, urgent)


async def finish(app, turn):
    registry = getattr(app.state, "conversation_providers", None)
    if registry is not None:
        await registry.finish(turn.context)


async def execute(app, data, metadata, language):
    from .engines import EngineError, EngineResult

    turn = metadata.get("conversation_turn")
    if not isinstance(turn, PreparedTurn):
        raise EngineError("conversation_context_required", 403)
    if metadata.get("client_id") != "voice":
        raise EngineError("conversation_actor_invalid", 403)
    provider = create_provider(app)
    if not turn.urgent and turn.revision != provider.config()["revision"]:
        raise EngineError("conversation_intent_expired", 409)
    registry = getattr(app.state, "conversation_providers", None)
    if registry is None:
        raise EngineError("conversation_disabled", 503)
    try:
        route = registry.urgent if turn.urgent else registry.handle
        reply = await route(data.decode("utf-8"), turn.context)
    except (RiderError, UnicodeDecodeError) as exc:
        raise EngineError("conversation_request_failed", 422) from exc
    finally:
        await registry.finish(turn.context)
    if reply is None:
        return None
    completed = provider.completed.get(turn.context.task_id) or {}
    return EngineResult(
        data={"text": reply, "speech_played": completed.get("speech_played") is True},
        runtime=capability(app)["runtime"],
        quality={"simulated": False},
    )
