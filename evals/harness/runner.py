"""Run one scenario, or a whole corpus, against an agent: a trial is one simulated call."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from evals.harness.caller import ScriptedCaller
from evals.schema import FaultMode, Scenario
from orders.clock import VirtualClock
from orders.db import connect, seed
from orders.models import Event
from orders.service import OrderService
from voice_agent.calllog import CallLog, Spoken
from voice_agent.gate.gate import ConfirmationGate
from voice_agent.llm.base import LLMProvider
from voice_agent.llm.mock import MockAgentLLM
from voice_agent.llm.pricing import CostMeter
from voice_agent.prompts import system_prompt
from voice_agent.textloop import TextAgent
from voice_agent.tools import SafetyConfig, ToolAdapter

CALLER_TURN_MS = 3000  # simulated time the caller takes to speak a turn

LLMFactory = Callable[[Scenario, int], LLMProvider]


def mock_factory(profile: str) -> LLMFactory:
    def make(scenario: Scenario, seed_: int) -> LLMProvider:
        return MockAgentLLM(
            now=scenario.now, language=scenario.language, profile=profile, seed=seed_
        )

    return make


def gemini_factory(
    model: str, meter: CostMeter | None = None, thinking: str | None = "low"
) -> LLMFactory:
    """Real Gemini calls. One shared meter counts every request and enforces the cost cap."""
    from voice_agent.llm.gemini import GeminiLLM

    def make(scenario: Scenario, seed_: int) -> LLMProvider:
        return GeminiLLM(model=model, seed=seed_, meter=meter, thinking=thinking)

    return make


@dataclass
class TrialRecord:
    scenario: Scenario
    seed: int
    model: str
    safety: SafetyConfig
    faults: FaultMode
    spoken: list[Spoken]
    events: list[Event]
    caller_log: list[dict[str, Any]]
    log: CallLog
    stuck: bool
    original_slot: str
    final_slot: str | None
    turns: int
    extra: dict[str, Any] = field(default_factory=dict)


def run_trial(
    scenario: Scenario,
    factory: LLMFactory,
    *,
    safety: SafetyConfig,
    faults: FaultMode,
    seed_: int,
) -> TrialRecord:
    conn = connect()
    seed(conn, scenario.world.spec())
    clock = VirtualClock(scenario.now)
    call_id = f"{scenario.id}#{seed_}"
    service = OrderService(conn, clock=clock, faults=scenario.fault_plan(faults), call_id=call_id)
    gate = ConfirmationGate(language=scenario.language, now=clock.now, enforce=safety.gate)
    adapter = ToolAdapter(service, gate, call_id=call_id, safety=safety)
    llm = factory(scenario, seed_)
    agent = TextAgent(
        llm,
        adapter,
        gate,
        system_prompt=system_prompt(scenario.now, scenario.language),
        safety=safety,
    )
    caller = ScriptedCaller(scenario.caller, now=scenario.now)

    line: str | None = caller.opening()
    while line is not None:
        clock.advance(CALLER_TURN_MS)
        spoken = agent.respond(line)
        listed = [
            slot
            for call in agent.log.tools
            if call["turn"] == agent.turn and call["name"] == "list_slots"
            for slot in call["result"].get("slots", [])
        ]
        point = caller.barge_in_point(spoken, listed)  # decided while the agent speaks
        if point is not None:
            agent.barge_in(point)
        action = caller.hear(spoken, service.last_seq())  # reacts only to what was heard
        line = None if action.ended else action.say

    clock.advance(CALLER_TURN_MS * 3)  # let any write still in flight land before grading
    final = service.peek_order(scenario.world.order.order_id)
    return TrialRecord(
        scenario=scenario,
        seed=seed_,
        model=llm.name,
        safety=safety,
        faults=faults,
        spoken=agent.log.spoken,
        events=service.events(),
        caller_log=caller.log,
        log=agent.log,
        stuck=caller.stuck,
        original_slot=scenario.world.order.slot,
        final_slot=final.slot.slot_id if final else None,
        turns=agent.turn,
    )


def run_corpus(
    scenarios: Sequence[Scenario],
    factory: LLMFactory,
    *,
    safety: SafetyConfig,
    faults: FaultMode,
    seeds: Sequence[int],
) -> list[TrialRecord]:
    return [
        run_trial(scenario, factory, safety=safety, faults=faults, seed_=s)
        for scenario in scenarios
        for s in seeds
    ]
