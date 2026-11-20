"""What the gate knows about one call, fed only by the tool adapter's observations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from orders.models import OrderView, WriteStatus


class WriteState(StrEnum):
    NONE = "none"  # no change attempted in this call yet
    IN_FLIGHT = "in_flight"  # a reschedule request is out
    RESOLVED = "resolved"  # the service answered, or a later read settled the outcome
    UNKNOWN = "unknown"  # the request timed out, failed or was cancelled, and no read settled it


@dataclass
class GateState:
    order_id: str | None = None  # verified by lookup_order with the PIN code
    original_slot_id: str | None = None
    record: OrderView | None = None  # the last record read from the order system
    last_read_seq: int = 0
    write: WriteState = WriteState.NONE
    last_write_seq: int = 0
    intended_slot_id: str | None = None  # the slot the last reschedule asked for
    last_answer: WriteStatus | None = None  # the service's answer, when there was one

    @property
    def fresh(self) -> bool:
        """The record was read after the last write, and that write's outcome is settled."""
        return (
            self.record is not None
            and self.write is WriteState.RESOLVED
            and self.last_read_seq > self.last_write_seq
        )

    @property
    def change_saved(self) -> bool:
        return (
            self.record is not None
            and self.intended_slot_id is not None
            and self.record.slot.slot_id == self.intended_slot_id
        )
