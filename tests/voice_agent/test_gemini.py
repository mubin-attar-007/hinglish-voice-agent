"""The Gemini provider, against a fake client: no network, no key."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import types

from voice_agent.llm.base import Done, Message, TextDelta, Usage
from voice_agent.llm.gemini import GeminiLLM
from voice_agent.llm.pricing import PRICES, BudgetExceeded, CostMeter
from voice_agent.tools import TOOL_SPECS, ToolCall


class FakeModels:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, *, model: str, contents: list[Any], config: Any) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self.responses.pop(0)


def response(*parts: Any, prompt: int = 120, output: int = 15) -> Any:
    return SimpleNamespace(
        candidates=[SimpleNamespace(content=types.Content(role="model", parts=list(parts)))],
        usage_metadata=SimpleNamespace(prompt_token_count=prompt, candidates_token_count=output),
    )


def test_text_tool_calls_and_usage_become_stream_events() -> None:
    call = types.Part.from_function_call(
        name="lookup_order", args={"order_id": "48291", "pincode": "380015"}
    )
    fake = FakeModels([response(types.Part.from_text(text="Ek second. "), call)])
    llm = GeminiLLM(client=SimpleNamespace(models=fake), seed=1)
    events = list(llm.stream("system", [Message("user", "Order 48291, PIN 380015")], TOOL_SPECS))

    assert isinstance(events[0], TextDelta) and events[0].text == "Ek second. "
    assert isinstance(events[1], ToolCall) and events[1].name == "lookup_order"
    assert events[1].arguments == {"order_id": "48291", "pincode": "380015"}
    assert isinstance(events[-1], Done)
    assert (events[-1].usage.input_tokens, events[-1].usage.output_tokens) == (120, 15)
    config = fake.calls[0]["config"]
    assert config.automatic_function_calling.disable is True
    assert [d.name for d in config.tools[0].function_declarations] == [s.name for s in TOOL_SPECS]
    assert "idempotency" not in str(config.tools[0].function_declarations)


def test_history_keeps_heard_text_and_the_model_s_own_call_parts() -> None:
    signed = types.Part(
        function_call=types.FunctionCall(name="get_order", args={"order_id": "48291"}),
        thought_signature=b"sig",
    )
    fake = FakeModels([response(signed), response(types.Part.from_text(text="ok"))])
    llm = GeminiLLM(client=SimpleNamespace(models=fake))
    first = list(llm.stream("s", [Message("user", "hi")], TOOL_SPECS))
    call = next(e for e in first if isinstance(e, ToolCall))

    history = [
        Message("user", "hi"),
        Message("assistant", "", tool_calls=(call,)),
        Message("tool", '{"order": {"order_id": "48291"}}', tool_call_id=call.id, name="get_order"),
        Message("assistant", "heard words only"),
        Message("user", "and?"),
    ]
    list(llm.stream("s", history, TOOL_SPECS))
    contents = fake.calls[1]["contents"]
    assert [c.role for c in contents] == ["user", "model", "user", "model", "user"]
    assert contents[1].parts[0].thought_signature == b"sig"  # returned unchanged
    response_part = contents[2].parts[0].function_response
    assert response_part.response == {"order": {"order_id": "48291"}}
    assert response_part.id is None  # the model gave no id, so none is invented
    assert contents[3].parts[0].text == "heard words only"


def test_thought_parts_are_never_spoken_and_thinking_tokens_are_billed() -> None:
    thought = types.Part(text="The caller wants Saturday; call list_slots.", thought=True)
    reply = types.Part.from_text(text="Which slot would you like?")
    fake = FakeModels([response(thought, reply, prompt=200, output=10)])
    fake.responses[0].usage_metadata.thoughts_token_count = 500
    fake.responses[0].usage_metadata.tool_use_prompt_token_count = 30
    meter = CostMeter(PRICES["gemini-3.8-flash"], cap_usd=1.0)
    llm = GeminiLLM(client=SimpleNamespace(models=fake), meter=meter)
    events = list(llm.stream("s", [Message("user", "hi")], TOOL_SPECS))
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Which slot would you like?"]
    assert (meter.input_tokens, meter.output_tokens, meter.thinking_tokens) == (230, 510, 500)


def test_the_meter_refuses_a_request_once_the_cap_is_reached() -> None:
    meter = CostMeter(PRICES["gemini-3.8-flash"], cap_usd=0.0001)
    meter.charge(Usage(input_tokens=1000, output_tokens=0))  # $0.00075, past the cap
    fake = FakeModels([])
    llm = GeminiLLM(client=SimpleNamespace(models=fake), meter=meter)
    with pytest.raises(BudgetExceeded):
        list(llm.stream("s", [Message("user", "hi")], TOOL_SPECS))
    assert fake.calls == []  # nothing was sent


def test_a_model_supplied_call_id_goes_back_on_the_function_response() -> None:
    call = types.Part(function_call=types.FunctionCall(id="fc-7", name="get_order", args={}))
    fake = FakeModels([response(call), response(types.Part.from_text(text="ok"))])
    llm = GeminiLLM(client=SimpleNamespace(models=fake))
    tool_call = next(
        e for e in llm.stream("s", [Message("user", "hi")], TOOL_SPECS) if isinstance(e, ToolCall)
    )
    assert tool_call.id == "fc-7"
    history = [
        Message("user", "hi"),
        Message("assistant", "", tool_calls=(tool_call,)),
        Message("tool", "{}", tool_call_id="fc-7", name="get_order"),
    ]
    list(llm.stream("s", history, TOOL_SPECS))
    assert fake.calls[1]["contents"][2].parts[0].function_response.id == "fc-7"


def test_without_a_key_it_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        GeminiLLM()
