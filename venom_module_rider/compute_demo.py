"""Explicit isolated fixture engines. Never chosen for a physical device."""

import io
import wave
from dataclasses import dataclass
from typing import Any

from .compute_contract import TASKS

_CONVERSATION_TASK = "conversation.respond"


@dataclass
class DemoResult:
    data: dict
    runtime: dict
    quality: dict
    binary: bytes | None = None
    content_type: str | None = None


class DemoEngines:
    def __init__(self, app=None):
        self.app = app

    async def revision(self):
        return "demo-v1"

    async def capabilities(self):
        """Describe the in-process demo tasks and their simulated runtime."""
        result: dict[str, Any] = {
            "config_revision": "demo-v1",
            "tasks": {
                task: {
                    "available": True,
                    "installed": True,
                    "configured": True,
                    "ready": True,
                    "runtime": {
                        "provider": "fixture",
                        "model": "demo",
                        "config_revision": "demo-v1",
                    },
                    "quality": {"simulated": True},
                    "languages": ["pl", "en", "de"],
                }
                for task in TASKS
            },
        }
        from .conversation_engine import capability

        result["tasks"][_CONVERSATION_TASK] = {
            **capability(getattr(self, "app", None)),
            "quality": {"simulated": True},
        }
        result["tasks"][_CONVERSATION_TASK]["runtime"]["config_revision"] = "demo-v1"
        return result

    async def execute(self, task, params, data, metadata):
        """Execute an admitted demo compute task without physical-device access."""
        if task == _CONVERSATION_TASK:
            from .conversation_engine import execute

            response = await execute(self.app, data, metadata, params["language"])
            if response is not None:
                response.quality = {"simulated": True}
                return response
        runtime = {"provider": "fixture", "model": "demo", "config_revision": "demo-v1"}
        result = DemoResult(
            {"language": params["language"]}, runtime, {"simulated": True}
        )
        if task == "audio.synthesize":
            stream = io.BytesIO()
            with wave.open(stream, "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b"\0\0" * 1600)
            result.binary, result.content_type = stream.getvalue(), "audio/wav"
            result.data["sample_rate"] = 16000
        elif task == "audio.transcribe":
            result.data["text"] = "Demo: transkrypcja fixture"
        elif task == "vision.describe":
            result.data["text"] = (
                "Demo: obraz fixture; opis nie ocenia bezpieczeństwa przejazdu."
            )
        else:
            result.data["text"] = "Demo: " + data.decode("utf-8")
        return result

    async def close(self):
        return None

    async def prepare_conversation(self, data, metadata, language):
        from .conversation_engine import prepare

        return await prepare(self.app, data, metadata, language)

    async def finish_conversation(self, turn):
        from .conversation_engine import finish

        await finish(self.app, turn)

    async def urgent_conversation(self, data, metadata, language):
        from .conversation_engine import execute
        from .engines import EngineError

        result = await execute(self.app, data, metadata, language)
        if result is None:
            raise EngineError("conversation_unhandled", 422)
        result.quality = {"simulated": True}
        return result
