from toss_trading_runtime.adaptive_guard import build_adaptive_guard
from toss_trading_runtime.decision_calibration import calibrate_structured_analysis
from toss_trading_runtime.quant_feedback import attach_quant_feedback_windows_to_evidence


def _window(label, count, expectancy, profit_factor, days):
    return {
        "window_days": days,
        "metrics": {
            "evaluated_count": count,
            "expectancy_pct": expectancy,
            "net_profit_factor": profit_factor,
        },
        "guidance": {"label": label, "sample_count": count},
    }


def test_short_positive_does_not_override_negative_medium_window():
    guard = build_adaptive_guard(
        _window("positive_expectancy", 8, 0.20, 1.30, 5),
        _window("negative_expectancy", 24, -0.12, 0.75, 20),
        min_short=5,
        min_medium=20,
    )
    assert guard["state"] == "short_positive_medium_negative"
    assert guard["allow_shadow_promotion"] is False
    assert guard["evidence_reflection_enabled"] is True
    assert guard["paper_validation_enabled"] is True
    assert guard["live_trade_blocked"] is True
    assert guard["risk_multiplier"] == 0.50
    assert guard["fixed_exit_rule_promoted"] is False


def test_recent_deterioration_tightens_decision_immediately():
    guard = build_adaptive_guard(
        _window("negative_expectancy", 8, -0.20, 0.65, 5),
        _window("positive_expectancy", 24, 0.18, 1.20, 20),
        min_short=5,
        min_medium=20,
    )
    evidence = {
        "symbol_evidence": {
            "NVDA": {
                "adaptive_guard": guard,
                "feedback_adjustment": {"samples": 0},
                "quant_feedback_guidance": {"label": "sample_too_small"},
                "quant_feedback": {},
                "minute_candles_summary": {},
                "daily_candles_summary": {},
            },
        },
        "shared_context_status": {"status": "ok"},
        "market_relationship": {},
    }
    result = calibrate_structured_analysis([{
        "symbol": "NVDA",
        "final_decision": "WATCH",
        "interest_score": 80,
        "risk_level": "MEDIUM",
        "confidence": "HIGH",
    }], evidence)[0]
    assert guard["state"] == "deteriorating"
    assert result["final_decision"] == "RISK"
    assert result["risk_level"] == "HIGH"
    assert result["interest_score"] < 80


def test_both_positive_windows_allow_only_shadow_promotion():
    guard = build_adaptive_guard(
        _window("positive_expectancy", 8, 0.20, 1.30, 5),
        _window("positive_expectancy", 24, 0.10, 1.15, 20),
        min_short=5,
        min_medium=20,
    )
    assert guard["state"] == "consistent_positive"
    assert guard["allow_shadow_promotion"] is True
    assert guard["evidence_reflection_enabled"] is True
    assert guard["live_trade_blocked"] is True
    assert guard["risk_multiplier"] == 1.0

def test_feedback_windows_attach_symbol_guard_to_analysis_evidence():
    short = _window("positive_expectancy", 8, 0.20, 1.30, 5)
    medium = _window("negative_expectancy", 24, -0.12, 0.75, 20)
    windows = {
        "schema": "toss_quant_feedback_windows_v1",
        "short_5d": {
            "window_days": 5,
            "min_sample": 5,
            "by_symbol": [dict(short["metrics"], symbol="NVDA")],
            "symbol_guidance": {"NVDA": short["guidance"]},
        },
        "medium_20d": {
            "window_days": 20,
            "min_sample": 20,
            "by_symbol": [dict(medium["metrics"], symbol="NVDA")],
            "symbol_guidance": {"NVDA": medium["guidance"]},
        },
    }
    evidence = {"symbol_evidence": {"NVDA": {}}}
    attach_quant_feedback_windows_to_evidence(evidence, windows)
    assert evidence["symbol_evidence"]["NVDA"]["adaptive_guard"]["state"] == (
        "short_positive_medium_negative"
    )
