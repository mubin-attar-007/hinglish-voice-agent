"""The order service: the system of record of the illustrative order system.

One service instance serves one call. It has its own in-memory database, virtual clock and fault
plan. All data is synthetic.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, time
from typing import Any

from orders.clock import VirtualClock
from orders.db import fill_slot
from orders.faults import FaultKind, FaultPlan, FaultRule
from orders.models import (
    Event,
    OrderNotFound,
    OrderServiceError,
    OrderServiceTimeout,
    OrderView,
    Slot,
    WriteResult,
    WriteStatus,
)

DEFAULT_CLIENT_TIMEOUT_MS = 2500

_SLOT_COLUMNS = (
    "s.slot_id, s.day, s.start, s.end, s.capacity, "
    "(SELECT COUNT(*) FROM orders x WHERE x.slot_id = s.slot_id) AS booked"
)


@dataclass(frozen=True)
class _PendingWrite:
    """A write still in flight after its caller gave up. Only possible without a deadline."""

    due_ms: int
    order_id: str
    slot_id: str
    idem_key: str | None
    expected_version: int | None
    deadline_ms: int | None
    noop_if_unchanged: bool


class OrderService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        clock: VirtualClock,
        faults: FaultPlan | None = None,
        call_id: str = "call",
    ) -> None:
        self._conn = conn
        self.clock = clock
        self.faults = faults or FaultPlan()
        self.call_id = call_id
        self._calls: Counter[str] = Counter()
        self._pending: list[_PendingWrite] = []

    # ---- reads -------------------------------------------------------------------------------

    def lookup_order(
        self, order_id: str, pincode: str, *, client_timeout_ms: int = DEFAULT_CLIENT_TIMEOUT_MS
    ) -> OrderView | None:
        """The order, if the id exists and the PIN code matches. Never says which check failed."""
        self._read_faults(self._begin("lookup_order"), client_timeout_ms, None)
        row = self._conn.execute(
            "SELECT pincode FROM orders WHERE order_id = ? AND other_customer = 0", (order_id,)
        ).fetchone()
        found = row is not None and row["pincode"] == pincode.strip()
        self._log("order_looked_up", order_id if found else None, {"found": found})
        return self._view(order_id) if found else None

    def get_order(
        self, order_id: str, *, client_timeout_ms: int = DEFAULT_CLIENT_TIMEOUT_MS
    ) -> OrderView:
        """Read the order from the system of record."""
        self._read_faults(self._begin("get_order"), client_timeout_ms, order_id)
        view = self._view(order_id)
        if view is None:
            raise OrderNotFound(order_id)
        self._log("order_read", order_id, {"slot_id": view.slot.slot_id, "version": view.version})
        return view

    def list_slots(
        self,
        day: date,
        *,
        after: time | None = None,
        before: time | None = None,
        client_timeout_ms: int = DEFAULT_CLIENT_TIMEOUT_MS,
    ) -> list[Slot]:
        """Slots on `day` that start at or after `after` and end at or before `before`."""
        self._read_faults(self._begin("list_slots"), client_timeout_ms, None)
        slots = [
            slot
            for slot in self._slots_on(day)
            if (after is None or slot.start >= after) and (before is None or slot.end <= before)
        ]
        self._log(
            "slots_listed",
            None,
            {
                "day": day.isoformat(),
                "after": after.strftime("%H:%M") if after else None,
                "before": before.strftime("%H:%M") if before else None,
                "available": [slot.slot_id for slot in slots if slot.available],
            },
        )
        return slots

    def peek_order(self, order_id: str) -> OrderView | None:
        """The current record, for the harness and graders only.

        It is not an agent operation, so it fires no faults and logs no read. It does let due
        late writes land first, so that the snapshot matches the current time.
        """
        self.tick()
        return self._view(order_id)

    # ---- the write ---------------------------------------------------------------------------

    def reschedule(
        self,
        order_id: str,
        slot_id: str,
        *,
        idem_key: str | None = None,
        expected_version: int | None = None,
        deadline_ms: int | None = None,
        noop_if_unchanged: bool = True,
        client_timeout_ms: int = DEFAULT_CLIENT_TIMEOUT_MS,
    ) -> WriteResult:
        """Move an order to another slot.

        Each safety feature is opt-in per request, so that a baseline can switch it off:

        - `idem_key` replays the stored result of an earlier request with the same key;
        - `expected_version` rejects the write if the order changed since the caller read it;
        - `deadline_ms` refuses to commit after the caller's deadline;
        - `noop_if_unchanged` writes nothing when the order is already in the slot.

        Raises OrderServiceError when nothing was done, or OrderServiceTimeout when the caller
        gave up waiting and so cannot know the outcome.
        """
        rules = self._begin("reschedule")
        kinds = {rule.kind for rule in rules}
        start = self.clock.now_ms
        self._log(
            "reschedule_requested",
            order_id,
            {
                "slot_id": slot_id,
                "key": idem_key,
                "expected_version": expected_version,
                "deadline_ms": deadline_ms,
            },
        )
        if FaultKind.ERROR_BEFORE_COMMIT in kinds:
            self._log("fault_injected", order_id, {"fault": FaultKind.ERROR_BEFORE_COMMIT})
            raise OrderServiceError(
                "injected fault: the request failed before anything was written"
            )
        if FaultKind.SLOT_TAKEN_MEANWHILE in kinds and fill_slot(self._conn, slot_id):
            self._log("slot_taken_by_other", None, {"slot_id": slot_id})

        delay = sum(rule.delay_ms for rule in rules if rule.kind is FaultKind.SLOW_RESPONSE)
        if delay:
            self._log(
                "fault_injected", order_id, {"fault": FaultKind.SLOW_RESPONSE, "delay_ms": delay}
            )
        if delay > client_timeout_ms:
            if deadline_ms is not None and start + delay > deadline_ms:
                self.clock.advance_to(start + client_timeout_ms)
                self._log(
                    "write_refused_after_deadline",
                    order_id,
                    {"slot_id": slot_id, "deadline_ms": deadline_ms},
                )
            else:
                self._pending.append(
                    _PendingWrite(
                        due_ms=start + delay,
                        order_id=order_id,
                        slot_id=slot_id,
                        idem_key=idem_key,
                        expected_version=expected_version,
                        deadline_ms=deadline_ms,
                        noop_if_unchanged=noop_if_unchanged,
                    )
                )
                self.clock.advance_to(start + client_timeout_ms)
            raise OrderServiceTimeout("the order service did not answer in time")

        self.clock.advance(delay)
        result = self._write(
            order_id,
            slot_id,
            idem_key=idem_key,
            expected_version=expected_version,
            deadline_ms=deadline_ms,
            noop_if_unchanged=noop_if_unchanged,
        )
        if FaultKind.TIMEOUT_AFTER_COMMIT in kinds:
            self.clock.advance_to(start + client_timeout_ms)
            self._log("fault_injected", order_id, {"fault": FaultKind.TIMEOUT_AFTER_COMMIT})
            raise OrderServiceTimeout("the order service did not answer in time")
        return result

    def tick(self) -> None:
        """Apply writes whose time has come: late writes that outlived their caller."""
        now = self.clock.now_ms
        due = sorted((p for p in self._pending if p.due_ms <= now), key=lambda p: p.due_ms)
        if not due:
            return
        self._pending = [p for p in self._pending if p.due_ms > now]
        for pending in due:
            self._write(
                pending.order_id,
                pending.slot_id,
                idem_key=pending.idem_key,
                expected_version=pending.expected_version,
                deadline_ms=pending.deadline_ms,
                noop_if_unchanged=pending.noop_if_unchanged,
                late=True,
                at_ms=pending.due_ms,
            )

    @property
    def writes_in_flight(self) -> int:
        return len(self._pending)

    # ---- the event log -----------------------------------------------------------------------

    def events(self, since_seq: int = 0) -> list[Event]:
        rows = self._conn.execute(
            "SELECT seq, at_ms, call_id, order_id, kind, data FROM events "
            "WHERE seq > ? ORDER BY seq",
            (since_seq,),
        ).fetchall()
        return [
            Event(
                seq=row["seq"],
                at_ms=row["at_ms"],
                call_id=row["call_id"],
                order_id=row["order_id"],
                kind=row["kind"],
                data=json.loads(row["data"]),
            )
            for row in rows
        ]

    def last_seq(self) -> int:
        return int(self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0])

    # ---- internals ---------------------------------------------------------------------------

    def _write(
        self,
        order_id: str,
        slot_id: str,
        *,
        idem_key: str | None,
        expected_version: int | None,
        deadline_ms: int | None,
        noop_if_unchanged: bool,
        late: bool = False,
        at_ms: int | None = None,
    ) -> WriteResult:
        at = self.clock.now_ms if at_ms is None else at_ms
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            result = self._decide(
                order_id,
                slot_id,
                idem_key,
                expected_version,
                deadline_ms,
                noop_if_unchanged,
                late,
                at,
            )
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")
        return result

    def _decide(
        self,
        order_id: str,
        slot_id: str,
        idem_key: str | None,
        expected_version: int | None,
        deadline_ms: int | None,
        noop_if_unchanged: bool,
        late: bool,
        at: int,
    ) -> WriteResult:
        conn = self._conn
        if idem_key is not None:
            stored = conn.execute(
                "SELECT order_id, slot_id, result FROM idempotency WHERE key = ?", (idem_key,)
            ).fetchone()
            if stored is not None:
                if (stored["order_id"], stored["slot_id"]) != (order_id, slot_id):
                    return self._reject(
                        WriteStatus.KEY_MISMATCH,
                        order_id,
                        slot_id,
                        at,
                        "key used for another change",
                    )
                original = json.loads(stored["result"])
                self._log(
                    "reschedule_replayed",
                    order_id,
                    {
                        "key": idem_key,
                        "original_status": original["status"],
                        "original_seq": original["commit_seq"],
                    },
                    at_ms=at,
                )
                return WriteResult(
                    WriteStatus.REPLAYED,
                    order_id,
                    slot_id,
                    self._view(order_id),
                    original["commit_seq"],
                    detail=f"original result: {original['status']}",
                )

        current = conn.execute(
            "SELECT slot_id, version FROM orders WHERE order_id = ? AND other_customer = 0",
            (order_id,),
        ).fetchone()
        if current is None:
            return self._reject(WriteStatus.ORDER_NOT_FOUND, order_id, slot_id, at)
        if deadline_ms is not None and at > deadline_ms:
            return self._reject(WriteStatus.DEADLINE_EXCEEDED, order_id, slot_id, at)
        if expected_version is not None and current["version"] != expected_version:
            return self._reject(
                WriteStatus.CONFLICT,
                order_id,
                slot_id,
                at,
                f"expected version {expected_version}, found {current['version']}",
            )
        if noop_if_unchanged and current["slot_id"] == slot_id:
            self._log("reschedule_noop", order_id, {"slot_id": slot_id, "key": idem_key}, at_ms=at)
            result = WriteResult(WriteStatus.NOOP, order_id, slot_id, self._view(order_id), None)
            self._remember(idem_key, result)
            return result

        slot = conn.execute("SELECT capacity FROM slots WHERE slot_id = ?", (slot_id,)).fetchone()
        if slot is None:
            return self._reject(WriteStatus.SLOT_NOT_FOUND, order_id, slot_id, at)
        others = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE slot_id = ? AND order_id != ?", (slot_id, order_id)
        ).fetchone()[0]
        if others >= slot["capacity"]:
            return self._reject(WriteStatus.SLOT_FULL, order_id, slot_id, at)

        conn.execute(
            "UPDATE orders SET slot_id = ?, version = version + 1 WHERE order_id = ?",
            (slot_id, order_id),
        )
        seq = self._log(
            "reschedule_committed",
            order_id,
            {
                "from_slot": current["slot_id"],
                "to_slot": slot_id,
                "version": current["version"] + 1,
                "key": idem_key,
                "late": late,
            },
            at_ms=at,
        )
        result = WriteResult(WriteStatus.COMMITTED, order_id, slot_id, self._view(order_id), seq)
        self._remember(idem_key, result)
        return result

    def _reject(
        self, status: WriteStatus, order_id: str, slot_id: str, at: int, detail: str = ""
    ) -> WriteResult:
        self._log(
            "reschedule_rejected",
            order_id,
            {"slot_id": slot_id, "reason": status, "detail": detail},
            at_ms=at,
        )
        return WriteResult(status, order_id, slot_id, self._view(order_id), None, detail)

    def _remember(self, idem_key: str | None, result: WriteResult) -> None:
        if idem_key is None:
            return
        self._conn.execute(
            "INSERT INTO idempotency (key, order_id, slot_id, result) VALUES (?, ?, ?, ?)",
            (
                idem_key,
                result.order_id,
                result.slot_id,
                json.dumps({"status": result.status, "commit_seq": result.commit_seq}),
            ),
        )

    def _begin(self, op: str) -> tuple[FaultRule, ...]:
        self.tick()
        self._calls[op] += 1
        return self.faults.for_call(op, self._calls[op])

    def _read_faults(
        self, rules: tuple[FaultRule, ...], client_timeout_ms: int, order_id: str | None
    ) -> None:
        for rule in rules:
            if rule.kind is FaultKind.ERROR_BEFORE_COMMIT:
                self._log("fault_injected", order_id, {"fault": rule.kind, "op": rule.op})
                raise OrderServiceError(f"injected fault: {rule.op} failed")
            if rule.kind is FaultKind.SLOW_RESPONSE:
                self._log(
                    "fault_injected",
                    order_id,
                    {"fault": rule.kind, "op": rule.op, "delay_ms": rule.delay_ms},
                )
                if rule.delay_ms > client_timeout_ms:
                    self.clock.advance(client_timeout_ms)
                    raise OrderServiceTimeout(f"{rule.op} did not answer in time")
                self.clock.advance(rule.delay_ms)

    def _log(
        self, kind: str, order_id: str | None, data: dict[str, Any], *, at_ms: int | None = None
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO events (at_ms, call_id, order_id, kind, data) VALUES (?, ?, ?, ?, ?)",
            (
                self.clock.now_ms if at_ms is None else at_ms,
                self.call_id,
                order_id,
                kind,
                json.dumps(data, ensure_ascii=False),
            ),
        )
        return int(cursor.lastrowid or 0)

    def _view(self, order_id: str) -> OrderView | None:
        row = self._conn.execute(
            f"SELECT o.order_id, o.item, o.version, {_SLOT_COLUMNS} "
            "FROM orders o JOIN slots s ON s.slot_id = o.slot_id "
            "WHERE o.order_id = ? AND o.other_customer = 0",
            (order_id,),
        ).fetchone()
        if row is None:
            return None
        return OrderView(row["order_id"], row["item"], _slot(row), row["version"])

    def _slots_on(self, day: date) -> list[Slot]:
        rows = self._conn.execute(
            f"SELECT {_SLOT_COLUMNS} FROM slots s WHERE s.day = ? ORDER BY s.start",
            (day.isoformat(),),
        ).fetchall()
        return [_slot(row) for row in rows]


def _slot(row: sqlite3.Row) -> Slot:
    return Slot(
        slot_id=row["slot_id"],
        day=date.fromisoformat(row["day"]),
        start=time.fromisoformat(row["start"]),
        end=time.fromisoformat(row["end"]),
        capacity=row["capacity"],
        booked=row["booked"],
    )
