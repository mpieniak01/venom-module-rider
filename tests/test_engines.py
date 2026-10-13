import asyncio
import copy
import io
import json
import sys
import wave
from contextlib import asynccontextmanager
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from venom_module_rider import engines
from venom_module_rider.engines import (
    EngineError,
    EngineResult,
    EngineUnavailable,
    VenomEngines,
)


def wav_bytes(*, channels=1, width=2, rate=16000, samples=160):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(rate)
        audio.writeframes(b"\x01\x00" * samples * channels)
    return buffer.getvalue()


def image_bytes(*, size=(16, 12), format="PNG"):
    output = io.BytesIO()
    Image.new("RGB", size, "blue").save(output, format=format)
    return output.getvalue()


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    model = tmp_path / "pl_PL-test.onnx"
    model.write_bytes(b"model")
    model.with_name(model.name + ".json").write_text(
        json.dumps({"language": {"code": "pl_PL"}})
    )

    async def transcribe(pcm, language, sample_rate):
        assert pcm.dtype == np.int16 and sample_rate == 16000 and language == "pl"
        return "Cześć"

    async def speak(text):
        assert text
        return np.array([0, 100, -100, 0], dtype=np.int16)

    audio = SimpleNamespace(
        whisper=SimpleNamespace(model_size="base", device="cpu", model=object()),
        voice=SimpleNamespace(
            model_path=str(model),
            speaker_id=0,
            is_fallback_mode=False,
            voice=object(),
            output_sample_rate=22050,
            speak=speak,
        ),
        transcribe_checked=transcribe,
    )
    app = SimpleNamespace(state=SimpleNamespace(audio_engine=audio))
    engine = VenomEngines(app)
    runtime = SimpleNamespace(
        provider="ollama",
        model_name="scene-model",
        endpoint="http://local.invalid/v1",
        config_hash="runtime-one",
    )

    async def probe(runtime):
        return {"reachable": True, "installed": True, "ready": True, "vision": True}

    async def selected():
        return copy.copy(engine._runtime())

    monkeypatch.setattr(engine, "_runtime", lambda: runtime)
    monkeypatch.setattr(engine, "_selected_runtime", selected)
    monkeypatch.setattr(engine, "_probe", probe)
    monkeypatch.setattr(engines, "_installed", lambda _: True)
    return engine, audio, runtime


async def test_status_reads_are_cached_and_do_not_load_models(adapter, monkeypatch):
    engine, audio, runtime = adapter
    calls = []

    async def probe(selected):
        calls.append(selected.model_name)
        return {"reachable": True, "installed": True, "ready": False, "vision": True}

    monkeypatch.setattr(engine, "_probe", probe)
    audio.whisper.model = audio.voice.voice = None
    first, second = await asyncio.gather(engine.capabilities(), engine.capabilities())
    assert first == second and calls == ["scene-model"]
    assert set(first["tasks"]) == set(engines.TASKS)
    for task in engines.TASKS:
        assert first["tasks"][task]["available"] is (task != "conversation.respond")
        assert not first["tasks"][task]["ready"]
        assert not first["tasks"][task]["quality"]["simulated"]
        assert first["tasks"][task]["runtime"]["config_revision"]
    assert audio.whisper.model is None and audio.voice.voice is None
    assert "local.invalid" not in json.dumps(first)
    runtime.config_hash = "runtime-two"
    assert (await engine.capabilities())["config_revision"] != first["config_revision"]
    assert calls == ["scene-model", "scene-model"]


async def test_dynamic_singleton_and_revision_see_model_switches(adapter):
    engine, audio, _ = adapter
    before = await engine.revision()
    audio.voice.speaker_id = 1
    assert await engine.revision() != before
    engine.app.state.audio_engine = None
    status = await engine.capabilities()
    assert not status["tasks"]["audio.transcribe"]["available"]
    assert not status["tasks"]["audio.synthesize"]["available"]
    engine.app.state.audio_engine = audio
    assert (await engine.capabilities())["tasks"]["audio.transcribe"]["available"]


