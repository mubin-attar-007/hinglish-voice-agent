"""Order service: idempotency, version checks, deadlines, fault injection and the event log."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time
from typing import Any

import pytest

from orders.clock import IST, VirtualClock
from orders.db import OrderSpec, WorldSpec, connect, seed, slot_id_for
from orders.faults import FaultKind, FaultPlan, FaultRule
from orders.models import OrderServiceError, OrderServiceTimeout, WriteStatus
from orders.service import OrderService

NOW = datetime(2026, 11, 26, 10, 0, tzinfo=IST)  # a Thursday
FRI, SAT = date(2026, 11, 27), date(2026, 11, 28)
FRI_2PM = slot_id_for(FRI, time(14))
SAT_4PM = slot_id_for(SAT, time(16))
SAT_6PM = slot_id_for(SAT, time(18))
ORDER = "48291"


def make(*rules: FaultRule, full: tuple[str, ...] = ()) -> OrderService:
    conn = connect()
    order = OrderSpec(ORDER, "380015", "study table", FRI_2PM)
    seed(conn, WorldSpec(first_day=FRI, days=2, orders=(order,), full_slots=full))
    return OrderService(conn, clock=VirtualClock(NOW), faults=FaultPlan(rules), call_id="test")


def commits(svc: OrderService) -> list[dict[str, Any]]:
    return [event.data for event in svc.events() if event.kind == "reschedule_committed"]


def slot_of(svc: OrderService) -> str:
    view = svc.peek_order(ORDER)
    assert view is not None
    return view.slot.slot_id


# ---- the plain write --------------------------------------------------------------------------


def test_reschedule_moves_the_order_and_logs_one_commit() -> None:
    svc = make()
    result = svc.reschedule(ORDER, SAT_4PM, idem_key="k1", expected_version=1)
    assert result.status is WriteStatus.COMMITTED
    assert result.order is not None and result.order.version == 2
    assert slot_of(svc) == SAT_4PM
    assert commits(svc) == [
        {"from_slot": FRI_2PM, "to_slot": SAT_4PM, "version": 2, "key": "k1", "late": False}
    ]


def test_full_unknown_and_missing_are_rejected_without_writing() -> None:
    svc = make(full=(SAT_4PM,))
    assert svc.reschedule(ORDER, SAT_4PM).status is WriteStatus.SLOT_FULL
    assert svc.reschedule(ORDER, "20991231-1000").status is WriteStatus.SLOT_NOT_FOUND
    assert svc.reschedule("00000", SAT_6PM).status is WriteStatus.ORDER_NOT_FOUND
    assert commits(svc) == []
    assert slot_of(svc) == FRI_2PM


# ---- idempotency ------------------------------------------------------------------------------


def test_same_key_replays_the_original_result_without_writing() -> None:
    svc = make()
    first = svc.reschedule(ORDER, SAT_4PM, idem_key="k1")
    again = svc.reschedule(ORDER, SAT_4PM, idem_key="k1")
    assert again.status is WriteStatus.REPLAYED
    assert again.commit_seq == first.commit_seq
    assert len(commits(svc)) == 1


def test_a_key_reused_for_a_different_change_is_rejected() -> None:
    svc = make()
    svc.reschedule(ORDER, SAT_4PM, idem_key="k1")
    assert svc.reschedule(ORDER, SAT_6PM, idem_key="k1").status is WriteStatus.KEY_MISMATCH
    assert slot_of(svc) == SAT_4PM


def test_order_already_in_the_slot_is_a_noop() -> None:
    svc = make()
    svc.reschedule(ORDER, SAT_4PM, idem_key="k1")
    assert svc.reschedule(ORDER, SAT_4PM, idem_key="k2").status is WriteStatus.NOOP
    assert len(commits(svc)) == 1


def test_naive_requests_write_every_time() -> None:
    """Without keys or the no-op check a retry is a second write: what the baseline measures."""
    svc = make()
    svc.reschedule(ORDER, SAT_4PM, noop_if_unchanged=False)
    svc.reschedule(ORDER, SAT_4PM, noop_if_unchanged=False)
    assert len(commits(svc)) == 2


def test_a_versioned_key_keeps_a_genuine_change_of_mind_distinct() -> None:
    """A to B to A to B: four real changes, none of them mistaken for a retry."""
    svc = make()
    version = 1
    for target in (SAT_4PM, FRI_2PM, SAT_4PM, FRI_2PM):
        result = svc.reschedule(
            ORDER, target, idem_key=f"{target}@v{version}", expected_version=version
        )
        assert result.status is WriteStatus.COMMITTED
        assert result.order is not None
        version = result.order.version
    assert len(commits(svc)) == 4


# ---- compare-and-set --------------------------------------------------------------------------


def test_a_stale_expected_version_is_a_conflict() -> None:
    svc = make()
    svc.reschedule(ORDER, SAT_4PM, idem_key="k1", expected_version=1)
    stale = svc.reschedule(ORDER, SAT_6PM, idem_key="k2", expected_version=1)
    assert stale.status is WriteStatus.CONFLICT
    assert slot_of(svc) == SAT_4PM


# ---- faults -----------------------------------------------------------------------------------


def test_timeout_after_commit_saves_and_a_retry_replays() -> None:
    svc = make(FaultRule(FaultKind.TIMEOUT_AFTER_COMMIT))
    with pytest.raises(OrderServiceTimeout):
        svc.reschedule(ORDER, SAT_4PM, idem_key="k1", expected_version=1)
    assert slot_of(svc) == SAT_4PM  # saved, although the caller timed out
    retry = svc.reschedule(ORDER, SAT_4PM, idem_key="k1", expected_version=1)
    assert retry.status is WriteStatus.REPLAYED
    assert len(commits(svc)) == 1


def test_error_before_commit_writes_nothing_and_leaves_the_key_unused() -> None:
    svc = make(FaultRule(FaultKind.ERROR_BEFORE_COMMIT))
    with pytest.raises(OrderServiceError):
        svc.reschedule(ORDER, SAT_4PM, idem_key="k1")
    assert slot_of(svc) == FRI_2PM
    assert svc.reschedule(ORDER, SAT_4PM, idem_key="k1").status is WriteStatus.COMMITTED


def test_slot_taken_meanwhile_rejects_the_write() -> None:
    svc = make(FaultRule(FaultKind.SLOT_TAKEN_MEANWHILE))
    assert svc.reschedule(ORDER, SAT_4PM, idem_key="k1").status is WriteStatus.SLOT_FULL
    assert [event.kind for event in svc.events()].count("slot_taken_by_other") == 1
    assert slot_of(svc) == FRI_2PM


def test_a_slow_response_within_the_timeout_succeeds_and_takes_time() -> None:
    svc = make(FaultRule(FaultKind.SLOW_RESPONSE, delay_ms=1200))
    result = svc.reschedule(ORDER, SAT_4PM, client_timeout_ms=2500)
    assert result.status is WriteStatus.COMMITTED
    assert svc.clock.now_ms == 1200


def test_the_deadline_refuses_a_write_that_would_land_after_the_caller_gave_up() -> None:
    svc = make(FaultRule(FaultKind.SLOW_RESPONSE, delay_ms=6000))
    with pytest.raises(OrderServiceTimeout):
        svc.reschedule(ORDER, SAT_4PM, deadline_ms=2500, client_timeout_ms=2500)
    svc.clock.advance(10_000)
    svc.tick()
    assert slot_of(svc) == FRI_2PM  # final: nothing can land later
    assert commits(svc) == []
    assert svc.writes_in_flight == 0


def test_without_a_deadline_a_slow_write_lands_after_the_caller_gave_up() -> None:
    """The race the deadline exists to prevent."""
    svc = make(FaultRule(FaultKind.SLOW_RESPONSE, delay_ms=6000))
    with pytest.raises(OrderServiceTimeout):
        svc.reschedule(ORDER, SAT_4PM, client_timeout_ms=2500)
    assert svc.get_order(ORDER).slot.slot_id == FRI_2PM  # a re-read now says "not changed"...
    svc.clock.advance(4000)
    assert svc.get_order(ORDER).slot.slot_id == SAT_4PM  # ...and then the write lands
    assert commits(svc)[0]["late"] is True


def test_a_fault_fires_only_on_its_nth_call() -> None:
    svc = make(FaultRule(FaultKind.ERROR_BEFORE_COMMIT, nth=2))
    assert svc.reschedule(ORDER, SAT_4PM, idem_key="a").status is WriteStatus.COMMITTED
    with pytest.raises(OrderServiceError):
        svc.reschedule(ORDER, SAT_6PM, idem_key="b")
    assert svc.reschedule(ORDER, SAT_6PM, idem_key="b").status is WriteStatus.COMMITTED


def test_read_faults_fail_or_time_out() -> None:
    svc = make(
        FaultRule(FaultKind.ERROR_BEFORE_COMMIT, op="get_order", nth=1),
        FaultRule(FaultKind.SLOW_RESPONSE, op="get_order", nth=2, delay_ms=9000),
    )
    with pytest.raises(OrderServiceError):
        svc.get_order(ORDER)
    with pytest.raises(OrderServiceTimeout):
        svc.get_order(ORDER)
    assert svc.get_order(ORDER).slot.slot_id == FRI_2PM


def test_fault_rules_reject_impossible_combinations() -> None:
    with pytest.raises(ValueError):
        FaultRule(FaultKind.TIMEOUT_AFTER_COMMIT, op="get_order")
    with pytest.raises(ValueError):
        FaultRule(FaultKind.SLOW_RESPONSE)


# ---- reads ------------------------------------------------------------------------------------


def test_lookup_needs_the_right_pincode() -> None:
    svc = make()
    assert svc.lookup_order(ORDER, "380015") is not None
    assert svc.lookup_order(ORDER, "111111") is None
    assert svc.lookup_order("00000", "380015") is None


def test_list_slots_after_a_time_marks_full_slots() -> None:
    svc = make(full=(SAT_4PM,))
    slots = svc.list_slots(SAT, after=time(16))
    assert [(slot.slot_id, slot.available) for slot in slots] == [(SAT_4PM, False), (SAT_6PM, True)]


# ---- the event log ----------------------------------------------------------------------------


def test_the_event_log_is_append_only() -> None:
    conn = connect()
    conn.execute("INSERT INTO events (at_ms, call_id, kind) VALUES (0, 't', 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE events SET kind = 'y'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM events")
