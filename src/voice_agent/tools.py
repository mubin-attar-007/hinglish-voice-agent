"""The tools the model can call, and the adapter that runs them against the order service.

The adapter, not the model, owns everything that makes a change safe:

- the idempotency key, derived from (call id, order id, slot id, order version);
- the expected version (compare-and-set) and the commit deadline;
- the re-read of the record after every write attempt, whatever happened to it;
- the preconditions: the order was verified, the slot came from list_slots, one write at a time.

The model sees only `reschedule(order_id, slot_id)`. Each safety feature can be switched off
through `SafetyConfig`, which is how the no-safety baseline and the ablations are run.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, fields, replace
from datetime import date, time
from typing import Any

from orders.models import OrderNotFound, OrderServiceError, OrderServiceTimeout, OrderView, Slot
from orders.service import DEFAULT_CLIENT_TIMEOUT_MS, OrderService
from voice_agent.gate import templates
from voice_agent.gate.claims import ClaimKind
from voice_agent.gate.gate import ConfirmationGate
from voice_agent.gate.state import WriteState

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
READ_BACKOFF_MS = 300


@dataclass(frozen=True)
class SafetyConfig:
    idempotency: bool = True  # derived keys, and no write when the order is already in the slot
    cas: bool = True  # expected version on every write
    deadline: bool = True  # the service refuses to commit after the caller's deadline
    reread: bool = True  # read the record after every write attempt
    preconditions: bool = True  # verified order, offered slot, one write per response
    gate: bool = True  # enforce the gate; off means observe only
    outcome_by_code: bool = True  # after a write, code speaks the outcome; the model does not

    @classmethod
    def baseline(cls) -> SafetyConfig:
        return cls(**{f.name: False for f in fields(cls)})

    @property
    def label(self) -> str:
        off = [f.name for f in fields(self) if not getattr(self, f.name)]
        if not off:
            return "full"
        if len(off) == len(fields(self)):
            return "baseline"
        return "without-" + "-".join(off)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolOutcome:
    call: ToolCall
    result: dict[str, Any]  # what the model sees
    seq_before: int
    seq_after: int
    say: str | None = None  # the outcome sentence rendered from the record, for reschedule
    duplicate_call: bool = False  # a repeated tool-call id: the cached outcome, nothing re-run


def _string(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "lookup_order",
        "Find the caller's order, using the order id and the delivery PIN code the caller gives. "
        "Returns the current delivery slot. Call this before anything else.",
        {
            "type": "object",
            "properties": {
                "order_id": _string("The order id, digits only, for example 48291."),
                "pincode": _string("The 6-digit delivery PIN code."),
            },
            "required": ["order_id", "pincode"],
        },
    ),
    ToolSpec(
        "list_slots",
        "List delivery slots on one date. Optionally only slots that start at or after "
        "after_time, or end at or before before_time. Each slot says whether it is available.",
        {
            "type": "object",
            "properties": {
                "date": _string("The date, as YYYY-MM-DD."),
                "after_time": _string("Optional. 24-hour HH:MM, for example 16:00."),
                "before_time": _string("Optional. 24-hour HH:MM, for example 18:00."),
            },
            "required": ["date"],
        },
    ),
    ToolSpec(
        "reschedule",
        "Move the verified order to an available slot returned by list_slots, once the caller "
        "has agreed to it. The result says whether the order system shows the change; tell the "
        "caller its say_this sentence, word for word.",
        {
            "type": "object",
            "properties": {
                "order_id": _string("The verified order id."),
                "slot_id": _string("A slot_id returned by list_slots in this call."),
            },
            "required": ["order_id", "slot_id"],
        },
    ),
    ToolSpec(
        "get_order",
        "Read the order's current delivery slot from the order system.",
        {
            "type": "object",
            "properties": {"order_id": _string("The verified order id.")},
            "required": ["order_id"],
        },
    ),
)


def derive_key(call_id: str, order_id: str, slot_id: str, base_version: int | None) -> str:
    """The idempotency key: a retry of the same change reuses it, a new change gets a new one."""
    material = f"{call_id}|{order_id}|{slot_id}|{base_version}".encode()
    return hashlib.sha256(material).hexdigest()[:24]


def slot_json(slot: Slot) -> dict[str, Any]:
    return {
        "slot_id": slot.slot_id,
        "date": slot.day.isoformat(),
        "weekday": _WEEKDAYS[slot.day.weekday()],
        "window": f"{slot.start:%H:%M}-{slot.end:%H:%M}",
        "available": slot.available,
    }


def order_json(view: OrderView) -> dict[str, Any]:
    return {"order_id": view.order_id, "item": view.item, "delivery": slot_json(view.slot)}


class ToolAdapter:
    def __init__(
        self,
        service: OrderService,
        gate: ConfirmationGate,
        *,
        call_id: str,
        safety: SafetyConfig | None = None,
        client_timeout_ms: int = DEFAULT_CLIENT_TIMEOUT_MS,
        read_attempts: int = 3,
    ) -> None:
        self.service = service
        self.gate = gate
        self.call_id = call_id
        self.safety = safety or SafetyConfig()
        self.client_timeout_ms = client_timeout_ms
        self.read_attempts = read_attempts
        self.verified_order_id: str | None = None
        self._offered: set[str] = set()
        self._seen: dict[tuple[str, str], ToolOutcome] = {}
        self._writes_per_response: Counter[str] = Counter()

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return TOOL_SPECS

    def execute(self, call: ToolCall, *, response_id: str) -> ToolOutcome:
        """Run one tool call from the model response `response_id`.

        A repeated (response, call id) pair is a provider retry: its cached outcome is returned
        and nothing runs again. Ids are scoped to the response, because some providers reuse
        them across responses.
        """
        seen_key = (response_id, call.id)
        if call.id and seen_key in self._seen:
            return replace(self._seen[seen_key], duplicate_call=True)
        before = self.service.last_seq()
        say: str | None = None
        try:
            if call.name == "lookup_order":
                result = self._lookup(call.arguments)
            elif call.name == "list_slots":
                result = self._list(call.arguments)
            elif call.name == "get_order":
                result = self._get(call.arguments)
            elif call.name == "reschedule":
                result, say = self._reschedule(call.arguments, response_id)
            else:
                result = {"error": f"unknown tool: {call.name}"}
        except (KeyError, TypeError, ValueError) as exc:
            result = {"error": f"bad arguments for {call.name}: {exc}"}
        outcome = ToolOutcome(call, result, before, self.service.last_seq(), say)
        if call.id:
            self._seen[seen_key] = outcome
        return outcome

    # ---- the tools ---------------------------------------------------------------------------

    def _lookup(self, args: dict[str, Any]) -> dict[str, Any]:
        order_id, pincode = str(args["order_id"]).strip(), str(args["pincode"]).strip()
        try:
            view = self.service.lookup_order(
                order_id, pincode, client_timeout_ms=self.client_timeout_ms
            )
        except (OrderServiceTimeout, OrderServiceError):
            return {"error": "the order system did not answer; try again"}
        self.gate.observe_lookup(view, self.service.last_seq())
        if view is None:
            return {"found": False, "note": "No order matches that order id and PIN code."}
        self.verified_order_id = view.order_id
        return {"found": True, "order": order_json(view)}

    def _list(self, args: dict[str, Any]) -> dict[str, Any]:
        day = date.fromisoformat(str(args["date"]))
        after = _parse_time(args.get("after_time"))
        before = _parse_time(args.get("before_time"))
        try:
            slots = self.service.list_slots(
                day, after=after, before=before, client_timeout_ms=self.client_timeout_ms
            )
        except (OrderServiceTimeout, OrderServiceError):
            return {"error": "the order system did not answer; try again"}
        self._offered.update(slot.slot_id for slot in slots if slot.available)
        return {"date": day.isoformat(), "slots": [slot_json(slot) for slot in slots]}

    def _get(self, args: dict[str, Any]) -> dict[str, Any]:
        order_id = str(args["order_id"]).strip()
        if self.safety.preconditions and order_id != self.verified_order_id:
            return {"error": "verify the order with lookup_order first"}
        view = self._read(order_id)
        if view is None:
            return {"error": "the order could not be read: no such order, or no answer"}
        return {"order": order_json(view)}

    def _reschedule(
        self, args: dict[str, Any], response_id: str
    ) -> tuple[dict[str, Any], str | None]:
        order_id, slot_id = str(args["order_id"]).strip(), str(args["slot_id"]).strip()
        problem = self._precondition_problem(order_id, slot_id, response_id)
        if problem:
            return {"error": problem}, None
        self._writes_per_response[response_id] += 1

        record = self.gate.state.record
        base_version = record.version if record is not None else None
        safety = self.safety
        self.gate.observe_write_started(slot_id, self.service.last_seq())
        try:
            answer = self.service.reschedule(
                order_id,
                slot_id,
                idem_key=derive_key(self.call_id, order_id, slot_id, base_version)
                if safety.idempotency
                else None,
                expected_version=base_version if safety.cas else None,
                deadline_ms=self.service.clock.now_ms + self.client_timeout_ms
                if safety.deadline
                else None,
                noop_if_unchanged=safety.idempotency,
                client_timeout_ms=self.client_timeout_ms,
            )
        except OrderServiceTimeout:
            self.gate.observe_write_lost()
            raw: dict[str, Any] = {"error": "timeout: the order system did not answer in time"}
        except OrderServiceError as exc:
            self.gate.observe_write_lost()
            raw = {"error": f"the order system failed: {exc}"}
        else:
            self.gate.observe_write_answer(answer)
            raw = {"status": answer.status.value}
            if answer.order is not None:
                raw["delivery"] = slot_json(answer.order.slot)

        if not safety.reread:
            return raw, None
        view = self._read(order_id)
        outcome = self.gate.outcome_sentence()
        if view is None or outcome is None:
            sorry = templates.cannot_verify(self.gate.language)
            return {
                "status": "unknown",
                "verified_from_order_system": False,
                "note": "The order system could not be read. Do not tell the caller whether "
                "the change worked.",
                "say_this": sorry,
            }, sorry
        saved = outcome.meaning is ClaimKind.CONFIRM
        result: dict[str, Any] = {
            "status": "saved" if saved else "not_saved",
            "verified_from_order_system": True,
            "delivery": slot_json(view.slot),
            "say_this": outcome.text,
        }
        if not saved and self.gate.state.last_answer is not None:
            result["reason"] = self.gate.state.last_answer.value
        return result, outcome.text

    # ---- helpers -----------------------------------------------------------------------------

    def refresh(self) -> OrderView | None:
        """Read the verified order's record now: the gate asks for this before it answers."""
        if self.verified_order_id is None:
            return None
        return self._read(self.verified_order_id)

    def _precondition_problem(self, order_id: str, slot_id: str, response_id: str) -> str | None:
        if not self.safety.preconditions:
            return None
        if self.verified_order_id is None or order_id != self.verified_order_id:
            return "verify the order with lookup_order first"
        if slot_id not in self._offered:
            return "use an available slot_id returned by list_slots in this call"
        if self._writes_per_response[response_id] >= 1:
            return "one change at a time: wait for the result of the last change"
        if self.gate.state.write is WriteState.UNKNOWN:
            self._read(order_id)
            if self.gate.state.write is WriteState.UNKNOWN:
                return "the outcome of the last change is still unknown; do not change it again"
        return None

    def _read(self, order_id: str) -> OrderView | None:
        """Read the record, retrying a few times; tell the gate what was read."""
        for attempt in range(self.read_attempts):
            try:
                view = self.service.get_order(order_id, client_timeout_ms=self.client_timeout_ms)
            except OrderNotFound:
                return None
            except (OrderServiceTimeout, OrderServiceError):
                if attempt + 1 < self.read_attempts:
                    self.service.clock.advance(READ_BACKOFF_MS)
                continue
            self.gate.observe_read(view, self.service.last_seq())
            return view
        return None


def _parse_time(value: object) -> time | None:
    if value in (None, ""):
        return None
    return time.fromisoformat(str(value))
