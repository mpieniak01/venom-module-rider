"""Operator-enabled conversation tools share the selected Pi domain and transport."""

from __future__ import annotations

import asyncio
import math
import os
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass

from venom_core.core.module_data_policy import ensure_module_mutation_allowed
from venom_core.services.conversation_providers import (
    ConversationContext,
    current_conversation_context,
)

from .client import PiClient
from .conversation_tools import RiderSkill, ToolCall, parse_request
from .errors import RiderError

AGENT_STATE_PATH = "/api/control/agent/state"

MESSAGES = {
    "off": (
        "Polecenia Rider w rozmowach są wyłączone.",
        "Rider conversation tools are disabled.",
        "Rider-Gesprächswerkzeuge sind deaktiviert.",
    ),
    "ambiguous": (
        "Podaj jedną czynność: stan, opisz scenę, powiedz tekst, "
        "buźka gest, kierunek lub STOP.",
        "Request one action: status, observe, say text, "
        "face gesture, direction or STOP.",
        "Eine Aktion anfordern: Zustand, Szene, Text sprechen, "
        "Gesichtsgeste, Richtung oder STOP.",
    ),
    "expired": (
        "Polecenie rozmowy jest nieaktualne lub zostało przejęte przez panel.",
        "The conversation command expired or was superseded by manual control.",
        "Der Gesprächsbefehl ist abgelaufen oder wurde "
        "von der manuellen Steuerung übernommen.",
    ),
    "failed": (
        "Rider odmówił wykonania: ",
        "Rider refused the request: ",
        "Rider hat die Anfrage abgelehnt: ",
    ),
    "move": (
        "Żądanie impulsu wysłane. Fizyczny ruch niepotwierdzony.",
        "Pulse requested. Physical movement is unconfirmed.",
        "Impuls angefordert. Physische Bewegung ist nicht bestätigt.",
    ),
    "stop": (
        "STOP wysłany. Fizyczne zatrzymanie niepotwierdzone.",
        "STOP sent. Physical stopping is unconfirmed.",
        "STOP gesendet. Physischer Stillstand ist nicht bestätigt.",
    ),
    "face": (
        "Żądanie gestu wysłane na LCD.",
        "Face gesture requested on LCD.",
        "Gesichtsgeste auf LCD angefordert.",
    ),
    "speak": ("Odtwarzanie zakończone.", "Playback completed.", "Wiedergabe beendet."),
    "battery": ("Bateria", "Battery", "Batterie"),
}


def message(key, language):
    return MESSAGES[key][{"pl": 0, "en": 1, "de": 2}.get(language, 0)]


@dataclass
class Scope:
    context: ConversationContext
    call: ToolCall | None
    session: str
    revision: int
    safety: dict
    task: asyncio.Task | None
    operation: asyncio.Task | None = None
    used: bool = False
    playback_id: str | None = None
    playback_transport: PiClient | None = None


