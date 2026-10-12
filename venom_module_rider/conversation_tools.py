"""Typed Rider tools and a bounded vocabulary for explicit robot requests."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from semantic_kernel.functions import kernel_function
from venom_core.services.conversation_providers import current_conversation_context

from .errors import RiderError


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: Literal["status", "observe", "speak", "face", "move", "stop"]
    direction: Literal["forward", "backward", "left", "right"] | None = None
    text: str = Field(default="", max_length=1024)
    gesture: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")

    @model_validator(mode="after")
    def required_arguments(self):
        if self.name == "move" and self.direction is None:
            raise ValueError("direction required")
        if self.name == "speak" and not self.text.strip():
            raise ValueError("text required")
        if self.name == "face" and not self.gesture:
            raise ValueError("gesture required")
        return self


def normalized(text: str) -> str:
    value = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    return " ".join(
        "".join(c for c in value if not unicodedata.combining(c)).split()
    ).strip(" .!?")


def parse_request(text: str, *, addressed=False) -> tuple[bool, ToolCall | None]:
    """Ambiguous movement never becomes a plan; the LLM cannot supply consent."""
    match = re.match(r"^\s*(?:/rider\b|rider\b)[\s,:-]*(.*)$", text, re.I | re.S)
    if not match and not addressed:
        return False, None
    if len(text.encode("utf-8")) > 4096:
        raise RiderError("conversation_input_size", 413)
    message = match.group(1) if match else text
    value = normalized(message)
    if value in {
        "stop",
        "zatrzymaj",
        "zatrzymaj sie",
        "stop robot",
        "halt",
        "anhalten",
    }:
        return True, ToolCall(name="stop")
    if value in {
        "status",
        "stan",
        "stan robota",
        "bateria",
        "battery",
        "zustand",
        "batterie",
    }:
        return True, ToolCall(name="status")
    if value in {
        "obserwuj",
        "opisz scene",
        "co widzisz",
        "observe",
        "describe scene",
        "what do you see",
        "beschreibe die szene",
    }:
        return True, ToolCall(name="observe")
    directions = {
        "forward": {
            "przod",
            "do przodu",
            "jedz do przodu",
            "krok do przodu",
            "forward",
            "move forward",
            "vorwarts",
        },
        "backward": {
            "tyl",
            "do tylu",
            "jedz do tylu",
            "krok do tylu",
            "backward",
            "move backward",
            "ruckwarts",
        },
        "left": {"lewo", "w lewo", "skrec w lewo", "left", "turn left", "links"},
        "right": {"prawo", "w prawo", "skrec w prawo", "right", "turn right", "rechts"},
    }
    for direction, choices in directions.items():
        if value in choices:
            return True, ToolCall.model_validate(
                {"name": "move", "direction": direction}
            )
    for prefixes, name in [
        (("powiedz ", "say ", "sage "), "speak"),
        (("buzka ", "gest ", "face ", "gesture ", "gesicht "), "face"),
    ]:
        for prefix in prefixes:
            if value.startswith(prefix):
                argument = message.strip()[len(prefix) :].strip()
                if name == "face":
                    argument = argument.rstrip(" .!?")
                try:
                    return True, ToolCall.model_validate(
                        {
                            "name": name,
                            "text" if name == "speak" else "gesture": argument,
                        }
                    )
                except ValueError:
                    return True, None
    if match or re.search(
        r"\b(jedz|skrec|przod|przodu|tyl|tylu|lewo|prawo|move|turn|forward|"
        r"backward|left|right|vorwarts|ruckwarts|links|rechts|arm|uzbroj|"
        r"shell|gpio|reset|height|wysokosc|balance|balans|action|home)\b",
        value,
    ):
        return True, None
    return False, None


class RiderSkill:
    """Semantic Kernel callables use the same provider as chat and voice."""

    def __init__(self, provider):
        self.provider = provider

    async def invoke(self, call: ToolCall):
        context = current_conversation_context()
        if context is None:
            raise RiderError("conversation_context_required", 403)
        return await self.provider.invoke(call, context)

    @kernel_function(
        name="rider_status",
        description="Read the explicitly selected Rider robot status.",
    )
    async def status(self) -> str:
        return await self.invoke(ToolCall(name="status"))

    @kernel_function(
        name="rider_observe",
        description="Describe the robot camera scene; never navigation clearance.",
    )
    async def observe(self) -> str:
        return await self.invoke(ToolCall(name="observe"))

    @kernel_function(
        name="rider_speak",
        description="Speak supplied text on Rider using shared Venom synthesis.",
    )
    async def speak(self, text: str) -> str:
        return await self.invoke(ToolCall(name="speak", text=text))

    @kernel_function(
        name="rider_face",
        description="Play an existing Rider face gesture without changing orientation.",
    )
    async def face(self, gesture: str) -> str:
        return await self.invoke(ToolCall(name="face", gesture=gesture))

    @kernel_function(
        name="rider_move",
        description="One Pi-calibrated pulse for an explicit direction; never arm.",
    )
    async def move(self, direction: str) -> str:
        return await self.invoke(
            ToolCall.model_validate({"name": "move", "direction": direction})
        )

    @kernel_function(
        name="rider_stop",
        description="Immediately STOP Rider through its independent safety transport.",
    )
    async def stop(self) -> str:
        return await self.invoke(ToolCall(name="stop"))