async def test_runtime_errors_and_cloud_are_explicitly_unavailable(
    adapter, monkeypatch
):
    engine, _, runtime = adapter
    runtime.provider = "openai"
    status = await engine.capabilities()
    assert not status["tasks"]["text.respond"]["available"]
    with pytest.raises(EngineUnavailable):
        await engine.execute("text.respond", {}, b"hello", {})

    def missing():
        raise ImportError("host unavailable")

    monkeypatch.setattr(engine, "_runtime", missing)
    assert await engine.revision()
    assert not (await engine.capabilities())["tasks"]["vision.describe"]["configured"]


async def test_library_presence_does_not_pretend_model_ready(adapter, monkeypatch):
    engine, audio, _ = adapter
    monkeypatch.setattr(engines, "_installed", lambda _: False)
    status = await engine.capabilities()
    assert not status["tasks"]["audio.transcribe"]["available"]
    assert not status["tasks"]["audio.synthesize"]["available"]
    audio.transcribe_checked = None
    audio.voice.is_fallback_mode = True
    monkeypatch.setattr(engines, "_installed", lambda _: True)
    status = await engine.capabilities()
    assert status["tasks"]["audio.transcribe"]["reason"] == (
        "engine_checked_stt_unavailable"
    )
    assert status["tasks"]["audio.synthesize"]["reason"] == (
        "engine_tts_fallback_refused"
    )


@pytest.mark.parametrize("text", [None, b"", b"  ", b"\xff", b"x" * 4097])
async def test_invalid_text_input(adapter, text):
    engine, _, _ = adapter
    with pytest.raises(EngineError, match="engine_invalid_text"):
        await engine.execute("text.respond", {}, text, {})


@pytest.mark.parametrize(
    "task,params,code",
    [
        ("arm", {}, "engine_unknown_task"),
        ("text.respond", {"language": "xx"}, "engine_invalid_language"),
        ("text.respond", {"language": []}, "engine_invalid_language"),
        ("vision.describe", {"prompt": "x" * 513}, "engine_invalid_prompt"),
        ("vision.describe", {"prompt": 1}, "engine_invalid_prompt"),
        ("audio.synthesize", {"language": "en"}, "engine_language_unavailable"),
    ],
)
async def test_only_bounded_purposeful_tasks_are_permitted(adapter, task, params, code):
    engine, _, _ = adapter
    with pytest.raises(EngineError, match=code):
        await engine.execute(task, params, b"input", {})


def install_text_service(monkeypatch, events, received):
    async def stream(request):
        received.append(request)

        async def chunks():
            for event in events:
                yield event

        return SimpleNamespace(body_iterator=chunks())

    monkeypatch.setitem(
        sys.modules,
        "venom_core.services.llm_simple_service",
        SimpleNamespace(stream_simple_chat=stream),
    )


async def test_text_reuses_simple_service_without_tools(adapter, monkeypatch):
    engine, _, _ = adapter
    requests = []
    install_text_service(
        monkeypatch,
        [
            b"event: start\ndata: {}\n\n",
            'event: content\ndata: {"text":"Dobry"}\n',
            '\nevent: content\ndata: {"text":" opis"}\n\nevent: done\ndata: {}\n\n',
        ],
        requests,
    )
    result = await engine.execute(
        "text.respond", {"language": "de"}, b"question", {"session_id": "session"}
    )
    assert result.data == {"text": "Dobry opis"}
    assert result.runtime["model"] == "scene-model"
    assert result.runtime["config_revision"] == await engine.revision()
    assert not result.quality["simulated"]
    assert requests[0].tools is None and requests[0].tool_choice is None
    assert requests[0].session_id == "session" and requests[0].stream is False
    assert requests[0].max_tokens == 512 and "German" in requests[0].content
    assert requests[0].model == "scene-model"


