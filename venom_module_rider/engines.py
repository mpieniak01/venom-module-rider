"""Inference adapters for Venom services; no capture, playback or robot commands.

Capability reads never warm up models. A cold configured model can accept a job
while ``ready`` stays false until it has actually loaded. Audio work is drained
after cancellation before this adapter permits another use of the same engine.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import importlib.util
import io
import json
import time
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

TASKS = (
    "text.respond",
    "audio.transcribe",
    "audio.synthesize",
    "vision.describe",
    "conversation.respond",
)
_LOCAL_PROVIDERS = {"ollama", "vllm", "local", "lmstudio", "multi_runtime"}
_MAX_TEXT = 4096
_MAX_OUTPUT_TEXT = 16_384
_MAX_MEDIA = 2 * 1024 * 1024
_MAX_OUTPUT_AUDIO = 4 * 1024 * 1024
_PROBE_TTL = 10.0


class EngineError(RuntimeError):
    """Stable public error code; provider diagnostics never expose credentials."""

    def __init__(self, code: str, status_code: int = 502):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class EngineUnavailable(EngineError):
    def __init__(self, code: str = "engine_unavailable"):
        super().__init__(code, 503)


@dataclass(slots=True)
class EngineResult:
    data: dict[str, Any]
    binary: bytes | None = None
    content_type: str | None = None
    runtime: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:24]


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _path_identity(path: Any) -> tuple[str, int, int]:
    """Fingerprint the selected artifact without exposing its private path."""
    name = str(path or "")
    try:
        stat = Path(name).stat() if name else None
        return name, stat.st_size if stat else 0, stat.st_mtime_ns if stat else 0
    except OSError:
        return name, 0, 0


def _model_matches(left: Any, right: Any) -> bool:
    a, b = str(left or ""), str(right or "")
    return a == b or a.removesuffix(":latest") == b.removesuffix(":latest")


def _voice_languages(voice: Any) -> list[str]:
    path = str(getattr(voice, "model_path", "") or "")
    try:
        config_path = Path(path + ".json")
        if not path or config_path.stat().st_size > 64 * 1024:
            return []
        config = json.loads(config_path.read_text())
        code = config.get("language", {}).get("code", "")
        language = str(code).replace("-", "_").split("_", 1)[0]
        return [language] if language in {"pl", "en", "de"} else []
    except (OSError, ValueError, AttributeError):
        return []


def _text_input(data: bytes | None) -> str:
    if not data or len(data) > _MAX_TEXT:
        raise EngineError("engine_invalid_text", 422)
    try:
        text = data.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise EngineError("engine_invalid_text", 422) from exc
    if not text:
        raise EngineError("engine_invalid_text", 422)
    return text


def _checked_text(value: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > _MAX_OUTPUT_TEXT:
        raise EngineError("engine_invalid_output")
    text = value.strip()
    if not allow_empty and not text:
        raise EngineError("engine_empty_output")
    return text


def _language(params: dict[str, Any]) -> str:
    language = params.get("language", "pl")
    if not isinstance(language, str) or language not in {"pl", "en", "de"}:
        raise EngineError("engine_invalid_language", 422)
    return str(language)


class VenomEngines:
    """Use the host's active LLM runtime and shared AudioEngine singleton."""

    def __init__(self, app: Any = None):
        self.app = app
        self._audio_lock = asyncio.Lock()
        self._probe_lock = asyncio.Lock()
        self._snapshot_key = ""
        self._snapshot_at = 0.0
        self._snapshot: dict[str, Any] = {}
        self._draining: set[asyncio.Task[Any]] = set()

    def _runtime(self) -> Any:
        from venom_core.utils.llm_runtime import get_active_llm_runtime

        return get_active_llm_runtime()

    def _audio(self) -> Any:
        return getattr(getattr(self.app, "state", None), "audio_engine", None)

    async def _selected_runtime(self) -> Any:
        """Use the same active adapter resolution as Venom's simple service."""
        from venom_core.services.llm_simple_service import (
            _get_active_adapter_id,
            _resolve_model_name_for_simple_request,
        )

        runtime = copy.copy(self._runtime())
        runtime.model_name = _resolve_model_name_for_simple_request(
            request_model=None,
            runtime_model=runtime.model_name,
            active_adapter_id=await _get_active_adapter_id(),
        )
        return runtime

    def _runtime_identity(self, runtime: Any) -> dict[str, Any]:
        return {
            "provider": str(getattr(runtime, "provider", "") or ""),
            "model": str(getattr(runtime, "model_name", "") or ""),
            "selection_revision": str(getattr(runtime, "config_hash", "") or ""),
        }

    async def revision(self, runtime: Any = None) -> str:
        """Configuration identity only; no network request or model loading."""
        audio = self._audio()
        whisper = getattr(audio, "whisper", None)
        voice = getattr(audio, "voice", None)
        try:
            runtime = runtime or await self._selected_runtime()
            llm = self._runtime_identity(runtime)
            # The endpoint participates in the hash but is never returned.
            llm["endpoint_identity"] = _fingerprint(getattr(runtime, "endpoint", ""))
        except (ImportError, RuntimeError, ValueError):
            llm = {"provider": "unavailable"}
        return _fingerprint(
            {
                "llm": llm,
                "stt": (
                    getattr(whisper, "model_size", ""),
                    getattr(whisper, "device", ""),
                    getattr(whisper, "compute_type", ""),
                ),
                "tts": (
                    _path_identity(getattr(voice, "model_path", "")),
                    _path_identity(str(getattr(voice, "model_path", "")) + ".json"),
                    getattr(voice, "speaker_id", 0),
                ),
            }
        )

    async def _probe(self, runtime: Any) -> dict[str, Any]:
        """Read serving metadata, never invoke the active model for a probe."""
        from venom_core.infrastructure.traffic_control import (
            TrafficControlledHttpClient,
        )

        provider = str(runtime.provider)
        endpoint = str(runtime.endpoint or "")
        parts = urlsplit(endpoint)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return {"reachable": False, "installed": False, "ready": False}
        if parts.username or parts.password or parts.query or parts.fragment:
            raise EngineUnavailable("engine_endpoint_invalid")
        base = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        async with TrafficControlledHttpClient(
            provider=provider, timeout=1.0
        ) as client:
            if provider == "ollama":
                tags = await client.aget(f"{base}/api/tags", disable_retry=True)
                tags.raise_for_status()
                entries = tags.json().get("models", [])
                installed = any(
                    _model_matches(entry.get("name"), runtime.model_name)
                    for entry in entries
                    if isinstance(entry, dict)
                )
                if not installed:
                    return {"reachable": True, "installed": False, "ready": False}
                show = await client.apost(
                    f"{base}/api/show",
                    json={"model": runtime.model_name},
                    disable_retry=True,
                )
                show.raise_for_status()
                from venom_core.core.ollama_runtime_capabilities import (
                    normalize_ollama_show_payload,
                )

                caps = normalize_ollama_show_payload(
                    model_name=runtime.model_name,
                    endpoint=base,
                    payload=show.json(),
                ).capabilities
                loaded = await client.aget(f"{base}/api/ps", disable_retry=True)
                loaded.raise_for_status()
                ready = any(
                    _model_matches(entry.get("name"), runtime.model_name)
                    for entry in loaded.json().get("models", [])
                    if isinstance(entry, dict)
                )
                return {
                    "reachable": True,
                    "installed": True,
                    "ready": ready,
                    "vision": caps.get("vision_input") is True,
                    "vision_source": "runtime_metadata",
                }
            models_url = endpoint.rstrip("/")
            if not models_url.endswith("/v1"):
                models_url += "/v1"
            response = await client.aget(f"{models_url}/models", disable_retry=True)
            response.raise_for_status()
            entry = next(
                (
                    item
                    for item in response.json().get("data", [])
                    if isinstance(item, dict)
                    and _model_matches(item.get("id"), runtime.model_name)
                ),
                {},
            )
            return {
                "reachable": True,
                "installed": bool(entry),
                "ready": bool(entry) and entry.get("status", "loaded") == "loaded",
                "vision": entry.get("supports_image_input") is True,
                "vision_source": "runtime_metadata",
            }

    async def _runtime_snapshot(self, runtime: Any) -> dict[str, Any]:
        key = _fingerprint((self._runtime_identity(runtime), runtime.endpoint))
        if (
            self._snapshot_key == key
            and time.monotonic() - self._snapshot_at < _PROBE_TTL
        ):
            return self._snapshot
        async with self._probe_lock:
            if (
                self._snapshot_key == key
                and time.monotonic() - self._snapshot_at < _PROBE_TTL
            ):
                return self._snapshot
            try:
                snapshot = await self._probe(runtime)
            except asyncio.CancelledError:
                raise
            except Exception:
                snapshot = {"reachable": False, "installed": False, "ready": False}
            self._snapshot_key = key
            self._snapshot_at = time.monotonic()
            self._snapshot = snapshot
            return snapshot

    async def capabilities(self) -> dict[str, Any]:
        tasks: dict[str, Any] = {}
        try:
            runtime = await self._selected_runtime()
            revision = await self.revision(runtime)
            llm_runtime = self._runtime_identity(runtime)
            local = runtime.provider in _LOCAL_PROVIDERS
            snapshot = await self._runtime_snapshot(runtime) if local else {}
            configured = bool(local and runtime.model_name and runtime.endpoint)
        except (ImportError, RuntimeError, ValueError):
            revision = await self.revision()
            llm_runtime, snapshot, configured = {"provider": "unavailable"}, {}, False
        llm_runtime["config_revision"] = revision
        for task in ("text.respond", "vision.describe"):
            supported = task == "text.respond" or snapshot.get("vision") is True
            available = bool(
                configured
                and supported
                and snapshot.get("reachable")
                and snapshot.get("installed")
            )
            tasks[task] = {
                "available": available,
                "installed": bool(snapshot.get("installed")),
                "configured": configured,
                "ready": bool(available and snapshot.get("ready")),
                "busy": False,
                "runtime": dict(llm_runtime),
                "quality": {"simulated": False, "purpose": "scene_description"}
                if task == "vision.describe"
                else {"simulated": False},
                "reason": None if available else "engine_unavailable",
            }
        audio = self._audio()
        whisper, voice = getattr(audio, "whisper", None), getattr(audio, "voice", None)
        stt_installed = _installed("faster_whisper")
        stt_configured = bool(whisper and getattr(whisper, "model_size", None))
        stt_checked = callable(getattr(audio, "transcribe_checked", None))
        tasks["audio.transcribe"] = {
            "available": bool(stt_installed and stt_configured and stt_checked),
            "installed": stt_installed,
            "configured": stt_configured,
            "ready": bool(stt_checked and getattr(whisper, "model", None) is not None),
            "busy": self._audio_lock.locked(),
            "runtime": {
                "provider": "faster_whisper",
                "model": str(getattr(whisper, "model_size", "") or ""),
                "config_revision": revision,
            },
            "quality": {"simulated": False},
            "languages": ["pl", "en", "de"],
            "reason": None if stt_checked else "engine_checked_stt_unavailable",
        }
        tts_installed = _installed("piper")
        path = _path_identity(getattr(voice, "model_path", ""))
        tts_configured = bool(voice and path[1] > 0)
        tts_real = bool(voice and getattr(voice, "is_fallback_mode", True) is False)
        tts_languages = _voice_languages(voice)
        tasks["audio.synthesize"] = {
            "available": bool(
                tts_installed and tts_configured and tts_real and tts_languages
            ),
            "installed": tts_installed,
            "configured": tts_configured,
            "ready": bool(tts_real and getattr(voice, "voice", None) is not None),
            "busy": self._audio_lock.locked(),
            "runtime": {
                "provider": "piper",
                "model": Path(path[0]).name,
                "config_revision": revision,
            },
            "quality": {"simulated": False},
            "languages": tts_languages,
            "reason": None if tts_real else "engine_tts_fallback_refused",
        }
        from .conversation_engine import capability

        tasks["conversation.respond"] = capability(self.app)
        tasks["conversation.respond"]["runtime"]["config_revision"] = revision
        return {"tasks": tasks, "config_revision": revision}

    async def _drained_audio(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        await self._audio_lock.acquire()

        async def run() -> Any:
            return await operation()

        work: asyncio.Task[Any] = asyncio.create_task(run())
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            self._draining.add(work)

            def drained(done: asyncio.Task[Any]) -> None:
                self._draining.discard(done)
                self._audio_lock.release()
                if not done.cancelled():
                    done.exception()

            work.add_done_callback(drained)
            raise
        finally:
            if work not in self._draining:
                self._audio_lock.release()

    async def _text(
        self,
        text: str,
        language: str,
        metadata: dict[str, Any],
        model_name: str,
    ) -> str:
        from venom_core.api.schemas.llm_simple import SimpleChatRequest
        from venom_core.services.llm_simple_service import stream_simple_chat

        language_name = {"pl": "Polish", "en": "English", "de": "German"}[language]
        response = await stream_simple_chat(
            SimpleChatRequest(
                content=f"Respond only in {language_name}.\n{text}",
                model=model_name,
                max_tokens=512,
                temperature=0.2,
                stream=False,
                session_id=str(metadata.get("session_id", "")),
                tools=None,
                tool_choice=None,
            )
        )
        output = ""
        pending = ""
        iterator = response.body_iterator
        try:
            async for chunk in iterator:
                pending += chunk.decode() if isinstance(chunk, bytes) else chunk
                if len(pending) > 64 * 1024:
                    raise EngineError("engine_invalid_output")
                while "\n\n" in pending:
                    event, pending = pending.split("\n\n", 1)
                    kind = ""
                    payload: dict[str, Any] = {}
                    for line in event.splitlines():
                        if line.startswith("event:"):
                            kind = line[6:].strip()
                        elif line.startswith("data:"):
                            value = json.loads(line[5:].strip())
                            if isinstance(value, dict):
                                payload = value
                    if kind == "error":
                        raise EngineError("engine_text_failed")
                    if kind == "content":
                        output += str(payload.get("text", ""))
                        if len(output) > _MAX_OUTPUT_TEXT:
                            raise EngineError("engine_invalid_output")
        finally:
            close = getattr(iterator, "aclose", None)
            if close:
                await close()
        return _checked_text(output)

    async def _vision(self, data: bytes, language: str, prompt: str) -> str:
        from PIL import Image

        if len(data) > _MAX_MEDIA:
            raise EngineError("engine_invalid_image", 422)
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"JPEG", "PNG"} or not (
                    0 < image.width <= 1920 and 0 < image.height <= 1080
                ):
                    raise ValueError("image_bounds")
                mime = "image/png" if image.format == "PNG" else "image/jpeg"
                image.verify()
        except Exception as exc:
            raise EngineError("engine_invalid_image", 422) from exc
        from venom_core.services.llm_simple_transport import open_stream_response
        from venom_core.services.runtime_switch_gate import runtime_request_guard
        from venom_core.utils.llm_runtime import _build_chat_completions_url

        runtime = await self._selected_runtime()
        url = _build_chat_completions_url(runtime)
        if not url:
            raise EngineUnavailable()
        language_name = {"pl": "Polish", "en": "English", "de": "German"}[language]
        instruction = (
            "Describe only objects and colors visible in this image. "
            f"Reply only in {language_name}. Do not infer navigation clearance. "
            f"If something is unclear, say so. {prompt}"
        )
        image_url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
        payload = {
            "model": runtime.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": instruction,
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url},
                        },
                    ],
                }
            ],
            "max_tokens": 256,
            "stream": False,
        }
        # A bounded scene description needs final text, not a token budget
        # consumed entirely by the model's optional reasoning trace.
        if runtime.provider == "ollama":
            payload["reasoning_effort"] = "none"
        async with runtime_request_guard(
            request_kind="rider_scene_description",
            provider=runtime.provider,
            model=runtime.model_name,
        ):
            async with open_stream_response(
                provider_name=runtime.provider, completions_url=url, payload=payload
            ) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 128 * 1024:
                        raise EngineError("engine_invalid_output")
        result = json.loads(body)
        return _checked_text(result["choices"][0]["message"]["content"])

    async def _transcribe(self, data: bytes, language: str) -> dict[str, Any]:
        import numpy as np

        try:
            if len(data) > _MAX_MEDIA:
                raise ValueError("audio_bounds")
            with wave.open(io.BytesIO(data), "rb") as audio:
                rate, samples = audio.getframerate(), audio.getnframes()
                if (
                    audio.getnchannels() != 1
                    or audio.getsampwidth() != 2
                    or not 8000 <= rate <= 48000
                    or not 0 < samples <= 15 * rate
                ):
                    raise ValueError("audio_format")
                frames = audio.readframes(samples)
                if len(frames) != samples * 2:
                    raise ValueError("audio_truncated")
            pcm = np.frombuffer(frames, dtype="<i2")
        except Exception as exc:
            raise EngineError("engine_invalid_audio", 422) from exc
        audio_engine = self._audio()
        checked = getattr(audio_engine, "transcribe_checked", None)
        if not callable(checked):
            raise EngineUnavailable("engine_checked_stt_unavailable")
        text = _checked_text(
            await self._drained_audio(
                lambda: checked(pcm, language=language, sample_rate=rate)
            ),
            allow_empty=True,
        )
        return {"text": text, "silence": not text, "sample_rate": rate}

    async def _synthesize(self, text: str) -> EngineResult:
        import numpy as np

        voice = getattr(self._audio(), "voice", None)
        if not voice or getattr(voice, "is_fallback_mode", True):
            raise EngineUnavailable("engine_tts_fallback_refused")
        pcm = await self._drained_audio(lambda: voice.speak(text))
        if pcm is None or getattr(voice, "is_fallback_mode", True):
            raise EngineError("engine_tts_failed")
        audio = np.asarray(pcm)
        rate = getattr(voice, "output_sample_rate", None)
        if (
            audio.ndim != 1
            or audio.dtype != np.int16
            or not audio.size
            or audio.nbytes > _MAX_OUTPUT_AUDIO - 44
            or not isinstance(rate, int)
            or not 8000 <= rate <= 48000
            or not np.any(audio)
        ):
            raise EngineError("engine_invalid_audio_output")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(rate)
            output.writeframes(audio.astype("<i2", copy=False).tobytes())
        return EngineResult(
            data={"sample_rate": rate, "channels": 1, "sample_width": 2},
            binary=buffer.getvalue(),
            content_type="audio/wav",
        )

    async def execute(
        self,
        task: str,
        params: dict[str, Any],
        data: bytes | None,
        metadata: dict[str, Any],
    ) -> EngineResult:
        if task not in TASKS:
            raise EngineError("engine_unknown_task", 422)
        language = _language(params)
        caps = await self.capabilities()
        capability = caps["tasks"][task]
        if not capability["available"]:
            raise EngineUnavailable(capability["reason"] or "engine_unavailable")
        if language not in capability.get("languages", ["pl", "en", "de"]):
            raise EngineUnavailable("engine_language_unavailable")
        revision = caps["config_revision"]
        try:
            if task == "conversation.respond":
                from .conversation_engine import execute

                result = await execute(self.app, data or b"", metadata, language)
                if result is None:
                    text_cap = caps["tasks"]["text.respond"]
                    if not text_cap["available"]:
                        raise EngineUnavailable()
                    result = EngineResult(
                        data={
                            "text": await self._text(
                                _text_input(data),
                                language,
                                metadata,
                                text_cap["runtime"]["model"],
                            )
                        }
                    )
                    capability = text_cap
            elif task == "text.respond":
                result = EngineResult(
                    data={
                        "text": await self._text(
                            _text_input(data),
                            language,
                            metadata,
                            capability["runtime"]["model"],
                        )
                    }
                )
            elif task == "audio.synthesize":
                result = await self._synthesize(_text_input(data))
            elif task == "audio.transcribe":
                result = EngineResult(
                    data=await self._transcribe(data or b"", language)
                )
            else:
                prompt = params.get("prompt", "")
                if not isinstance(prompt, str) or len(prompt) > 512:
                    raise EngineError("engine_invalid_prompt", 422)
                result = EngineResult(
                    data={"text": await self._vision(data or b"", language, prompt)}
                )
        except (EngineError, asyncio.CancelledError):
            raise
        except Exception as exc:
            raise EngineError("engine_inference_failed") from exc
        if await self.revision() != revision:
            raise EngineError("engine_configuration_changed", 409)
        result.runtime = dict(capability["runtime"])
        result.quality = dict(capability["quality"])
        if task == "audio.transcribe":
            result.quality["silence"] = result.data["silence"]
        if task == "vision.describe":
            result.quality["navigation_clearance"] = False
        return result

    async def drain(self) -> None:
        """Wait for cancelled callers' model work before starting another job."""
        if self._draining:
            await asyncio.shield(
                asyncio.gather(*self._draining, return_exceptions=True)
            )

    async def prepare_conversation(self, data, metadata, language):
        from .conversation_engine import prepare

        return await prepare(self.app, data, metadata, language)

    async def finish_conversation(self, turn):
        from .conversation_engine import finish

        await finish(self.app, turn)

    async def urgent_conversation(self, data, metadata, language):
        from .conversation_engine import execute

        return await execute(self.app, data, metadata, language)

    async def close(self) -> None:
        """Drain owned work; never close or unload the host's shared engines."""
        await self.drain()
