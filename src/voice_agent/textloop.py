"""The text-mode agent: one caller turn in, the sentences the caller hears out.

Each model step streams text and tool calls. The text is split into sentences, and each one passes
through the confirmation gate before it counts as spoken. Tool calls go to the tool adapter. Three
rules keep outcome statements honest whatever the model does:

1. After a reschedule, code speaks the outcome rendered from the record, and the turn ends. The
   model's own words for that step are not spoken (`SafetyConfig.outcome_by_code`).
2. When the gate blocks a sentence because the record is not fresh, the agent reads the record
   and the record answers instead of the model.
3. When the gate blocks a premature claim, the agent says a holding line and the model is asked
   again, with a note that says why.

The model's context keeps what the caller heard, not what the model generated, as Pipecat does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from voice_agent.calllog import CallLog, Meaning, Spoken
from voice_agent.gate import templates
from voice_agent.gate.claims import ClaimKind
from voice_agent.gate.gate import Action, ConfirmationGate, Verdict
from voice_agent.llm.base import Done, LLMProvider, Message, TextDelta
from voice_agent.prompts import GATE_NOTES
from voice_agent.sentences import SentenceSplitter
from voice_agent.tools import SafetyConfig, ToolAdapter, ToolCall, ToolOutcome

OUTCOME_KINDS = (ClaimKind.CONFIRM, ClaimKind.FAIL)


@dataclass(frozen=True)
class _Step:
    sentences: list[tuple[str, Meaning | None]]
    calls: list[ToolCall]


class TextAgent:
    def __init__(
        self,
        llm: LLMProvider,
        adapter: ToolAdapter,
        gate: ConfirmationGate,
        *,
        system_prompt: str,
        safety: SafetyConfig,
        max_steps: int = 6,
        step_ms: int = 600,
    ) -> None:
        self.llm = llm
        self.adapter = adapter
        self.gate = gate
        self.system_prompt = system_prompt
        self.safety = safety
        self.max_steps = max_steps
        self.step_ms = step_ms  # simulated model latency on the virtual clock
        self.history: list[Message] = []
        self.log = CallLog()
        self.turn = 0
        self._pending_disclosure = False

    @property
    def _clock_ms(self) -> int:
        return self.adapter.service.clock.now_ms

    # ---- one caller turn -----------------------------------------------------------------------

    def respond(self, caller_text: str) -> list[Spoken]:
        self.turn += 1
        self.gate.start_turn(caller_text)
        self.history.append(Message("user", caller_text))
        first = len(self.log.spoken)

        if self._pending_disclosure:  # the caller cut off the outcome last turn: say it again
            self._pending_disclosure = False
            self._say_outcome("redisclose")

        note, blocks = "", 0
        for step_no in range(1, self.max_steps + 1):
            response_id = f"t{self.turn}s{step_no}"
            step = self._model_step(note, step_no)
            note = ""
            spoken_now: list[Spoken] = []
            blocked: Verdict | None = None
            # A response that makes a change says nothing of its own: code speaks the outcome
            # right after the write, so a claim the detector misses can't get out first.
            silent = self.safety.outcome_by_code and any(c.name == "reschedule" for c in step.calls)
            for text, meaning in step.sentences:
                if blocked is not None:
                    self.log.dropped.append({"turn": self.turn, "text": text, "why": "after_block"})
                    continue
                verdict = self.gate.check(text)
                if silent:  # not a gate decision: counted apart, as silenced
                    self.log.dropped.append(
                        {
                            "turn": self.turn,
                            "text": text,
                            "why": "same_response_as_write",
                            "claim": verdict.claim.kind.value,
                            "label": meaning.kind.value if meaning else None,
                        }
                    )
                    continue
                self._log_verdict(text, meaning, verdict)
                if verdict.action is Action.ALLOW:
                    spoken_now.append(self._say(text, "model", meaning, verdict))
                elif verdict.action is Action.REWRITE:
                    if self._just_said(verdict.text):  # two rewrites, one truth: say it once
                        self.log.dropped.append(
                            {"turn": self.turn, "text": text, "why": "same_rewrite_again"}
                        )
                        continue
                    rendered = Meaning(verdict.meaning or ClaimKind.NONE, verdict.meaning_slot_id)
                    spoken_now.append(self._say(verdict.text, "template", rendered, verdict))
                else:
                    blocked = verdict

            wrote = False
            if step.calls:
                self._remember(spoken_now, tool_calls=step.calls)
                for call in step.calls:
                    outcome = self.adapter.execute(call, response_id=response_id)
                    self._log_tool(outcome)
                    self.history.append(
                        Message(
                            "tool",
                            json.dumps(outcome.result, ensure_ascii=False),
                            tool_call_id=call.id,
                            name=call.name,
                        )
                    )
                    wrote = wrote or (call.name == "reschedule" and outcome.say is not None)
            elif spoken_now:
                self._remember(spoken_now)

            if wrote and self.safety.outcome_by_code:
                self._say_outcome("outcome_by_code")
                break
            if blocked is not None:
                blocks += 1
                if blocked.needs_read:
                    self.adapter.refresh()
                    self._say_outcome("answered_from_record")
                    break
                if blocks == 1:
                    holding = templates.holding(self.gate.language)
                    self._say(holding, "holding", Meaning(ClaimKind.UNCERTAIN), blocked)
                    self._remember(self.log.spoken[-1:])
                if blocks >= 2:
                    break
                # A note only if nothing changed since the block: tools that ran in this step
                # may already have made it stale ("no order looked up yet" after a lookup).
                note = "" if step.calls else GATE_NOTES.get(blocked.rule, "")
                continue
            if not step.calls:
                break
        return self.log.spoken[first:]

    def barge_in(self, after_words: int) -> None:
        """The caller started speaking after `after_words` words of this turn's reply."""
        remaining = after_words
        touched: set[int] = set()
        for spoken in (s for s in self.log.spoken if s.turn == self.turn):
            words = spoken.text.split()
            if remaining >= len(words):
                remaining -= len(words)
                continue
            if remaining > 0:
                spoken.heard_text, spoken.cut = " ".join(words[:remaining]), True
                remaining = 0
            else:
                spoken.heard_text, spoken.heard = "", False
            touched.add(spoken.history_index)
            if (
                spoken.source == "template"
                and spoken.meaning
                and spoken.meaning.kind in OUTCOME_KINDS
            ):
                self._pending_disclosure = True
        for index in touched:  # the model's context keeps only what the caller heard
            if 0 <= index < len(self.history):
                heard = " ".join(
                    s.heard_text for s in self.log.spoken if s.history_index == index and s.heard
                )
                old = self.history[index]
                self.history[index] = Message(old.role, heard, tool_calls=old.tool_calls)

    # ---- internals -----------------------------------------------------------------------------

    def _model_step(self, note: str, step_no: int) -> _Step:
        splitter = SentenceSplitter()
        sentences: list[tuple[str, Meaning | None]] = []
        calls: list[ToolCall] = []
        label: Meaning | None = None
        done: Done | None = None
        for event in self.llm.stream(self.system_prompt + note, self.history, self.adapter.specs):
            if isinstance(event, TextDelta):
                label = event.meaning or label
                sentences += [(sentence, label) for sentence in splitter.feed(event.text)]
                if not splitter.pending:
                    label = None  # a label covers the sentences of its own delta
            elif isinstance(event, ToolCall):
                calls.append(event)
            else:
                done = event
        sentences += [(sentence, label) for sentence in splitter.flush()]
        self.adapter.service.clock.advance(self.step_ms)
        self.log.steps.append(
            {
                "turn": self.turn,
                "step": step_no,
                "model": done.model if done else self.llm.name,
                "input_tokens": done.usage.input_tokens if done else 0,
                "output_tokens": done.usage.output_tokens if done else 0,
                "latency_ms": done.latency_ms if done else 0.0,
            }
        )
        return _Step(sentences, calls)

    def _say(
        self,
        text: str,
        source: str,
        meaning: Meaning | None,
        verdict: Verdict | None = None,
        rule: str | None = None,
    ) -> Spoken:
        spoken = Spoken(
            turn=self.turn,
            text=text,
            source=source,  # type: ignore[arg-type]
            meaning=meaning,
            seq=self.adapter.service.last_seq(),
            at_ms=self._clock_ms,
            rule=rule or (verdict.rule if verdict else None),
            agreed=verdict.agreed if verdict else None,
        )
        self.log.spoken.append(spoken)
        return spoken

    def _just_said(self, text: str) -> bool:
        this_turn = [s for s in self.log.spoken if s.turn == self.turn]
        return bool(this_turn) and this_turn[-1].text == text

    def _say_outcome(self, rule: str) -> None:
        outcome = self.gate.outcome_sentence()
        if outcome is not None:
            self._say(
                outcome.text, "template", Meaning(outcome.meaning, outcome.slot_id), rule=rule
            )
        else:
            sorry = templates.cannot_verify(self.gate.language)
            self._say(sorry, "template", Meaning(ClaimKind.UNCERTAIN), rule=rule)
        self._remember(self.log.spoken[-1:])

    def _remember(self, spoken: list[Spoken], tool_calls: list[ToolCall] | None = None) -> None:
        index = len(self.history)
        for item in spoken:
            item.history_index = index
        text = " ".join(item.text for item in spoken)
        self.history.append(Message("assistant", text, tool_calls=tuple(tool_calls or ())))

    def _log_verdict(self, text: str, meaning: Meaning | None, verdict: Verdict) -> None:
        self.log.verdicts.append(
            {
                "turn": self.turn,
                "text": text,
                "action": verdict.action.value,
                "rule": verdict.rule,
                "claim": verdict.claim.kind.value,
                "agreed": verdict.agreed,
                "enforced": verdict.enforced,
                "would": verdict.would.value if verdict.would else None,
                "label": meaning.kind.value if meaning else None,
                "seq": self.adapter.service.last_seq(),
            }
        )

    def _log_tool(self, outcome: ToolOutcome) -> None:
        self.log.tools.append(
            {
                "turn": self.turn,
                "name": outcome.call.name,
                "arguments": outcome.call.arguments,
                "result": outcome.result,
                "seq_before": outcome.seq_before,
                "seq_after": outcome.seq_after,
                "duplicate_call": outcome.duplicate_call,
            }
        )
