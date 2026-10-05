import os
import json
import tempfile

from toss_trading_runtime.paper_execution import (
    ASSUMPTION_VERSION,
    build_readiness_report,
    simulate_order,
)
from toss_trading_runtime.store import TossRuntimeStore
from toss_trading_runtime.trade_plan import build_trade_plan


def _plan(status="paper_candidate"):
    return {
        "orders_enabled": False,
        "cost_model": {
            "buy_commission_pct": 0.10,
            "sell_commission_pct": 0.10,
            "slippage_each_side_pct": 0.05,
            "sell_regulatory_buffer_pct": 0.01,
            "fx_round_trip_buffer_pct": 0.0,
        },
        "entry": {
            "status": status,
            "trigger_price": 100.0,
            "reference_price": 100.0,
            "zone_low": 99.5,
            "zone_high": 100.5,
            "blockers": ["test_guard"] if status == "blocked" else [],
        },
        "risk": {
            "initial_stop_price": 98.0,
            "max_holding_minutes": 60,
            "paper_risk_multiplier": 0.5,
        },
        "exit": {
            "take_profit_1_price": 102.0,
            "take_profit_2_price": 105.0,
            "move_stop_after_tp1_to": 100.31,
            "trailing_after_tp1_pct": 1.0,
        },
        "validation": {
            "adaptive_guard": {"state": "short_positive_medium_negative"},
        },
    }


def test_new_plan_declares_fill_clock_without_enabling_orders():
    plan = build_trade_plan({"symbol": "NVDA"}, {})
    assert plan["risk"]["holding_clock"] == "entry_fill"
    assert plan["orders_enabled"] is False


def test_delayed_fill_uses_explicit_clock_and_preserves_legacy():
    points = [
        {"at": "2026-07-31 22:40:00.000000", "price": 100.0},
        {"at": "2026-07-31 23:30:00.000000", "price": 101.0},
        {"at": "2026-07-31 23:40:00.000000", "price": 101.5},
    ]
    legacy = simulate_order(
        _plan(), points, "2026-07-31 22:30:00.000000",
        asof="2026-07-31 23:30:00.000000",
    )
    assert legacy["status"] == "closed_timeout"
    assert legacy["holding_clock"] == "plan_created"
    plan = _plan()
    plan["risk"]["holding_clock"] = "entry_fill"
    current = simulate_order(
        plan, points, "2026-07-31 22:30:00.000000",
        asof="2026-07-31 23:30:00.000000",
    )
    assert current["remaining_fraction"] == 1.0
    assert current["terminal"] is False
    assert current["holding_deadline"].startswith("2026-07-31 23:40:00")
    closed = simulate_order(
        plan, points, "2026-07-31 22:30:00.000000",
        asof="2026-07-31 23:40:00.000000",
    )
    assert closed["status"] == "closed_timeout"
    assert closed["exit_at"].startswith("2026-07-31 23:40:00")
    assert closed["net_return_pct"] < closed["gross_return_pct"]
    assert closed["orders_called"] is False


def test_closed_position_excursions_ignore_post_exit_prices():
    result = simulate_order(
        _plan(), [
            {"at": "2026-07-31 22:30:01.000000", "price": 100.0},
            {"at": "2026-07-31 22:31:00.000000", "price": 98.0},
            {"at": "2026-07-31 22:32:00.000000", "price": 120.0},
            {"at": "2026-07-31 22:33:00.000000", "price": 80.0},
        ], "2026-07-31 22:30:00.000000", asof="2026-07-31 22:40:00.000000",
    )
    assert result["status"] == "closed_stop"
    assert result["mfe_pct"] == 0.0
    assert result["mae_pct"] == -2.0


def test_invalid_holding_clock_fails_closed():
    plan = _plan()
    plan["risk"]["holding_clock"] = "unknown"
    result = simulate_order(plan, [], "2026-07-31 22:30:00.000000")
    assert result["status"] == "rejected_by_guard"
    assert result["exit_reason"] == "invalid_holding_policy"
    assert result["orders_called"] is False


def test_blocked_plan_is_recorded_without_broker_execution():
    result = simulate_order(
        _plan(status="blocked"),
        [],
        "2026-07-31 22:30:00.000000",
        asof="2026-07-31 22:31:00.000000",
    )
    assert result["status"] == "rejected_by_guard"
    assert result["orders_called"] is False
    assert result["live_order_enabled"] is False


def test_paper_position_moves_through_tp1_and_trailing_exit_with_costs():
    points = [
        {"at": "2026-07-31 22:30:01.000000", "price": 100.0},
        {"at": "2026-07-31 22:35:00.000000", "price": 102.0},
        {"at": "2026-07-31 22:37:00.000000", "price": 103.0},
        {"at": "2026-07-31 22:38:00.000000", "price": 101.8},
    ]
    result = simulate_order(
        _plan(),
        points,
        "2026-07-31 22:30:00.000000",
        asof="2026-07-31 22:40:00.000000",
    )
    assert result["status"] == "closed_trailing"
    assert result["tp1_hit"] is True
    assert result["remaining_fraction"] == 0
    assert result["cost_model_applied"] is True
    assert result["net_return_pct"] < result["gross_return_pct"]
    assert [item["event_type"] for item in result["events"]].count("position_exit") == 2


