"""The scenario schema, and the loader for a frozen, versioned corpus."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from orders.clock import IST
from orders.db import OrderSpec, WorldSpec
from orders.faults import FaultKind, FaultPlan, FaultRule, Operation
from voice_agent.gate.templates import Language

CORPUS_DIR = Path(__file__).parent / "corpus"
DEFAULT_NOW = datetime(2026, 11, 26, 10, 0, tzinfo=IST)  # a Thursday
FaultMode = Literal["on", "scenario", "off"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OrderModel(_Model):
    order_id: str = "48291"
    pincode: str = "380015"
    item: str = "study table"
    slot: str = "20261127-1400"  # Friday, 2 to 4 PM


class WorldModel(_Model):
    first_day: date = date(2026, 11, 27)
    days: int = 7
    capacity: int = 3
    order: OrderModel = OrderModel()
    full_slots: tuple[str, ...] = ()

    def spec(self) -> WorldSpec:
        order = self.order
        return WorldSpec(
            first_day=self.first_day,
            days=self.days,
            capacity=self.capacity,
            orders=(OrderSpec(order.order_id, order.pincode, order.item, order.slot),),
            full_slots=self.full_slots,
        )

    @property
    def last_day(self) -> date:
        return self.first_day + timedelta(days=self.days - 1)


class FaultModel(_Model):
    kind: FaultKind
    op: Operation = "reschedule"
    nth: int | None = 1
    delay_ms: int = 0

    def rule(self) -> FaultRule:
        return FaultRule(self.kind, self.op, self.nth, self.delay_ms)


class Goal(_Model):
    """The slot the caller wants: the earliest offered slot that fits is the one they pick."""

    date: date
    after: time | None = None
    before: time | None = None


class Trigger(_Model):
    when: Literal["slot_offer"] = "slot_offer"  # not "on": YAML 1.1 reads that as true
    nth: int = 1  # the n-th time the agent offers slots
    interrupt_at_word: int | None = None  # barge in after this many words of the agent's turn
    say: str


class Facts(_Model):
    """What the caller says when the agent asks for it."""

    ids: tuple[str, ...]  # answers to "order number and PIN code?", used in order
    wish: str  # the day and time they want
    clarify: str | None = None  # the answer to a clarifying question
    reject: str = "Koi aur option hai?"  # when no offered slot fits
    repeat: str | None = None  # when the caller doesn't recognise what the agent said
    fallback_wish: str | None = None  # after turning an offer down, when another day is proposed


class CallerScript(_Model):
    opening: str
    facts: Facts
    goals: tuple[Goal, ...] = ()  # one per change, in order
    fallback_goal: Goal | None = None  # accepted when nothing fits the goal
    pick_style: Literal["roman", "devanagari", "english"] = "roman"
    triggers: tuple[Trigger, ...] = ()
    after_not_saved: tuple[str, ...] = ()  # said, in order, after hearing "not saved"
    after_outcome: tuple[str, ...] = ()  # said, in order, after hearing a confirmation
    close: str
    max_turns: int = 14


class Expected(_Model):
    final_slot: str  # a slot id, or "unchanged"
    writes: int  # committed writes this call should make
    clarify_before_write: bool = False
    # Any slot that fits passes, not only `final_slot`: an agent may offer a different subset
    # (for example "evening" read as after 5 PM), and the caller accepts any slot in its goal.
    final_fits: Goal | None = None


class Scenario(_Model):
    id: str
    category: Literal[
        "plain",
        "interruption",
        "code_mixed_time",
        "clarification",
        "unavailable",
        "fault",
        "change_of_mind",
        "bad_input",
    ]
    language: Language
    description: str
    split: Literal["dev", "heldout"] = "dev"
    now: datetime = DEFAULT_NOW
    world: WorldModel = WorldModel()
    faults: tuple[FaultModel, ...] = ()
    caller: CallerScript
    expected: Expected

    def fault_plan(self, mode: FaultMode) -> FaultPlan:
        """`scenario`: this scenario's faults. `on`: also a timeout after commit on the first
        reschedule, when the scenario has no reschedule fault of its own. `off`: none."""
        if mode == "off":
            return FaultPlan()
        rules = [fault.rule() for fault in self.faults]
        if mode == "on" and not any(rule.op == "reschedule" for rule in rules):
            rules.append(FaultRule(FaultKind.TIMEOUT_AFTER_COMMIT))
        return FaultPlan(tuple(rules))

    @property
    def expected_final_slot(self) -> str:
        if self.expected.final_slot == "unchanged":
            return self.world.order.slot
        return self.expected.final_slot


@dataclass(frozen=True)
class Corpus:
    version: str
    scenarios: tuple[Scenario, ...]
    sha256: str  # of every file, in name order: identifies the frozen corpus


def load_corpus(version: str) -> Corpus:
    folder = CORPUS_DIR / version
    digest = hashlib.sha256()
    scenarios: list[Scenario] = []
    for path in sorted(folder.glob("*.yaml")):
        data = path.read_bytes()
        digest.update(path.name.encode())
        digest.update(data)
        scenarios += [Scenario.model_validate(row) for row in yaml.safe_load(data.decode("utf-8"))]
    ids = [scenario.id for scenario in scenarios]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate scenario ids: {sorted(duplicates)}")
    return Corpus(version, tuple(scenarios), digest.hexdigest())
