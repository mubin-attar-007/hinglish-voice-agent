"""The tool adapter: derived keys, preconditions, and a re-read after every write attempt."""

from __future__ import annotations

from datetime import date, datetime, time

from orders.clock import IST, VirtualClock
from orders.db import OrderSpec, WorldSpec, connect, seed, slot_id_for
from orders.faults import FaultKind, FaultPlan, FaultRule
from orders.service import OrderService
from voice_agent.gate.gate import ConfirmationGate
from voice_agent.tools import TOOL_SPECS, SafetyConfig, ToolAdapter, ToolCall, derive_key

NOW = datetime(2026, 11, 26, 10, 0, tzinfo=IST)
FRI, SAT = date(2026, 11, 27), date(2026, 11, 28)
FRI_2PM, SAT_4PM = slot_id_for(FRI, time(14)), slot_id_for(SAT, time(16))
ORDER = "48291"


def make(*rules: FaultRule, safety: SafetyConfig | None = None) -> ToolAdapter:
    conn = connect()
    seed(
        conn,
        WorldSpec(first_day=FRI, days=2, orders=(OrderSpec(ORDER, "380015", "table", FRI_2PM),)),
    )
    clock = VirtualClock(NOW)
    service = OrderService(conn, clock=clock, faults=FaultPlan(rules), call_id="call-1")
    gate = ConfirmationGate(language="english", now=clock.now)
    return ToolAdapter(service, gate, call_id="call-1", safety=safety)


_ids = iter(range(10_000))


def call(adapter: ToolAdapter, name: str, response: str = "r", **args: str) -> dict[str, object]:
    return adapter.execute(ToolCall(f"t{next(_ids)}", name, args), response_id=response).result


def ready(adapter: ToolAdapter) -> None:
    """Verify the order and list Saturday's slots, as a model would before a change."""
    assert call(adapter, "lookup_order", order_id=ORDER, pincode="380015")["found"] is True
    call(adapter, "list_slots", date="2026-11-28", after_time="16:00")
    call(adapter, "list_slots", date="2026-11-27")


def commits(adapter: ToolAdapter) -> int:
    return sum(e.kind == "reschedule_committed" for e in adapter.service.events())


def test_the_model_never_supplies_the_key_version_or_deadline() -> None:
    reschedule = next(spec for spec in TOOL_SPECS if spec.name == "reschedule")
    assert set(reschedule.parameters["properties"]) == {"order_id", "slot_id"}


def test_the_key_is_stable_for_a_retry_and_new_for_a_new_version() -> None:
    assert derive_key("c", ORDER, SAT_4PM, 1) == derive_key("c", ORDER, SAT_4PM, 1)
    assert derive_key("c", ORDER, SAT_4PM, 1) != derive_key("c", ORDER, SAT_4PM, 2)
    assert derive_key("c", ORDER, SAT_4PM, 1) != derive_key("other-call", ORDER, SAT_4PM, 1)


def test_a_change_needs_a_verified_order_and_an_offered_slot() -> None:
    adapter = make()
    assert "lookup_order first" in str(call(adapter, "reschedule", order_id=ORDER, slot_id=SAT_4PM))
    call(adapter, "lookup_order", order_id=ORDER, pincode="380015")
    assert "list_slots" in str(call(adapter, "reschedule", order_id=ORDER, slot_id=SAT_4PM))
    assert commits(adapter) == 0


def test_one_write_per_model_response() -> None:
    adapter = make()
    ready(adapter)
    first = call(adapter, "reschedule", "r1", order_id=ORDER, slot_id=SAT_4PM)
    second = call(adapter, "reschedule", "r1", order_id=ORDER, slot_id=FRI_2PM)
    assert first["status"] == "saved"
    assert "one change at a time" in str(second)
    assert commits(adapter) == 1


def test_after_a_timeout_the_adapter_re_reads_and_reports_the_change_as_saved() -> None:
    adapter = make(FaultRule(FaultKind.TIMEOUT_AFTER_COMMIT))
    ready(adapter)
    result = call(adapter, "reschedule", order_id=ORDER, slot_id=SAT_4PM)
    assert result["status"] == "saved"
    assert result["verified_from_order_system"] is True
    assert "Saturday, 28 November" in str(result["say_this"])
    kinds = [e.kind for e in adapter.service.events()]
    assert kinds.index("order_read", kinds.index("reschedule_requested")) > 0


