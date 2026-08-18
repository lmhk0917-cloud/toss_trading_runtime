import os
import tempfile

from toss_trading_runtime.dashboard import build_dashboard_snapshot, render_dashboard_html
from toss_trading_runtime.store import TossRuntimeStore


def test_trade_plan_is_visible_in_dashboard_snapshot_and_html():
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    handle.close()
    store = TossRuntimeStore(handle.name)
    try:
        analysis_id = store.save_analysis_result(
            {"symbols": ["NVDA"], "collected_at": "2026-07-31 22:30:00.000000"},
            {"model": "test", "analysis": "NVDA"},
        )
        store.save_structured_analysis(analysis_id, [{
            "symbol": "NVDA",
            "final_decision": "WATCH",
            "interest_score": 85,
            "risk_level": "MEDIUM",
            "confidence": "HIGH",
            "summary": "Cost-aware paper candidate.",
            "trade_plan": {
                "orders_enabled": False,
                "readiness": "paper_ready",
                "cost_model": {"round_trip_total_pct": 0.31},
                "entry": {
                    "status": "paper_candidate",
                    "trigger_price": 100.0,
                },
                "risk": {"initial_stop_price": 98.0, "paper_risk_multiplier": 0.5},
                "validation": {"adaptive_guard": {"state": "short_positive_medium_negative"}},
                "exit": {
                    "take_profit_1_price": 104.0,
                    "take_profit_2_price": 107.0,
                },
            },
        }])
        snapshot = build_dashboard_snapshot(store, symbols=["NVDA"])
        html = render_dashboard_html(snapshot)
    finally:
        store.close()
        os.unlink(handle.name)

    assert snapshot["symbols"][0]["trade_plan"]["entry"]["status"] == "paper_candidate"
    assert "paper_candidate" in html
    assert "cost 0.31%" in html
    assert "short_positive_medium_negative" in html
    assert "x0.50" in html
