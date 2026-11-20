"""Value types and errors of the illustrative order system (synthetic data)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from enum import StrEnum
from typing import Any


class WriteStatus(StrEnum):
    """The order service's verdict on one reschedule request."""

    COMMITTED = "committed"  # the change was written
    REPLAYED = "replayed"  # same idempotency key as an earlier request: its result, no new write
    NOOP = "noop"  # the order is already in that slot, so there is nothing to write
    CONFLICT = "conflict"  # the order's version is not the one the caller expected
    SLOT_FULL = "slot_full"
    SLOT_NOT_FOUND = "slot_not_found"
    ORDER_NOT_FOUND = "order_not_found"
    KEY_MISMATCH = "key_mismatch"  # an idempotency key reused for a different change
    DEADLINE_EXCEEDED = "deadline_exceeded"  # refused: the caller's deadline had passed


@dataclass(frozen=True)
class Slot:
    slot_id: str
    day: date
    start: time
    end: time
    capacity: int
    booked: int

    @property
    def available(self) -> bool:
        return self.booked < self.capacity


@dataclass(frozen=True)
class OrderView:
    """The system of record's view of one order."""

    order_id: str
    item: str
    slot: Slot
    version: int


@dataclass(frozen=True)
class WriteResult:
    status: WriteStatus
    order_id: str
    slot_id: str  # the slot that was requested
    order: OrderView | None  # the order as it stands after the request, if it exists
    commit_seq: int | None  # event sequence number of the commit this result refers to
    detail: str = ""

    @property
    def order_in_requested_slot(self) -> bool:
        """Whether the order sits in the requested slot now, judged from the current record.

        Not from the status alone: a REPLAYED result repeats an old answer, and the order may
        have moved since.
        """
        return self.order is not None and self.order.slot.slot_id == self.slot_id


@dataclass(frozen=True)
class Event:
    """One row of the append-only event log."""

    seq: int
    at_ms: int
    call_id: str
    order_id: str | None
    kind: str
    data: dict[str, Any]


class OrderServiceError(Exception):
    """The service failed before doing anything. Retrying is safe."""


class OrderServiceTimeout(Exception):
    """The caller stopped waiting, so it cannot know whether the change was committed."""


class OrderNotFound(Exception):
    """There is no order with that id."""
