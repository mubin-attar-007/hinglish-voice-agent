"""The confirmation gate: the guarantee outside the model.

The gate sees each sentence before the caller hears it, and decides by these rules:

    no claim about the outcome                 -> ALLOW: deliver it as written
    a claim, but no record yet                 -> BLOCK: hold, nothing to check against
    a claim before any change was attempted    -> BLOCK (premature); a stated slot is rendered
                                                  from the record instead
    a claim while the record is not fresh      -> BLOCK and ask for a read: the change is in
                                                  flight, or its outcome is unknown
    a claim while the record is fresh          -> REWRITE: say the template rendered from the
                                                  record, never the model's own words

The record is fresh when it was read after the last write, and that write's outcome is settled.
The gate is pure: it does no I/O, and learns about the world only from the tool adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from orders.models import OrderView, WriteResult
from voice_agent.gate import templates
from voice_agent.gate.claims import Claim, ClaimKind, classify
from voice_agent.gate.state import GateState, WriteState
from voice_agent.gate.templates import Language
from voice_agent.timeparse import describes_slot


class Action(StrEnum):
    ALLOW = "allow"  # deliver the sentence as written
    REWRITE = "rewrite"  # deliver the template rendered from the record instead
    BLOCK = "block"  # deliver only a holding line; the loop reads the record and asks again


@dataclass(frozen=True)
class Outcome:
    """A sentence about the outcome, rendered from a fresh record, and what it says."""

    text: str
    meaning: ClaimKind
    slot_id: str


@dataclass(frozen=True)
class Verdict:
    action: Action
    text: str  # what the caller hears
    rule: str
    claim: Claim
    meaning: ClaimKind | None = None  # what the delivered template says
    meaning_slot_id: str | None = None
    needs_read: bool = False
    agreed: bool | None = None  # REWRITE only: did the model's own claim agree with the record?
    enforced: bool = True
    would: Action | None = None  # observe mode: what the gate would have done


class ConfirmationGate:
    def __init__(
        self, *, language: Language, now: Callable[[], datetime], enforce: bool = True
    ) -> None:
        self.language: Language = language
        self.enforce = enforce
        self.state = GateState()
        self._now = now
        self._caller_last: str | None = None

    # ---- observations, from the tool adapter only ----------------------------------------------

    def observe_lookup(self, view: OrderView | None, seq: int) -> None:
        if view is None:
            return
        state = self.state
        state.order_id = view.order_id
        if state.original_slot_id is None:
            state.original_slot_id = view.slot.slot_id
        state.record = view
        state.last_read_seq = max(state.last_read_seq, seq)

    def observe_read(self, view: OrderView, seq: int) -> None:
        state = self.state
        state.record = view
        state.last_read_seq = max(state.last_read_seq, seq)
        if state.write in (WriteState.UNKNOWN, WriteState.IN_FLIGHT) and seq > state.last_write_seq:
            state.write = WriteState.RESOLVED

    def observe_write_started(self, slot_id: str, seq: int) -> None:
        state = self.state
        state.write = WriteState.IN_FLIGHT
        state.last_write_seq = seq
        state.intended_slot_id = slot_id
        state.last_answer = None

    def observe_write_answer(self, result: WriteResult) -> None:
        self.state.write = WriteState.RESOLVED
        self.state.last_answer = result.status

    def observe_write_lost(self) -> None:
        """The request timed out, failed or was cancelled: its outcome is unknown."""
        self.state.write = WriteState.UNKNOWN

    # ---- the turn ------------------------------------------------------------------------------

    def start_turn(self, caller_text: str) -> None:
        self._caller_last = caller_text

    def outcome_sentence(self) -> Outcome | None:
        """What to tell the caller about the change, if the record is fresh enough to say it."""
        state = self.state
        if not state.fresh or state.record is None:
            return None
        slot = state.record.slot
        if state.change_saved:
            return Outcome(
                templates.confirmed(slot, self.language), ClaimKind.CONFIRM, slot.slot_id
            )
        return Outcome(
            templates.unchanged(slot, state.last_answer, self.language),
            ClaimKind.FAIL,
            slot.slot_id,
        )

    def check(self, sentence: str) -> Verdict:
        claim = classify(sentence, self._now(), caller_last=self._caller_last)
        verdict = self._decide(sentence, claim)
        if self.enforce or verdict.action is Action.ALLOW:
            return verdict
        return Verdict(
            Action.ALLOW,
            sentence,
            verdict.rule,
            claim,
            agreed=verdict.agreed,
            enforced=False,
            would=verdict.action,
        )

    def _decide(self, sentence: str, claim: Claim) -> Verdict:
        if not claim.kind.is_claim:
            return Verdict(Action.ALLOW, sentence, "no_claim", claim)
        state = self.state
        hold = templates.holding(self.language)
        if state.record is None:
            return Verdict(Action.BLOCK, hold, "no_record", claim)
        if state.write is WriteState.NONE:
            if claim.kind is ClaimKind.CONFIRM:
                return Verdict(Action.BLOCK, hold, "premature", claim)
            # Stating the slot, or saying nothing changed: true or not, the record says it.
            slot = state.record.slot
            agreed = claim.kind is ClaimKind.FAIL or (
                describes_slot(claim.when, slot.day, slot.start, slot.end) is not False
            )
            return Verdict(
                Action.REWRITE,
                templates.current(slot, self.language),
                "render_current",
                claim,
                ClaimKind.STATE_SLOT,
                slot.slot_id,
                agreed=agreed,
            )
        outcome = self.outcome_sentence()
        if outcome is None:
            return Verdict(Action.BLOCK, hold, "not_fresh", claim, needs_read=True)
        return Verdict(
            Action.REWRITE,
            outcome.text,
            "render_from_record",
            claim,
            outcome.meaning,
            outcome.slot_id,
            agreed=self._agrees(claim, outcome),
        )

    def _agrees(self, claim: Claim, outcome: Outcome) -> bool:
        record = self.state.record
        if record is None:
            return False
        slot = record.slot
        consistent = describes_slot(claim.when, slot.day, slot.start, slot.end) is not False
        if claim.kind is ClaimKind.CONFIRM:
            return outcome.meaning is ClaimKind.CONFIRM and consistent
        if claim.kind is ClaimKind.FAIL:
            return outcome.meaning is ClaimKind.FAIL
        return consistent
