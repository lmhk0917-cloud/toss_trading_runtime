from toss_trading_runtime import config
from toss_trading_runtime.trade_plan import attach_trade_plans, build_trade_plan, cost_model


def _evidence(guidance_label="sample_too_small", samples=4, expectancy=None, profit_factor=None):
    return {
        "safe_for_analysis": True,
        "shared_context_status": {"status": "ok"},
        "symbol_evidence": {
            "NVDA": {
                "price": {"lastPrice": 100.0},
                "minute_candles_summary": {
                    "sample": 120,
                    "latest_close": 100.0,
                    "range_high": 102.0,
                    "range_low": 98.0,
                    "vwap": 99.5,
                    "rsi14": 55.0,
                },
                "quant_feedback_guidance": {
                    "label": guidance_label,
                    "sample_count": samples,
                },
                "quant_feedback": {
                    "evaluated_count": samples,
                    "expectancy_pct": expectancy,
                    "net_profit_factor": profit_factor,
                },
            }
        },
    }


def _analysis(decision="WATCH", score=85, risk="MEDIUM", confidence="HIGH"):
    return {
        "symbol": "NVDA",
        "final_decision": decision,
        "interest_score": score,
        "risk_level": risk,
        "confidence": confidence,
    }


def test_default_cost_model_is_nonzero_and_componentized():
    model = cost_model()
    assert model["buy_commission_pct"] == config.US_STOCK_COMMISSION_PCT
    assert model["sell_commission_pct"] == config.US_STOCK_COMMISSION_PCT
    assert model["round_trip_total_pct"] == round(config.PAPER_ROUND_TRIP_COST_PCT, 4)
    assert model["round_trip_total_pct"] > 0
    assert model["small_order_fee_exemption_applied"] is False


def test_small_feedback_can_only_create_paper_candidate():
    plan = build_trade_plan(_analysis(), _evidence())
    assert plan["entry"]["status"] == "paper_candidate"
    assert plan["readiness"] == "paper_ready"
    assert plan["orders_enabled"] is False
    assert plan["safety"]["orders_called"] is False
    assert plan["safety"]["live_trade_blocked"] is True
    assert plan["validation"]["evidence_reflection_enabled"] is True
    assert plan["validation"]["paper_validation_enabled"] is True
    assert plan["exit"]["take_profit_1_price"] > plan["entry"]["trigger_price"]
    assert plan["risk"]["initial_stop_price"] < plan["entry"]["trigger_price"]
    assert plan["validation"]["net_reward_risk_1"] >= config.TRADE_PLAN_MIN_NET_RR


def test_positive_feedback_promotes_only_to_shadow_candidate():
    evidence = _evidence(
        guidance_label="positive_expectancy",
        samples=config.TRADE_PLAN_MIN_FEEDBACK_SAMPLES,
        expectancy=0.25,
        profit_factor=1.25,
    )
    evidence["symbol_evidence"]["NVDA"]["adaptive_guard"] = {
        "state": "consistent_positive",
        "risk_multiplier": 1.0,
        "allow_shadow_promotion": True,
    }
    plan = build_trade_plan(
        _analysis(),
        evidence,
    )
    assert plan["entry"]["status"] == "shadow_candidate"
    assert plan["readiness"] == "shadow_ready"
    assert plan["orders_enabled"] is False
    assert plan["safety"]["live_trade_blocked"] is True


def test_negative_expectancy_and_high_risk_are_blocked():
    plan = build_trade_plan(
        _analysis(risk="HIGH"),
        _evidence(
            guidance_label="negative_expectancy",
            samples=30,
            expectancy=-0.30,
            profit_factor=0.70,
        ),
    )
    assert plan["entry"]["status"] == "blocked"
    assert plan["readiness"] == "research_only"
    assert "high_risk" in plan["entry"]["blockers"]
    assert "negative_expectancy" in plan["entry"]["blockers"]


def test_attach_trade_plans_preserves_analysis_fields():
    planned = attach_trade_plans([_analysis()], _evidence())
    assert planned[0]["final_decision"] == "WATCH"
    assert planned[0]["trade_plan"]["schema"] == "trade_plan_v1"
