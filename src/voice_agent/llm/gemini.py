"""Gemini through the google-genai SDK, with manual function calling.

The model's context is rebuilt from the agent's own history, which keeps what the caller heard,
not what the model generated. The model's function-call parts are reused as they came back, so
any thought signature they carry is returned to the API unchanged.

The key comes only from the GEMINI_API_KEY environment variable, and is never logged.
"""

from __future__ import annotations

import itertools
import json
import os
import time
from collections.abc import Iterator, Sequence
from typing import Any

from voice_agent.llm.base import Done, Message, StreamEvent, TextDelta, Usage
from voice_agent.tools import ToolCall, ToolSpec

DEFAULT_MODEL = "gemini-3.8-flash"


class GeminiLLM:
    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        client: Any | None = None,
        seed: int | None = None,
    ) -> None:
        self.name = model
        self._seed = seed
        if client is None:
            from google import genai

            key = os.environ.get("GEMINI_API_KEY")
            if not key:
                raise RuntimeError("set GEMINI_API_KEY to use the Gemini provider")
            client = genai.Client(api_key=key)
        self._client = client
        self._ids = itertools.count(1)
        self._call_parts: dict[str, Any] = {}  # our tool-call id -> the model's own part

    def stream(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> Iterator[StreamEvent]:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=spec.name,
                            description=spec.description,
                            parameters_json_schema=spec.parameters,
                        )
                        for spec in tools
                    ]
                )
            ],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            seed=self._seed,
        )
        started = time.perf_counter()
        response = self._client.models.generate_content(
            model=self.name, contents=self._contents(messages), config=config
        )
        latency_ms = (time.perf_counter() - started) * 1000

        parts = _parts(response)
        text = "".join(part.text for part in parts if getattr(part, "text", None))
        if text:
            yield TextDelta(text)
        for part in parts:
            call = getattr(part, "function_call", None)
            if call is None:
                continue
            call_id = getattr(call, "id", None) or f"gemini-{next(self._ids)}"
            self._call_parts[call_id] = part
            yield ToolCall(call_id, call.name, dict(call.args or {}))
        usage = getattr(response, "usage_metadata", None)
        yield Done(
            model=self.name,
            usage=Usage(
                input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
                output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            ),
            latency_ms=latency_ms,
        )

    def _contents(self, messages: Sequence[Message]) -> list[Any]:
        from google.genai import types

        contents: list[Any] = []
        pending_responses: list[Any] = []

        def flush_responses() -> None:
            if pending_responses:
                contents.append(types.Content(role="tool", parts=list(pending_responses)))
                pending_responses.clear()

        for message in messages:
            if message.role == "tool":
                pending_responses.append(
                    types.Part.from_function_response(
                        name=message.name or "", response=json.loads(message.content or "{}")
                    )
                )
                continue
            flush_responses()
            if message.role == "user":
                contents.append(
                    types.Content(role="user", parts=[types.Part.from_text(text=message.content)])
                )
                continue
            parts: list[Any] = []
            if message.content:
                parts.append(types.Part.from_text(text=message.content))
            for call in message.tool_calls:
                parts.append(
                    self._call_parts.get(call.id)
                    or types.Part.from_function_call(name=call.name, args=call.arguments)
                )
            if parts:
                contents.append(types.Content(role="model", parts=parts))
        flush_responses()
        return contents


def _parts(response: Any) -> list[Any]:
    candidates = getattr(response, "candidates", None) or []
    if not candidates or candidates[0].content is None:
        return []
    return list(candidates[0].content.parts or [])