class RiderConversation:
    def __init__(self, app):
        self.app = app
        self.enabled = False
        self.revision = 0
        self.intent_epoch = 0
        self.session = None
        self.scopes: dict[str, Scope] = {}
        self.completed: OrderedDict[str, dict] = OrderedDict()
        self.retired: OrderedDict[str, ConversationContext] = OrderedDict()
        self.skill = RiderSkill(self)
        self.closed = False
        self._attached = None

    @property
    def domain(self):
        domain = getattr(self.app.state, "rider", None)
        if domain is None:
            raise RiderError("device_not_selected", 503)
        if domain is not self._attached:
            domain.conversation_invalidator = self.invalidate
            domain.conversation_drainer = self.drain
            self._attached = domain
        return domain

    def invalidate(self):
        """Drop intentions; do not disarm or send an automatic global STOP."""
        self.intent_epoch += 1
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        for scope in tuple(self.scopes.values()):
            if (
                scope.operation is not None
                and scope.operation is not current
                and scope.task is not current
            ):
                scope.operation.cancel()

    def config(self):
        domain = self.domain
        if self.session is not None and self.session != domain.session:
            self.enabled, self.session = False, None
            self.revision += 1
            self.invalidate()
        device = domain.device
        last = next(reversed(self.completed.values()), None) if self.completed else None
        return {
            "enabled": self.enabled and not self.closed,
            "revision": self.revision,
            "device_id": device.id if device else None,
            "credential_configured": bool(device and os.getenv(device.agent_token_env)),
            "credential_transport_secure": bool(
                device and getattr(domain.client, "credential_transport_secure", False)
            ),
            "credential_transport_allowed": bool(
                device and getattr(domain.client, "credential_transport_allowed", False)
            ),
            "transport_profile": device.transport_profile if device else "secure",
            "transport_identity_verified": bool(
                domain.client
                and getattr(
                    domain.client,
                    "transport_status",
                    lambda: {"identity_verified": False},
                )().get("identity_verified", False)
            ),
            "tools": ["status", "observe", "speak", "face", "move", "stop"]
            if self.enabled
            else [],
            "last_result": {**last, "stale": last["revision"] != self.intent_epoch}
            if last
            else None,
        }

    def configure(self, enabled, revision, session):
        self.domain.check_session(session)
        current = self.config()
        if type(enabled) is not bool or type(revision) is not int:
            raise RiderError("conversation_configuration_invalid", 400)
        if enabled and revision != current["revision"]:
            raise RiderError("conversation_configuration_conflict", 409)
        if enabled and (self.closed or self.domain.device is None):
            raise RiderError("device_not_selected", 503)
        self.enabled, self.session = enabled, self.domain.session
        self.revision += 1
        self.invalidate()
        return self.config()

    def list_tools(self):
        if not self.config()["enabled"]:
            return []
        arguments: dict[str, dict] = {
            "move": {
                "direction": {
                    "type": "string",
                    "enum": ["forward", "backward", "left", "right"],
                }
            },
            "speak": {"text": {"type": "string", "minLength": 1, "maxLength": 1024}},
            "face": {
                "gesture": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "pattern": "^[A-Za-z0-9_-]+$",
                }
            },
        }
        return [
            {
                "name": "rider_" + name,
                "plugin": "RiderSkill",
                "parameters": {
                    "type": "object",
                    "properties": arguments.get(name, {}),
                    "required": list(arguments.get(name, {})),
                    "additionalProperties": False,
                },
            }
            for name in self.config()["tools"]
        ]

    def _check(self, scope, *, stop=False):
        self.domain.check_session(scope.session)
        if current_conversation_context() is not scope.context:
            raise RiderError("conversation_context_required", 403)
        if scope.context.actor not in {"chat", "voice"}:
            raise RiderError("conversation_actor_invalid", 403)
        target = scope.context.target_device_id
        device = self.domain.device
        if target is not None and (device is None or target != device.id):
            raise RiderError("conversation_target_changed", 409)
        if stop:
            return
        if (
            not self.config()["enabled"]
            or scope.revision != self.intent_epoch
            or time.monotonic() >= scope.context.deadline
        ):
            raise RiderError("conversation_intent_expired", 409)

    async def prepare(self, text, context):
        addressed, call = parse_request(
            text, addressed=context.target_device_id is not None
        )
        audio_input = not text and context.actor == "voice"
        if not self.config()["enabled"] or not (
            audio_input or addressed and call is not None and call.name == "move"
        ):
            return
        if context.task_id in self.retired:
            raise RiderError("conversation_intent_expired", 409)
        if context.task_id in self.scopes:
            if self.scopes[context.task_id].context is not context:
                raise RiderError("conversation_context_conflict", 409)
            return
        if len(self.scopes) >= 16:
            raise RiderError("conversation_busy", 429)
        scope = Scope(context, call, self.domain.session, self.intent_epoch, {}, None)
        self.scopes[context.task_id] = scope
        try:
            self._check(scope)
            control = await self.domain.read(AGENT_STATE_PATH, 0, force=True)
            self._check(scope)
            scope.safety = self.domain.data(control).get("safety") or {}
        except RiderError as exc:
            if audio_input:
                # Unavailable Pi must not break ordinary voice chat or acquire
                # fresh permission after ASR. The bound refusal is immutable.
                scope.safety = {"unavailable_error": exc.code}
                return
            self._drop(scope)
            raise
        except BaseException:
            self._drop(scope)
            raise

    def _drop(self, scope):
        if self.scopes.get(scope.context.task_id) is scope:
            self.scopes.pop(scope.context.task_id)
            self.retired[scope.context.task_id] = scope.context
            while len(self.retired) > 64:
                self.retired.popitem(last=False)

    async def finish(self, context):
        scope = self.scopes.get(context.task_id)
        if scope is None or scope.context is not context:
            return
        if scope.task is not None and scope.task is not asyncio.current_task():
            scope.task.cancel()
            await asyncio.gather(scope.task, return_exceptions=True)
        self._drop(scope)

    def accepts(self, text, context) -> bool:
        """Pure routing check; unrelated chat does not consume a robot deadline."""
        addressed, _ = parse_request(
            text, addressed=context.target_device_id is not None
        )
        return addressed

    async def urgent(self, text, context):
        addressed, call = parse_request(
            text, addressed=context.target_device_id is not None
        )
        if addressed and call is not None and call.name == "stop":
            return await self.handle(text, context)
        return None

    def _request_refusal(self, call, context, language):
        """Refuse retired, ambiguous or disabled requests before preparing a scope."""
        if context.task_id in self.retired:
            return message("expired", language)
        if call is None:
            return message("ambiguous", language)
        if call.name != "stop" and not self.config()["enabled"]:
            return message("off", language)
        return None

    async def _request_scope(self, text, call, context, language):
        """Reuse the captured scope without renewing its safety permission."""
        scope = self.scopes.get(context.task_id)
        if scope is not None and scope.context is not context:
            return message("failed", language) + "conversation_context_conflict"
        if scope is None:
            try:
                await self.prepare(text, context)
            except RiderError as exc:
                return message("failed", language) + exc.code
            scope = self.scopes.get(context.task_id)
        if scope is None:
            scope = Scope(
                context, call, self.domain.session, self.intent_epoch, {}, None
            )
        if len(self.scopes) >= 16 and context.task_id not in self.scopes:
            return message("failed", language) + "conversation_busy"
        scope.task = asyncio.current_task()
        if scope.call is None:
            scope.call = call
        self.scopes[context.task_id] = scope
        return scope

    async def handle(self, text, context):
        """Bind an addressed request and preserve the caller cancellation boundary."""
        addressed, call = parse_request(
            text, addressed=context.target_device_id is not None
        )
        if not addressed:
            return None
        language = context.language
        refusal = self._request_refusal(call, context, language)
        if refusal is not None:
            return refusal
        scope = await self._request_scope(text, call, context, language)
        if isinstance(scope, str):
            return scope
        return await self._run_scope(scope, call, language)

    async def _run_scope(self, scope, call, language):
        """Translate child invalidation while propagating caller cancellation."""
        try:
            scope.operation = asyncio.create_task(self._execute(scope, call))
            return await scope.operation
        except RiderError as exc:
            return message("failed", language) + exc.code
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            if scope.revision != self.intent_epoch:
                return message("expired", language)
            raise
        finally:
            try:
                await self._cancel_playback(scope)
            finally:
                self._drop(scope)

    async def _cancel_playback(self, scope):
        """Cancel unfinished playback; caller cancellation must still propagate."""
        if not scope.playback_id or not scope.playback_transport:
            return
        try:
            await asyncio.wait_for(
                scope.playback_transport.json(
                    "/api/voice/playback/cancel",
                    method="POST",
                    body={"playback_id": scope.playback_id},
                    stop=True,
                ),
                timeout=1.5,
            )
        except (TimeoutError, RiderError):
            pass

    async def _execute(self, scope, call):
        """Run the scoped tool separately from the caller's cancellation."""
        self._check(scope, stop=call.name == "stop")
        if call.name == "move" and not scope.safety:
            control = await self.domain.read(AGENT_STATE_PATH, 0, force=True)
            self._check(scope)
            scope.safety = self.domain.data(control).get("safety") or {}
        arguments = {
            "move": (call.direction,),
            "speak": (call.text,),
            "face": (call.gesture,),
        }.get(call.name, ())
        return await getattr(self.skill, call.name)(*arguments)

    async def invoke(self, call: ToolCall, context):
        """Execute a single-use scoped tool and retain the bounded result history."""
        scope = self.scopes.get(context.task_id)
        if (
            scope is None
            or scope.context is not context
            or call != scope.call
            or scope.used
        ):
            raise RiderError("conversation_intent_required", 403)
        self._check(scope, stop=call.name == "stop")
        scope.used = True
        domain = self.domain
        self._require_mutation(call)
        result, reply, speech_played = await self._invoke_call(
            domain, scope, call, context
        )
        if call.name != "stop":
            self._check(scope)
        self.completed[context.task_id] = {
            "tool": call.name,
            "actor": context.actor,
            "reply": reply,
            "result": result,
            "speech_played": speech_played,
            "observed_at": time.time(),
            "revision": self.intent_epoch,
        }
        while len(self.completed) > 16:
            self.completed.popitem(last=False)
        return reply

    @staticmethod
    def _require_mutation(call):
        if call.name in {"status", "stop"}:
            return
        try:
            ensure_module_mutation_allowed(
                module_id="rider", operation_name="conversation:" + call.name
            )
        except PermissionError as exc:
            raise RiderError("environment_read_only", 403) from exc

    async def _invoke_call(self, domain, scope, call, context):
        """Dispatch the tool while retaining the established speech fallback."""
        speech_played = False
        if call.name == "status":
            result, reply = await self._invoke_status(domain, context)
        elif call.name == "stop":
            result, reply = await self._invoke_stop(domain, scope, context)
        elif call.name == "move":
            result, reply = await self._invoke_move(domain, scope, call, context)
        elif call.name == "face":
            result, reply = await self._invoke_face(domain, scope, call, context)
        elif call.name == "observe":
            result, reply = await self._invoke_observe(domain, scope, context)
        else:
            result, reply = await self._invoke_speak(domain, scope, call, context)
            speech_played = True
        return result, reply, speech_played

    async def _invoke_status(self, domain, context):
        result = await domain.status("overview")
        safety = (result.get("control") or {}).get("safety") or {}
        reading = self._battery_reading(result)
        reply = (
            f"{result['connection']['label']}: {result['connection']['status']} · "
            f"{safety.get('state', 'unknown')} · "
            f"{message('battery', context.language)}: {reading}"
        )
        return result, reply

    @staticmethod
    def _battery_reading(result):
        telemetry = result.get("telemetry") or {}
        xgo = (telemetry.get("devices") or {}).get("xgo") or {}
        battery = ((xgo.get("sensor_quality") or {}).get("sensors") or {}).get(
            "battery"
        ) or {}
        value = battery.get("value")
        if not (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and battery.get("stale") is not True
            and battery.get("ready") is True
        ):
            return "—"
        if battery.get("unit") == "ratio":
            percent = value * 100
        elif battery.get("unit") in {"%", "percent"}:
            percent = value
        else:
            return "—"
        if 0 <= percent <= 100:
            return f"{percent:.0f}%"
        return "—"

    async def _invoke_stop(self, domain, scope, context):
        result = await domain.control(
            {
                "action": "stop",
                "source": "agent",
                "execution_context": "agent",
                "channel": context.actor,
                "request_id": uuid.uuid4().hex,
            },
            scope.session,
        )
        reply = message("stop", context.language)
        return result, reply

    @staticmethod
    def _motion_safety(scope):
        safety = scope.safety
        if safety.get("unavailable_error"):
            raise RiderError(safety["unavailable_error"], 503)
        if (
            safety.get("can_move") is not True
            or safety.get("panel_motion_enabled") is not True
        ):
            raise RiderError("motion_not_armed")
        if (
            safety.get("owner") != "manual"
            or safety.get("execution_context") != "panel"
            or safety.get("arming_mode") != "operator"
        ):
            raise RiderError("operator_motion_permission_required", 403)
        if (
            not safety.get("agent_epoch")
            or type(safety.get("agent_last_seq")) is not int
        ):
            raise RiderError("unsupported_agent_contract", 502)
        return safety

    async def _invoke_move(self, domain, scope, call, context):
        safety = self._motion_safety(scope)
        control = await domain.read(AGENT_STATE_PATH, 0, force=True)
        self._check(scope)
        current = domain.data(control).get("safety") or {}
        if (current.get("generation"), current.get("agent_epoch")) != (
            safety.get("generation"),
            safety.get("agent_epoch"),
        ):
            raise RiderError("conversation_intent_expired", 409)
        body = {
            "generation": safety["generation"],
            "agent_epoch": safety["agent_epoch"],
            "seq": current["agent_last_seq"] + 1,
            "ts": control["meta"]["server_time"],
            "ttl_ms": 500,
            "settings_revision": (safety.get("manual_settings") or {}).get("revision"),
            "request_id": uuid.uuid4().hex,
            "intent_id": context.task_id,
            "channel": context.actor,
            "direction": call.direction,
        }
        result = await domain.transport().json(
            "/api/control/agent/pulse", method="POST", body=body
        )
        reply = message("move", context.language)
        return result, reply

    @staticmethod
    def _find_gesture(library, gesture_id):
        gestures = library.get("gestures") or {}
        if isinstance(gestures, list):
            for row in gestures:
                if (
                    isinstance(row, dict)
                    and (row.get("id") or row.get("key")) == gesture_id
                ):
                    return row
            return None
        if isinstance(gestures, dict):
            return gestures.get(gesture_id)
        return None

    async def _invoke_face(self, domain, scope, call, context):
        library = domain.data(await domain.read("/api/face-studio/gestures", 5))
        gesture = self._find_gesture(library, call.gesture)
        if not isinstance(gesture, dict):
            raise RiderError("unknown_gesture", 404)
        self._check(scope)
        result = await domain.mutate(
            "/api/face-studio/runtime/play",
            {
                "gesture_id": call.gesture,
                "expr": gesture.get("expr", "neutral"),
                "sink": "lcd",
            },
            scope.session,
        )
        reply = message("face", context.language)
        return result, reply

    async def _invoke_observe(self, domain, scope, context):
        payload, mime, headers = await domain.camera("raw")
        self._check(scope)
        if (
            headers.get("x-rider-placeholder") == "true"
            or headers.get("x-rider-stale") == "true"
        ):
            raise RiderError("camera_frame_stale", 409)
        engine = self.app.state.rider_compute.engines
        observation = await engine.execute(
            "vision.describe",
            {"language": context.language},
            payload,
            {"content_type": mime},
        )
        self._check(scope)
        result = {
            "data": observation.data,
            "runtime": observation.runtime,
            "quality": observation.quality,
            "source": headers,
        }
        reply = str(observation.data.get("text", ""))
        return result, reply

    async def _invoke_speak(self, domain, scope, call, context):
        engine = self.app.state.rider_compute.engines
        observation = await engine.execute(
            "audio.synthesize",
            {"language": context.language},
            call.text.encode(),
            {},
        )
        self._check(scope)
        if not observation.binary:
            raise RiderError("compute_output_unavailable", 503)
        scope.playback_id = uuid.uuid4().hex
        scope.playback_transport = domain.transport()
        ttl = min(60000, int((context.deadline - time.monotonic()) * 1000))
        if ttl <= 0:
            raise RiderError("conversation_intent_expired", 409)
        result = await scope.playback_transport.json(
            "/api/voice/playback",
            method="POST",
            body=observation.binary,
            headers={
                "Content-Type": "audio/wav",
                "X-Rider-Playback-Id": scope.playback_id,
                "X-Rider-Playback-TTL-Ms": str(ttl),
            },
        )
        self._check(scope)
        if (result.get("data") or {}).get("state") != "played":
            raise RiderError("playback_not_completed", 502)
        scope.playback_id = None
        reply = message("speak", context.language)
        return result, reply

    async def close(self):
        self.enabled, self.closed = False, True
        self.invalidate()
        await self.drain()

    async def drain(self):
        tasks = [
            scope.task
            for scope in self.scopes.values()
            if scope.task and scope.task is not asyncio.current_task()
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def create_provider(app):
    provider = getattr(app.state, "rider_conversation", None)
    if provider is None or provider.closed:
        provider = RiderConversation(app)
        app.state.rider_conversation = provider
    return provider
