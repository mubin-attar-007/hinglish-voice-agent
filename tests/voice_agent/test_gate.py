"""The confirmation gate: written before the gate, as the brief asks.

The gate decides, sentence by sentence, what the caller may hear about the outcome of a change.
It only ever trusts the system of record, as fed to it by the tool adapter.
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from orders.clock import IST
from orders.db import slot_id_for
from orders.models import OrderView, Slot, WriteResult, WriteStatus
from voice_agent.gate.claims import ClaimKind
from voice_agent.gate.gate import Action, ConfirmationGate
from voice_agent.gate.state import WriteState

NOW = datetime(2026, 11, 26, 10, 0, tzinfo=IST)
FRI, SAT = date(2026, 11, 27), date(2026, 11, 28)


def slot(day: date, hour: int) -> Slot:
    return Slot(slot_id_for(day, time(hour)), day, time(hour), time(hour + 2), 3, 1)


FRI_2PM, SAT_4PM, SAT_6PM = slot(FRI, 14), slot(SAT, 16), slot(SAT, 18)


def view(at: Slot, version: int) -> OrderView:
    return OrderView("48291", "study table", at, version)


def gate(language: str = "hinglish", enforce: bool = True) -> ConfirmationGate:
    g = ConfirmationGate(language=language, now=lambda: NOW, enforce=enforce)  # type: ignore[arg-type]
    g.start_turn("Mera order 48291 hai, pincode 380015")
    g.observe_lookup(view(FRI_2PM, 1), seq=2)
    return g


def committed(g: ConfirmationGate, *, write_seq: int = 5, read_seq: int = 8) -> None:
    g.observe_write_started(SAT_4PM.slot_id, seq=write_seq)
    g.observe_write_answer(
        WriteResult(
            WriteStatus.COMMITTED, "48291", SAT_4PM.slot_id, view(SAT_4PM, 2), write_seq + 1
        )
    )
    g.observe_read(view(SAT_4PM, 2), seq=read_seq)


def timed_out_then_read(g: ConfirmationGate, record: Slot, version: int) -> None:
    g.observe_write_started(SAT_4PM.slot_id, seq=5)
    g.observe_write_lost()
    assert g.state.write is WriteState.UNKNOWN
    g.observe_read(view(record, version), seq=9)


# ---- the decision table -----------------------------------------------------------------------


def test_sentences_without_claims_pass_unchanged() -> None:
    g = gate()
    for sentence in (
        "Saturday ko shaam 4 se 6 aur 6 se 8 available hai. Kaunsa chahiye?",
        "Kya main ise Saturday 4 se 6 kar doon?",
        "Ek second, main check kar rahi hoon.",
        "Namaste! Main aapki kya madad kar sakti hoon?",
    ):
        verdict = g.check(sentence)
        assert verdict.action is Action.ALLOW, sentence
        assert verdict.text == sentence


def test_a_question_that_uses_outcome_words_is_not_blocked() -> None:
    """The demo's step 3: the agent must be able to ask before it changes anything."""
    g = gate(language="english")
    for question in (
        "Would you like it rescheduled to Saturday, 4 to 6 PM?",
        "Shall I confirm the 4 to 6 slot for you?",
    ):
        assert g.check(question).action is Action.ALLOW, question


def test_a_yes_that_names_a_slot_is_a_confirmation_when_the_caller_asked() -> None:
    g = gate()
    g.observe_write_started(SAT_4PM.slot_id, seq=5)
    g.observe_write_lost()
    g.start_turn("Saturday pe shift ho gaya na?")
    assert g.check("Haan ji, Saturday shaam ko.").action is Action.BLOCK


def test_a_slot_statement_with_offer_words_is_still_checked() -> None:
    g = gate(language="english")
    verdict = g.check(
        "Your delivery is on Saturday between 4 and 6 PM, which is the earliest option."
    )
    assert verdict.action is Action.REWRITE  # rendered from the record: it is on Friday
    assert verdict.agreed is False


def test_a_confirmation_before_any_change_is_blocked_as_premature() -> None:
    g = gate()
    verdict = g.check("Ho gaya! Aapki delivery Saturday 4 se 6 kar di hai.")
    assert verdict.action is Action.BLOCK
    assert verdict.rule == "premature"


def test_a_confirmation_while_the_write_is_in_flight_is_blocked_and_asks_for_a_read() -> None:
    g = gate()
    g.observe_write_started(SAT_4PM.slot_id, seq=5)
    verdict = g.check("Done, your delivery is moved to Saturday.")
    assert verdict.action is Action.BLOCK
    assert verdict.needs_read is True


def test_after_a_timeout_any_outcome_claim_is_blocked_until_a_read() -> None:
    g = gate()
    g.observe_write_started(SAT_4PM.slot_id, seq=5)
    g.observe_write_lost()
    for sentence in (
        "Sorry, booking fail ho gayi.",
        "Ho gaya!",
        "Aapki delivery Saturday ko hogi.",
    ):
        verdict = g.check(sentence)
        assert verdict.action is Action.BLOCK, sentence
        assert verdict.needs_read is True


def test_an_earlier_read_does_not_count_after_a_new_write() -> None:
    g = gate()
    committed(g)
    g.observe_write_started(SAT_6PM.slot_id, seq=12)  # a second change: the old read is stale now
    assert g.check("Ho gaya!").action is Action.BLOCK


def test_a_true_confirmation_is_rewritten_from_the_record_and_marked_as_agreed() -> None:
    g = gate()
    committed(g)
    verdict = g.check("Ho gaya! Aapki delivery ab Saturday shaam 4 se 6 baje hogi.")
    assert verdict.action is Action.REWRITE
    assert verdict.agreed is True
    assert verdict.meaning is ClaimKind.CONFIRM
    assert verdict.meaning_slot_id == SAT_4PM.slot_id
    assert "Saturday" in verdict.text and "4 से 6" in verdict.text


