"""The text-mode loop: what the caller hears, whatever the model streams."""

from __future__ import annotations

from datetime import date, datetime, time

from orders.clock import IST, VirtualClock
from orders.db import OrderSpec, WorldSpec, connect, seed, slot_id_for
from orders.faults import FaultKind, FaultPlan, FaultRule
from orders.service import OrderService
from voice_agent.gate.gate import ConfirmationGate
from voice_agent.llm.base import TextDelta
from voice_agent.llm.mock import ScriptedLLM
from voice_agent.textloop import TextAgent
from voice_agent.tools import SafetyConfig, ToolAdapter, ToolCall

NOW = datetime(2026, 11, 26, 10, 0, tzinfo=IST)
SAT_4PM = slot_id_for(date(2026, 11, 28), time(16))
ORDER = "48291"


def agent(
    steps: list[list[TextDelta | ToolCall]], *rules: FaultRule, safety: SafetyConfig | None = None
) -> TextAgent:
    conn = connect()
    friday = slot_id_for(date(2026, 11, 27), time(14))
    seed(
        conn,
        WorldSpec(
            first_day=date(2026, 11, 27),
            days=2,
            orders=(OrderSpec(ORDER, "380015", "table", friday),),
        ),
    )
    clock = VirtualClock(NOW)
    service = OrderService(conn, clock=clock, faults=FaultPlan(rules), call_id="c1")
    safety = safety or SafetyConfig()
    gate = ConfirmationGate(language="english", now=clock.now, enforce=safety.gate)
    adapter = ToolAdapter(service, gate, call_id="c1", safety=safety)
    return TextAgent(ScriptedLLM(steps), adapter, gate, system_prompt="sys", safety=safety)


def lookup() -> ToolCall:
    return ToolCall("l1", "lookup_order", {"order_id": ORDER, "pincode": "380015"})


def listing() -> ToolCall:
    return ToolCall("s1", "list_slots", {"date": "2026-11-28", "after_time": "16:00"})


def reschedule() -> ToolCall:
    return ToolCall("r1", "reschedule", {"order_id": ORDER, "slot_id": SAT_4PM})


def test_after_a_write_code_speaks_the_outcome_and_the_model_is_not_asked() -> None:
    a = agent(
        [
            [lookup(), listing()],
            [TextDelta("Which slot? ")],
            [reschedule()],
            [TextDelta("Your request went through. ")],  # never reached: the turn ends first
        ],
        FaultRule(FaultKind.TIMEOUT_AFTER_COMMIT),
    )
    a.respond("Order 48291, PIN 380015, Saturday evening.")
    heard = a.respond("The 4 to 6 one.")
    assert [s.source for s in heard] == ["template"]
    assert heard[0].rule == "outcome_by_code"
    assert heard[0].text.startswith("Done.")
    assert "went through" not in " ".join(s.text for s in a.log.spoken)


def test_text_in_the_same_response_as_a_write_is_not_spoken() -> None:
    """A paraphrased claim the detector doesn't know can't get out before the write."""
    a = agent(
        [
            [lookup(), listing()],
            [TextDelta("बस, अब आपकी delivery Saturday को आ जाएगी। "), reschedule()],
        ],
        FaultRule(FaultKind.ERROR_BEFORE_COMMIT),
    )
    heard = a.respond("Order 48291, PIN 380015, Saturday 4 to 6.")
    assert [s.source for s in heard] == ["template"]
    assert heard[0].text.startswith("The change was not saved")
    assert a.log.dropped[0]["why"] == "same_response_as_write"


def test_a_premature_claim_is_held_and_the_model_is_asked_again_with_a_note() -> None:
    llm_steps: list[list[TextDelta | ToolCall]] = [
        [lookup()],
        [TextDelta("Done, your delivery is moved. ")],  # premature: nothing was changed
        [TextDelta("Which day would you like? ")],
    ]
    a = agent(llm_steps)
    heard = a.respond("Order 48291, PIN 380015.")
    assert [s.source for s in heard] == ["holding", "model"]
    assert heard[1].text == "Which day would you like?"
    llm = a.llm
    assert isinstance(llm, ScriptedLLM)
    assert "[Gate note]" in llm.systems[-1]


def test_a_claim_while_the_outcome_is_unknown_is_answered_from_the_record() -> None:
    no_outcome_by_code = SafetyConfig(outcome_by_code=False, reread=False)
    a = agent(
        [
            [lookup(), listing()],
            [reschedule()],
            [TextDelta("Sorry, the change failed. ")],  # the write committed, then timed out
        ],
        FaultRule(FaultKind.TIMEOUT_AFTER_COMMIT),
        safety=no_outcome_by_code,
    )
    heard = a.respond("Order 48291, PIN 380015, Saturday 4 to 6.")
    assert heard[-1].rule == "answered_from_record"
    assert heard[-1].text.startswith("Done.")  # the record shows the change saved


def test_barge_in_keeps_only_the_heard_words_in_the_model_context() -> None:
    a = agent([[TextDelta("On Saturday I have 4 to 6 PM and 6 to 8 PM. Which one? ")]])
    a.respond("Hello")
    a.barge_in(after_words=3)
    assert a.log.spoken[0].heard_text == "On Saturday I"
    assert a.log.spoken[0].cut is True
    assert a.log.spoken[1].heard is False
    assert a.history[-1].content == "On Saturday I"


def test_an_outcome_cut_off_by_barge_in_is_said_again_next_turn() -> None:
    a = agent([[lookup(), listing()], [reschedule()], [TextDelta("Anything else? ")]])
    a.respond("Order 48291, PIN 380015, Saturday 4 to 6.")
    a.barge_in(after_words=2)  # the caller talks over "Done. Your delivery is now on..."
    heard = a.respond("Sorry, what?")
    assert heard[0].rule == "redisclose"
    assert heard[0].text.startswith("Done.")


def test_observe_mode_speaks_the_model_s_claims_but_records_the_verdicts() -> None:
    a = agent([[TextDelta("Done! ")]], safety=SafetyConfig.baseline())
    heard = a.respond("Hello")
    assert heard[0].text == "Done!"
    assert a.log.verdicts[0]["would"] == "block"
