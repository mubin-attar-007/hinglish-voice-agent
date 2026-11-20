"""Per-call fault injection for the order service."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

Operation = Literal["lookup_order", "list_slots", "reschedule", "get_order"]


class FaultKind(StrEnum):
    # reschedule only: the change is committed, then the caller times out waiting for the answer
    TIMEOUT_AFTER_COMMIT = "timeout_after_commit"
    # any operation: the request fails before the service does anything
    ERROR_BEFORE_COMMIT = "error_before_commit"
    # any operation: the answer takes `delay_ms`; past the caller's timeout, the caller gives up
    SLOW_RESPONSE = "slow_response"
    # reschedule only: another (synthetic) customer takes the last place in the slot first
    SLOT_TAKEN_MEANWHILE = "slot_taken_meanwhile"


_RESCHEDULE_ONLY = {FaultKind.TIMEOUT_AFTER_COMMIT, FaultKind.SLOT_TAKEN_MEANWHILE}


@dataclass(frozen=True)
class FaultRule:
    kind: FaultKind
    op: Operation = "reschedule"
    nth: int | None = 1  # which call of `op` in this call, counting from 1; None means every call
    delay_ms: int = 0  # slow_response only

    def __post_init__(self) -> None:
        if self.kind in _RESCHEDULE_ONLY and self.op != "reschedule":
            raise ValueError(f"{self.kind} applies to reschedule only, not {self.op}")
        if self.kind is FaultKind.SLOW_RESPONSE and self.delay_ms <= 0:
            raise ValueError("slow_response needs a positive delay_ms")
        if self.nth is not None and self.nth < 1:
            raise ValueError("nth counts from 1")

    def matches(self, op: str, n: int) -> bool:
        return self.op == op and (self.nth is None or self.nth == n)


@dataclass(frozen=True)
class FaultPlan:
    rules: tuple[FaultRule, ...] = ()

    def for_call(self, op: str, n: int) -> tuple[FaultRule, ...]:
        """The rules that fire on the `n`-th call of `op`."""
        return tuple(rule for rule in self.rules if rule.matches(op, n))