async def test_shared_adapter_selection_is_used_for_probe_and_revision(
    adapter, monkeypatch
):
    from venom_core.services.llm_simple_service import (
        _resolve_model_name_for_simple_request,
    )

    engine, _, raw_runtime = adapter
    active = "first"

    async def adapter_id():
        return active

    monkeypatch.setitem(
        sys.modules,
        "venom_core.services.llm_simple_service",
        SimpleNamespace(
            _get_active_adapter_id=adapter_id,
            _resolve_model_name_for_simple_request=_resolve_model_name_for_simple_request,
        ),
    )

    async def selected():
        return await VenomEngines._selected_runtime(engine)

    monkeypatch.setattr(engine, "_selected_runtime", selected)
    resolved = await selected()
    assert resolved.model_name == "venom-adapter-first"
    assert raw_runtime.model_name == "scene-model"
    before = await engine.revision()
    active = "second"
    assert await engine.revision() != before
    status = await engine.capabilities()
    assert status["tasks"]["text.respond"]["runtime"]["model"] == "venom-adapter-second"


@pytest.mark.parametrize(
    "events,code",
    [
        (["event: error\ndata: {}\n\n"], "engine_text_failed"),
        (["event: done\ndata: {}\n\n"], "engine_empty_output"),
        (["x" * 65537], "engine_invalid_output"),
        (
            ["event: content\ndata: " + json.dumps({"text": "x" * 16385}) + "\n\n"],
            "engine_invalid_output",
        ),
        (["event: content\ndata: invalid\n\n"], "engine_inference_failed"),
    ],
)
async def test_text_failure_never_becomes_mock_success(
    adapter, monkeypatch, events, code
):
    engine, _, _ = adapter
    install_text_service(monkeypatch, events, [])
    with pytest.raises(EngineError, match=code):
        await engine.execute("text.respond", {}, b"question", {})


async def test_changed_model_invalidates_completed_inference(adapter, monkeypatch):
    engine, _, runtime = adapter

    async def text(*args):
        runtime.config_hash = "switched"
        return "old result"

    monkeypatch.setattr(engine, "_text", text)
    with pytest.raises(EngineError, match="engine_configuration_changed") as error:
        await engine.execute("text.respond", {}, b"question", {})
    assert error.value.status_code == 409


async def test_transcription_preserves_silence_and_actual_format(adapter):
    engine, audio, _ = adapter
    result = await engine.execute("audio.transcribe", {}, wav_bytes(), {})
    assert result.data == {"text": "Cześć", "silence": False, "sample_rate": 16000}

    async def silence(*args, **kwargs):
        return ""

    audio.transcribe_checked = silence
    result = await engine.execute("audio.transcribe", {}, wav_bytes(), {})
    assert result.data["text"] == "" and result.quality["silence"]

    async def failed(*args, **kwargs):
        raise RuntimeError("private model error")

    audio.transcribe_checked = failed
    with pytest.raises(EngineError, match="engine_inference_failed") as error:
        await engine.execute("audio.transcribe", {}, wav_bytes(), {})
    assert "private model error" not in str(error.value)


@pytest.mark.parametrize(
    "data",
    [
        b"bad",
        b"x" * (engines._MAX_MEDIA + 1),
        wav_bytes(channels=2),
        wav_bytes(width=1),
        wav_bytes(rate=7999),
        wav_bytes(samples=16 * 16000),
        wav_bytes()[:-3],
    ],
)
async def test_invalid_audio_format_is_rejected(adapter, data):
    engine, _, _ = adapter
    with pytest.raises(EngineError, match="engine_invalid_audio"):
        await engine.execute("audio.transcribe", {}, data, {})