def test_a_confirmation_with_the_wrong_time_is_rewritten_with_the_recorded_time() -> None:
    g = gate()
    committed(g)
    verdict = g.check("Ho gaya, Saturday shaam 6 se 8 baje.")
    assert verdict.action is Action.REWRITE
    assert verdict.agreed is False
    assert "4 से 6" in verdict.text


def test_a_false_failure_after_a_timeout_becomes_the_verified_confirmation() -> None:
    """Failure mode 1: the change was saved, the model says it failed."""
    g = gate()
    timed_out_then_read(g, record=SAT_4PM, version=2)
    verdict = g.check("Sorry, system mein error aa gaya, change nahi ho paya.")
    assert verdict.action is Action.REWRITE
    assert verdict.agreed is False
    assert verdict.meaning is ClaimKind.CONFIRM


def test_a_false_confirmation_after_a_timeout_becomes_the_truth() -> None:
    """Failure mode 3: nothing was saved, the model says it was."""
    g = gate()
    timed_out_then_read(g, record=FRI_2PM, version=1)
    verdict = g.check("Ho gaya! Saturday 4 se 6 ki delivery confirm hai.")
    assert verdict.action is Action.REWRITE
    assert verdict.agreed is False
    assert verdict.meaning is ClaimKind.FAIL
    assert "Friday" in verdict.text


def test_a_confirmation_after_the_slot_was_taken_meanwhile_becomes_the_truth() -> None:
    g = gate()
    g.observe_write_started(SAT_4PM.slot_id, seq=5)
    g.observe_write_answer(
        WriteResult(WriteStatus.SLOT_FULL, "48291", SAT_4PM.slot_id, view(FRI_2PM, 1), None)
    )
    g.observe_read(view(FRI_2PM, 1), seq=7)
    verdict = g.check("Aapki delivery Saturday 4 baje ke liye book ho gayi hai.")
    assert verdict.action is Action.REWRITE
    assert verdict.meaning is ClaimKind.FAIL


def test_stating_the_current_slot_before_any_change_is_rendered_from_the_record() -> None:
    g = gate()
    verdict = g.check("Aapki delivery abhi Friday dopahar 2 se 4 baje hai.")
    assert verdict.action is Action.REWRITE
    assert verdict.meaning is ClaimKind.STATE_SLOT
    assert verdict.agreed is True


def test_a_failure_before_any_change_was_attempted_is_let_through() -> None:
    """Nothing was attempted, so it can't be a false failure, and it is often the real news."""
    g = gate()
    for sentence in (
        "ठीक है, मैंने कोई change नहीं किया।",  # a cancelled change
        "Woh slot book nahi ho sakta.",
    ):
        verdict = g.check(sentence)
        assert verdict.action is Action.ALLOW, sentence
        assert verdict.rule == "nothing_attempted"
    no_order = ConfirmationGate(language="english", now=lambda: NOW)
    no_order.start_turn("Order 48219, PIN 380015.")
    verdict = no_order.check("I couldn't find that order.")  # before any record exists
    assert verdict.action is Action.ALLOW


def test_a_claim_with_no_record_at_all_is_blocked() -> None:
    g = ConfirmationGate(language="hinglish", now=lambda: NOW)
    g.start_turn("hello")
    assert g.check("Ho gaya!").action is Action.BLOCK


# ---- scripts, paraphrases and context ----------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "हो गया! आपकी delivery अब Saturday को है।",
        "Aapki delivery shift kar di gayi hai.",
        "Your delivery has been rescheduled to Saturday.",
        "All set, Saturday it is.",
        "Booking confirm ho gayi hai.",
        "Maine change kar diya hai.",
    ],
)
def test_paraphrased_and_devanagari_confirmations_are_caught(sentence: str) -> None:
    g = gate()
    assert g.check(sentence).action is Action.BLOCK  # premature: no change has been made yet


def test_a_bare_yes_to_did_it_work_is_a_confirmation() -> None:
    g = gate()
    g.start_turn("Ho gaya kya?")
    assert g.check("Haan ji.").action is Action.BLOCK


def test_a_bare_yes_to_anything_else_is_not_a_claim() -> None:
    g = gate()
    g.start_turn("Kya aap meri baat sun rahe ho?")
    assert g.check("Haan ji.").action is Action.ALLOW


def test_english_templates_are_used_for_english_calls() -> None:
    g = gate(language="english")
    committed(g)
    verdict = g.check("Done!")
    assert verdict.text.startswith("Done.")
    assert "Saturday, 28 November" in verdict.text
    assert "between 4 and 6 PM" in verdict.text


# ---- observe mode (the no-gate baseline) -------------------------------------------------------


def test_observe_mode_lets_everything_through_but_records_what_it_would_have_done() -> None:
    g = gate(enforce=False)
    verdict = g.check("Ho gaya!")
    assert verdict.action is Action.ALLOW
    assert verdict.text == "Ho gaya!"
    assert verdict.would is Action.BLOCK
    assert verdict.enforced is False


# ---- what the adapter can ask the gate ---------------------------------------------------------


def test_the_outcome_sentence_comes_only_from_a_fresh_record() -> None:
    g = gate()
    g.observe_write_started(SAT_4PM.slot_id, seq=5)
    g.observe_write_lost()
    assert g.outcome_sentence() is None  # not fresh: nothing to say yet
    g.observe_read(view(SAT_4PM, 2), seq=9)
    outcome = g.outcome_sentence()
    assert outcome is not None
    assert outcome.meaning is ClaimKind.CONFIRM
    assert outcome.slot_id == SAT_4PM.slot_id