def test_a_retry_storm_after_a_timeout_writes_once() -> None:
    adapter = make(FaultRule(FaultKind.TIMEOUT_AFTER_COMMIT))
    ready(adapter)
    for response in ("r1", "r2", "r3"):
        call(adapter, "reschedule", response, order_id=ORDER, slot_id=SAT_4PM)
    assert commits(adapter) == 1


def test_an_error_before_commit_is_reported_as_not_saved_and_a_retry_works() -> None:
    adapter = make(FaultRule(FaultKind.ERROR_BEFORE_COMMIT))
    ready(adapter)
    first = call(adapter, "reschedule", "r1", order_id=ORDER, slot_id=SAT_4PM)
    assert first["status"] == "not_saved"
    assert "Friday" in str(first["say_this"])
    assert call(adapter, "reschedule", "r2", order_id=ORDER, slot_id=SAT_4PM)["status"] == "saved"
    assert commits(adapter) == 1


def test_if_the_record_cannot_be_read_the_outcome_is_unknown_and_unsaid() -> None:
    adapter = make(
        FaultRule(FaultKind.TIMEOUT_AFTER_COMMIT),
        FaultRule(FaultKind.ERROR_BEFORE_COMMIT, op="get_order", nth=None),
    )
    ready(adapter)
    result = call(adapter, "reschedule", order_id=ORDER, slot_id=SAT_4PM)
    assert result["status"] == "unknown"
    assert result["verified_from_order_system"] is False
    assert "can't confirm" in str(result["say_this"])


def test_a_repeated_tool_call_in_the_same_response_is_not_run_twice() -> None:
    adapter = make()
    ready(adapter)
    tool_call = ToolCall("same-id", "reschedule", {"order_id": ORDER, "slot_id": SAT_4PM})
    adapter.execute(tool_call, response_id="r1")
    repeat = adapter.execute(tool_call, response_id="r1")
    assert repeat.duplicate_call is True
    assert commits(adapter) == 1


def test_a_call_id_reused_in_a_later_response_is_a_new_call() -> None:
    """Some providers reuse ids across responses: a later change must not get an old answer."""
    adapter = make()
    ready(adapter)
    adapter.execute(
        ToolCall("call_0", "reschedule", {"order_id": ORDER, "slot_id": SAT_4PM}), response_id="r1"
    )
    back = adapter.execute(
        ToolCall("call_0", "reschedule", {"order_id": ORDER, "slot_id": FRI_2PM}), response_id="r2"
    )
    assert back.duplicate_call is False
    assert back.result["status"] == "saved"
    assert "Friday" in str(back.say)
    assert commits(adapter) == 2


def test_an_unknown_order_id_is_an_error_not_a_crash_even_without_preconditions() -> None:
    adapter = make(safety=SafetyConfig.baseline())
    assert "error" in call(adapter, "get_order", order_id="99999")
    result = call(adapter, "reschedule", order_id="99999", slot_id=SAT_4PM)
    assert result == {"status": "order_not_found"}


def test_a_genuine_change_of_mind_writes_each_time() -> None:
    adapter = make()
    ready(adapter)
    for response, slot in (("r1", SAT_4PM), ("r2", FRI_2PM), ("r3", SAT_4PM)):
        assert (
            call(adapter, "reschedule", response, order_id=ORDER, slot_id=slot)["status"] == "saved"
        )
    assert commits(adapter) == 3


def test_the_baseline_double_writes_on_a_retry_after_a_timeout() -> None:
    """What the safety layers prevent: without them, a retry is a second write."""
    adapter = make(FaultRule(FaultKind.TIMEOUT_AFTER_COMMIT), safety=SafetyConfig.baseline())
    ready(adapter)
    first = call(adapter, "reschedule", "r1", order_id=ORDER, slot_id=SAT_4PM)
    assert "timeout" in str(first["error"])
    call(adapter, "reschedule", "r2", order_id=ORDER, slot_id=SAT_4PM)
    assert commits(adapter) == 2
    assert SafetyConfig.baseline().label == "baseline"
    assert SafetyConfig().label == "full"
    assert SafetyConfig(cas=False).label == "without-cas"