async def test_tts_outputs_real_sample_rate_pcm16_wav_without_playback(adapter):
    engine, audio, _ = adapter
    result = await engine.execute("audio.synthesize", {}, "Cześć".encode(), {})
    assert result.content_type == "audio/wav" and result.binary is not None
    assert result.data == {"sample_rate": 22050, "channels": 1, "sample_width": 2}
    with wave.open(io.BytesIO(result.binary), "rb") as output:
        assert output.getframerate() == audio.voice.output_sample_rate
        assert output.getsampwidth() == 2 and output.getnchannels() == 1
        assert (
            output.readframes(4) == np.array([0, 100, -100, 0], dtype="<i2").tobytes()
        )


@pytest.mark.parametrize(
    "pcm,rate,code",
    [
        (None, 22050, "engine_tts_failed"),
        (np.zeros(4, dtype=np.int16), 22050, "engine_invalid_audio_output"),
        (np.ones(4, dtype=np.float32), 22050, "engine_invalid_audio_output"),
        (np.ones((2, 2), dtype=np.int16), 22050, "engine_invalid_audio_output"),
        (np.ones(4, dtype=np.int16), 48001, "engine_invalid_audio_output"),
        (np.zeros(0, dtype=np.int16), 22050, "engine_invalid_audio_output"),
        (
            np.ones(engines._MAX_OUTPUT_AUDIO // 2, dtype=np.int16),
            22050,
            "engine_invalid_audio_output",
        ),
    ],
)
async def test_tts_fallback_invalid_and_unbounded_audio_are_refused(
    adapter, pcm, rate, code
):
    engine, audio, _ = adapter

    async def speak(text):
        return pcm

    audio.voice.speak = speak
    audio.voice.output_sample_rate = rate
    with pytest.raises(EngineError, match=code):
        await engine.execute("audio.synthesize", {}, b"text", {})


async def test_tts_load_failure_is_not_a_silent_success(adapter):
    engine, audio, _ = adapter

    async def fallback(text):
        audio.voice.is_fallback_mode = True
        return np.zeros(4, dtype=np.int16)

    audio.voice.speak = fallback
    with pytest.raises(EngineError, match="engine_tts_failed"):
        await engine.execute("audio.synthesize", {}, b"text", {})


async def test_cancelled_audio_drains_before_second_inference_and_close(adapter):
    engine, audio, _ = adapter
    started, finish, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def transcribe(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await finish.wait()
        else:
            second_started.set()
        return "result"

    audio.transcribe_checked = transcribe
    first = asyncio.create_task(engine.execute("audio.transcribe", {}, wav_bytes(), {}))
    await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert engine._audio_lock.locked()
    second = asyncio.create_task(
        engine.execute("audio.transcribe", {}, wav_bytes(), {})
    )
    close = asyncio.create_task(engine.close())
    await asyncio.sleep(0)
    assert not second_started.is_set() and not close.done()
    finish.set()
    await close
    assert (await second).data["text"] == "result"
    assert second_started.is_set() and not engine._audio_lock.locked()


async def test_cancelled_drain_does_not_cancel_still_running_audio(adapter):
    engine, audio, _ = adapter
    started, finish = asyncio.Event(), asyncio.Event()

    async def transcribe(*args, **kwargs):
        started.set()
        await finish.wait()
        return "finished"

    audio.transcribe_checked = transcribe
    owner = asyncio.create_task(engine.execute("audio.transcribe", {}, wav_bytes(), {}))
    await started.wait()
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    drain = asyncio.create_task(engine.drain())
    await asyncio.sleep(0)
    drain.cancel()
    with pytest.raises(asyncio.CancelledError):
        await drain
    assert engine._audio_lock.locked()
    assert all(not work.cancelled() for work in engine._draining)
    finish.set()
    await engine.close()
    assert not engine._draining and not engine._audio_lock.locked()


class JsonResponse:
    def __init__(self, value, *, error=False):
        self.value = value
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise RuntimeError("offline")

    def json(self):
        return self.value


def install_probe_transport(monkeypatch, responses, calls):
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 1.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            calls.append(("close",))

        async def aget(self, url, **kwargs):
            calls.append(("get", url, kwargs))
            assert kwargs["disable_retry"] is True
            return JsonResponse(responses.pop(0))

        async def apost(self, url, **kwargs):
            calls.append(("post", url, kwargs))
            assert kwargs["disable_retry"] is True
            return JsonResponse(responses.pop(0))

    monkeypatch.setitem(
        sys.modules,
        "venom_core.infrastructure.traffic_control",
        SimpleNamespace(TrafficControlledHttpClient=Client),
    )


async def test_ollama_metadata_probe_uses_shared_normalizer_and_never_generates(
    adapter, monkeypatch
):
    engine, _, runtime = adapter
    calls = []
    install_probe_transport(
        monkeypatch,
        [
            {"models": [{"name": "scene-model:latest"}]},
            {"capabilities": ["completion", "vision"]},
            {"models": [{"name": "scene-model"}]},
        ],
        calls,
    )
    snapshot = await VenomEngines._probe(engine, runtime)
    assert snapshot == {
        "reachable": True,
        "installed": True,
        "ready": True,
        "vision": True,
        "vision_source": "runtime_metadata",
    }
    assert [c[1].rsplit("/", 1)[-1] for c in calls if len(c) > 1] == [
        "tags",
        "show",
        "ps",
    ]
    assert calls[-1] == ("close",)


async def test_probe_missing_model_and_compatible_model_metadata(adapter, monkeypatch):
    engine, _, runtime = adapter
    calls = []
    install_probe_transport(monkeypatch, [{"models": []}], calls)
    snapshot = await VenomEngines._probe(engine, runtime)
    assert snapshot == {"reachable": True, "installed": False, "ready": False}
    runtime.provider = "multi_runtime"
    install_probe_transport(
        monkeypatch,
        [
            {
                "data": [
                    {
                        "id": "scene-model",
                        "status": "loaded",
                        "supports_image_input": True,
                    }
                ]
            }
        ],
        calls,
    )
    snapshot = await VenomEngines._probe(engine, runtime)
    assert snapshot["installed"] and snapshot["ready"] and snapshot["vision"]
    assert calls[-2][1] == "http://local.invalid/v1/models"


@pytest.mark.parametrize(
    "endpoint",
    ["file:///tmp/model", "http://user:secret@host/v1", "http://host/v1?token=x"],
)
async def test_probe_endpoint_bounds_and_private_credentials(adapter, endpoint):
    engine, _, runtime = adapter
    runtime.endpoint = endpoint
    if endpoint.startswith("file:"):
        assert not (await VenomEngines._probe(engine, runtime))["reachable"]
    else:
        with pytest.raises(EngineUnavailable, match="engine_endpoint_invalid"):
            await VenomEngines._probe(engine, runtime)


async def test_probe_failure_and_cancellation_are_not_fake_readiness(
    adapter, monkeypatch
):
    engine, _, _ = adapter

    async def failed(runtime):
        raise RuntimeError("private connection details")

    monkeypatch.setattr(engine, "_probe", failed)
    assert not (await engine.capabilities())["tasks"]["text.respond"]["available"]
    engine._snapshot_at = 0

    async def cancelled(runtime):
        raise asyncio.CancelledError()

    monkeypatch.setattr(engine, "_probe", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await engine.capabilities()


def install_vision_transport(monkeypatch, content, received):
    class Response:
        def raise_for_status(self):
            pass

        async def aiter_bytes(self):
            yield content

    @asynccontextmanager
    async def transport(**kwargs):
        received.append(kwargs)
        yield Response()

    @asynccontextmanager
    async def guard(**kwargs):
        received.append(kwargs)
        yield

    monkeypatch.setitem(
        sys.modules,
        "venom_core.services.llm_simple_transport",
        SimpleNamespace(open_stream_response=transport),
    )
    monkeypatch.setitem(
        sys.modules,
        "venom_core.services.runtime_switch_gate",
        SimpleNamespace(runtime_request_guard=guard),
    )


@pytest.mark.parametrize("format", ["JPEG", "PNG"])
@pytest.mark.parametrize("provider", ["ollama", "vllm"])
async def test_vision_uses_active_runtime_and_bounded_scene_contract(
    adapter, monkeypatch, format, provider
):
    engine, _, runtime = adapter
    runtime.provider = provider
    received = []
    install_vision_transport(
        monkeypatch,
        json.dumps({"choices": [{"message": {"content": "Blue wall"}}]}).encode(),
        received,
    )
    result = await engine.execute(
        "vision.describe", {"language": "en"}, image_bytes(format=format), {}
    )
    assert result.data == {"text": "Blue wall"}
    assert not result.quality["navigation_clearance"]
    assert result.quality["purpose"] == "scene_description"
    request = received[1]
    assert request["provider_name"] == provider
    assert request["payload"].get("reasoning_effort") == (
        "none" if provider == "ollama" else None
    )
    assert request["payload"]["model"] == "scene-model"
    assert "tools" not in request["payload"]
    assert request["payload"]["messages"][0]["content"][1]["image_url"][
        "url"
    ].startswith(f"data:image/{format.lower()};base64,")


@pytest.mark.parametrize(
    "data",
    [
        b"bad",
        b"x" * (engines._MAX_MEDIA + 1),
        image_bytes(size=(1921, 1)),
        image_bytes(size=(1, 1081)),
        image_bytes(format="GIF"),
    ],
)
async def test_vision_rejects_invalid_or_oversized_frame(adapter, data):
    engine, _, _ = adapter
    with pytest.raises(EngineError, match="engine_invalid_image"):
        await engine.execute("vision.describe", {}, data, {})


async def test_vision_refuses_absent_capability_and_huge_runtime_response(
    adapter, monkeypatch
):
    engine, _, _ = adapter

    async def text_only(runtime):
        return {"reachable": True, "installed": True, "ready": True, "vision": False}

    monkeypatch.setattr(engine, "_probe", text_only)
    with pytest.raises(EngineUnavailable):
        await engine.execute("vision.describe", {}, image_bytes(), {})

    async def multimodal(runtime):
        return {"reachable": True, "installed": True, "ready": True, "vision": True}

    monkeypatch.setattr(engine, "_probe", multimodal)
    engine._snapshot_at = 0
    install_vision_transport(monkeypatch, b"x" * (128 * 1024 + 1), [])
    with pytest.raises(EngineError, match="engine_invalid_output"):
        await engine.execute("vision.describe", {}, image_bytes(), {})


def test_utility_bounds_and_errors(tmp_path, monkeypatch):
    error = EngineUnavailable("offline")
    assert error.code == "offline" and error.status_code == 503
    assert EngineResult(data={}).runtime == {}
    monkeypatch.setattr(engines.importlib.util, "find_spec", lambda _: None)
    assert not engines._installed("fake")

    def invalid(module):
        raise ValueError("missing spec")

    monkeypatch.setattr(engines.importlib.util, "find_spec", invalid)
    assert not engines._installed("fake")
    assert engines._path_identity(tmp_path / "missing")[1:] == (0, 0)
    assert engines._path_identity(None)[1:] == (0, 0)
    assert engines._voice_languages(None) == []
    model = tmp_path / "voice.onnx"
    voice = SimpleNamespace(model_path=str(model))
    model.with_name("voice.onnx.json").write_text("not json")
    assert engines._voice_languages(voice) == []
    model.with_name("voice.onnx.json").write_text("x" * 65537)
    assert engines._voice_languages(voice) == []
    with pytest.raises(EngineError, match="engine_invalid_output"):
        engines._checked_text(None)