def test_gap_above_entry_zone_is_not_chased():
    result = simulate_order(
        _plan(),
        [{"at": "2026-07-31 22:30:01.000000", "price": 103.0}],
        "2026-07-31 22:30:00.000000",
        asof="2026-07-31 22:50:00.000000",
    )
    assert result["status"] == "canceled_gap_above_zone"
    assert result.get("entry_filled_at") is None


def test_readiness_requires_robust_cost_aware_sample_and_never_enables_live():
    rows = []
    for index in range(120):
        rows.append({
            "status": "closed_tp2",
            "entry_filled_at": "2026-07-01 22:30:00.000000",
            "exit_at": "2026-07-01 23:{:02d}:00.000000".format(index % 60),
            "net_return_pct": 0.5,
        })
    report = build_readiness_report(rows)
    assert report["assumption_version"] == ASSUMPTION_VERSION
    assert report["readiness"] == "shadow_ready"
    assert report["live_order_enabled"] is False
    assert report["live_deployment_ready"] is False
    assert all(item["passed"] for item in report["checks"])

def test_store_persists_order_states_events_and_cost_aware_return():
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    handle.close()
    store = TossRuntimeStore(handle.name)
    try:
        created = store.create_paper_execution_orders(
            analysis_id=1,
            structured=[{"symbol": "NVDA", "trade_plan": _plan()}],
            created_at="2026-07-31 22:30:00.000000",
        )
        assert created == 1
        for at, price in [
            ("2026-07-31 22:30:01.000000", 100.0),
            ("2026-07-31 22:35:00.000000", 102.0),
            ("2026-07-31 22:37:00.000000", 103.0),
            ("2026-07-31 22:38:00.000000", 101.8),
        ]:
            store.conn.execute("""
                INSERT INTO price_snapshots (
                    collected_at, symbol, price, currency, payload_json
                ) VALUES (?, 'NVDA', ?, 'USD', '{}')
            """, (at, price))
        store.conn.commit()

        advanced = store.advance_paper_execution_orders(
            now="2026-07-31 22:40:00.000000",
        )
        row = store.conn.execute("""
            SELECT * FROM paper_execution_orders LIMIT 1
        """).fetchone()
        event_count = store.conn.execute("""
            SELECT COUNT(1) FROM paper_execution_events
        """).fetchone()[0]
        readiness = store.paper_execution_readiness()
    finally:
        store.close()
        os.unlink(handle.name)

    assert advanced == {"advanced": 1, "closed": 1}
    assert row["status"] == "closed_trailing"
    assert row["orders_called"] == 0
    assert row["net_return_pct"] < row["gross_return_pct"]
    assert event_count >= 5
    assert readiness["readiness"] == "paper_collecting"
    assert readiness["live_order_enabled"] is False


def test_open_position_refreshes_mark_without_status_transition(tmp_path):
    store = TossRuntimeStore(str(tmp_path / "marks.db"))
    try:
        store.create_paper_execution_orders(
            1, [{"symbol": "NVDA", "trade_plan": _plan()}],
            created_at="2026-07-31 22:30:00.000000",
        )
        for at, price in [("2026-07-31 22:30:01.000000", 100.0),
                          ("2026-07-31 22:35:00.000000", 101.0)]:
            store.conn.execute(
                "INSERT INTO price_snapshots(collected_at,symbol,price,currency,payload_json) VALUES(?,'NVDA',?,'USD','{}')",
                (at, price),
            )
        store.conn.commit()
        store.advance_paper_execution_orders(now="2026-07-31 22:30:02.000000")
        before = store.conn.execute("SELECT result_json FROM paper_execution_orders").fetchone()[0]
        result = store.advance_paper_execution_orders(now="2026-07-31 22:35:01.000000")
        after = store.conn.execute("SELECT result_json FROM paper_execution_orders").fetchone()[0]
        assert result["advanced"] == 1
        assert json.loads(before)["status"] == json.loads(after)["status"]
        assert json.loads(after)["net_return_pct"] > json.loads(before)["net_return_pct"]
        assert store.advance_paper_execution_orders(now="2026-07-31 22:35:01.000000")["advanced"] == 0
    finally:
        store.close()


def test_order_clock_cannot_precede_saved_gpt_decision(tmp_path):
    store = TossRuntimeStore(str(tmp_path / "decision_clock.db"))
    try:
        store.conn.execute("INSERT INTO analysis_results(id,analyzed_at,mode,symbols) VALUES(1,?,'test','NVDA')",
                           ("2026-09-01 23:00:10.000000",))
        store.conn.commit()
        store.create_paper_execution_orders(1, [{"symbol":"NVDA","trade_plan":_plan()}],
                                           created_at="2026-09-01 23:00:00.000000")
        order = store.conn.execute("SELECT created_at FROM paper_execution_orders").fetchone()
        assert order["created_at"] == "2026-09-01 23:00:10.000000"
    finally:
        store.close()
