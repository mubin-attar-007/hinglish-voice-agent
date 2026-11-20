"""The provider interface: a model streams text and tool calls for one step of the conversation."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from voice_agent.calllog import Meaning
from voice_agent.tools import ToolCall, ToolSpec


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()  # assistant messages that called tools
    tool_call_id: str | None = None  # tool messages
    name: str | None = None  # tool messages: the tool's name


@dataclass(frozen=True)
class TextDelta:
    text: str
    meaning: Meaning | None = None  # ground truth, set only by mock models; never by real ones


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0  # prompt and tool-use prompt tokens
    output_tokens: int = 0  # visible and thinking tokens: both are billed as output
    thinking_tokens: int = 0  # the thinking share of output_tokens


@dataclass(frozen=True)
class Done:
    model: str
    usage: Usage = Usage()
    latency_ms: float = 0.0


StreamEvent = TextDelta | ToolCall | Done


class LLMProvider(Protocol):
    name: str

    def stream(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> Iterator[StreamEvent]:
        """One model step: text deltas and tool calls, then a Done with usage."""
        ...
