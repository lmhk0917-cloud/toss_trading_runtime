"""Cost-aware entry and exit plans for research and paper validation.

The plan is deliberately separated from order execution. It can promote an
analysis to paper or shadow validation, but it never enables or places orders.
"""

try:
    from . import config
except ImportError:  # pragma: no cover
    import config


SCHEMA = "trade_plan_v1"


def attach_trade_plans(structured, evidence):
    result = []
    for item in structured or []:
        planned = dict(item)
        planned["trade_plan"] = build_trade_plan(planned, evidence)
        result.append(planned)
    return result


def build_trade_plan(analysis, evidence):
    analysis = analysis or {}
    evidence = evidence or {}
    symbol = str(analysis.get("symbol") or "").upper()
    symbol_evidence = (evidence.get("symbol_evidence") or {}).get(symbol) or {}
    minute = symbol_evidence.get("minute_candles_summary") or {}
    feedback = symbol_evidence.get("quant_feedback") or {}
    guidance = symbol_evidence.get("quant_feedback_guidance") or {}
    adaptive_guard = symbol_evidence.get("adaptive_guard") or {}

    price = _price(symbol_evidence)
    score = _number(analysis.get("interest_score"), 0)
    decision = str(analysis.get("final_decision") or "UNKNOWN").upper()
    risk = str(analysis.get("risk_level") or "UNKNOWN").upper()
    confidence = str(analysis.get("confidence") or "UNKNOWN").upper()
    min_score = float(getattr(config, "TRADE_PLAN_MIN_ENTRY_SCORE", 75))
    min_samples = int(getattr(config, "TRADE_PLAN_MIN_FEEDBACK_SAMPLES", 20))
    min_net_rr = float(getattr(config, "TRADE_PLAN_MIN_NET_RR", 1.20))

    blockers = []
    warnings = []
    if not evidence.get("safe_for_analysis"):
        blockers.append("analysis_data_not_safe")
    if price <= 0:
        blockers.append("missing_reference_price")
    if decision != "WATCH":
        blockers.append("decision_not_watch")
    if score < min_score:
        blockers.append("interest_score_below_{:.0f}".format(min_score))
    if risk == "HIGH":
        blockers.append("high_risk")
    if confidence not in ("MEDIUM", "HIGH"):
        blockers.append("confidence_below_medium")
    if int(minute.get("sample") or 0) < 30:
        blockers.append("insufficient_minute_candles")
    if _number(minute.get("rsi14")) is not None and _number(minute.get("rsi14")) >= 78:
        blockers.append("short_term_overbought")

    guidance_label = str(guidance.get("label") or "no_feedback")
    sample_count = int(
        guidance.get("sample_count")
        or feedback.get("evaluated_count")
        or 0
    )
    expectancy = _number(feedback.get("expectancy_pct"))
    profit_factor = _number(feedback.get("net_profit_factor"))
    if guidance_label in ("negative_expectancy", "mixed_expectancy"):
        blockers.append(guidance_label)
    elif guidance_label in ("sample_too_small", "no_feedback") or sample_count < min_samples:
        warnings.append("feedback_sample_below_{}".format(min_samples))
    if adaptive_guard.get("blocker"):
        blockers.append(adaptive_guard["blocker"])
    elif adaptive_guard.get("state") == "insufficient":
        warnings.append("adaptive_multi_window_sample_insufficient")

    context_status = evidence.get("shared_context_status") or {}
    if context_status.get("status") in ("missing", "stale", "partial"):
        warnings.append("shared_context_not_fully_fresh")

    levels = _levels(price, minute)
    if levels["net_reward_risk_1"] < min_net_rr:
        blockers.append("net_reward_risk_below_{:.2f}".format(min_net_rr))

    status = "blocked"
    readiness = "research_only"
    if not blockers:
        positive_feedback = (
            sample_count >= min_samples
            and guidance_label == "positive_expectancy"
            and expectancy is not None
            and expectancy > 0
            and profit_factor is not None
            and profit_factor >= 1.0
        )
        if positive_feedback and adaptive_guard.get("allow_shadow_promotion"):
            status = "shadow_candidate"
            readiness = "shadow_ready"
        else:
            status = "paper_candidate"
            readiness = "paper_ready"

    return {
        "schema": SCHEMA,
        "broker": "tossinvest",
        "market": "US",
        "symbol": symbol,
        "orders_enabled": False,
        "readiness": readiness,
        "cost_model": cost_model(),
        "entry": {
            "status": status,
            "reference_price": levels["reference_price"],
            "trigger_price": levels["trigger_price"],
            "zone_low": levels["zone_low"],
            "zone_high": levels["zone_high"],
            "rule": "WATCH score/risk/feedback gate plus VWAP reclaim when price is below VWAP",
            "blockers": _dedupe(blockers),
            "warnings": _dedupe(warnings),
        },
        "risk": {
            "initial_stop_price": levels["stop_price"],
            "initial_stop_distance_pct": levels["stop_distance_pct"],
            "max_holding_minutes": int(getattr(config, "TRADE_PLAN_MAX_HOLD_MINUTES", 60)),
            "position_size_enabled": False,
            "paper_risk_multiplier": adaptive_guard.get("risk_multiplier", 0.50),
            "note": "Position sizing remains disabled until shadow validation and account-level risk limits pass.",
        },
        "exit": {
            "take_profit_1_price": levels["target_1_price"],
            "take_profit_1_fraction": 0.50,
            "take_profit_2_price": levels["target_2_price"],
            "take_profit_2_fraction": 0.50,
            "move_stop_after_tp1_to": levels["breakeven_after_cost_price"],
            "trailing_after_tp1_pct": levels["trailing_after_tp1_pct"],
            "timeout_action": "exit_or_revalidate_at_max_holding_time",
            "stop_fill_warning": "A stop trigger does not guarantee the stop price in a fast market.",
        },
        "validation": {
            "feedback_label": guidance_label,
            "feedback_samples": sample_count,
            "net_expectancy_pct": expectancy,
            "net_profit_factor": profit_factor,
            "minimum_feedback_samples": min_samples,
            "minimum_net_reward_risk": min_net_rr,
            "net_reward_risk_1": levels["net_reward_risk_1"],
            "net_reward_risk_2": levels["net_reward_risk_2"],
            "adaptive_guard": adaptive_guard,
            "evidence_reflection_enabled": adaptive_guard.get("evidence_reflection_enabled", True),
            "paper_validation_enabled": adaptive_guard.get("paper_validation_enabled", True),
        },
        "safety": {
            "order_mode": getattr(config, "ORDER_MODE", "disabled"),
            "allow_real_order": bool(getattr(config, "ALLOW_REAL_ORDER", False)),
            "orders_called": False,
            "live_trade_blocked": adaptive_guard.get("live_trade_blocked", True),
        },
    }


def cost_model():
    return {
        "currency": "USD",
        "buy_commission_pct": round(float(config.US_STOCK_COMMISSION_PCT), 4),
        "sell_commission_pct": round(float(config.US_STOCK_COMMISSION_PCT), 4),
        "slippage_each_side_pct": round(float(config.US_STOCK_SLIPPAGE_PCT), 4),
        "sell_regulatory_buffer_pct": round(float(config.US_STOCK_REGULATORY_BUFFER_PCT), 4),
        "fx_round_trip_buffer_pct": round(float(config.US_STOCK_FX_ROUND_TRIP_BUFFER_PCT), 4),
        "round_trip_total_pct": round(float(config.PAPER_ROUND_TRIP_COST_PCT), 4),
        "source": config.PAPER_COST_SOURCE,
        "small_order_fee_exemption_applied": False,
        "note": "The official small-order exemption is not assumed because the analysis has no order notional.",
    }


def _levels(price, minute):
    if price <= 0:
        return {
            "reference_price": None,
            "trigger_price": None,
            "zone_low": None,
            "zone_high": None,
            "stop_price": None,
            "stop_distance_pct": 0.0,
            "target_1_price": None,
            "target_2_price": None,
            "breakeven_after_cost_price": None,
            "trailing_after_tp1_pct": 0.0,
            "net_reward_risk_1": 0.0,
            "net_reward_risk_2": 0.0,
        }

    vwap = _number(minute.get("vwap"))
    trigger = max(price, vwap) if vwap and vwap > 0 else price
    range_high = _number(minute.get("range_high"))
    range_low = _number(minute.get("range_low"))
    observed_span_pct = 0.0
    if range_high and range_low and range_high > range_low:
        observed_span_pct = (range_high - range_low) / trigger * 100.0
    stop_distance = _clamp(observed_span_pct * 0.35, 0.90, 2.00)
    total_cost = float(config.PAPER_ROUND_TRIP_COST_PCT)
    target_1_pct = max(stop_distance * 1.50 + total_cost, total_cost + 0.75)
    target_2_pct = max(stop_distance * 2.40 + total_cost, total_cost + 1.50)
    downside_after_cost = stop_distance + total_cost
    net_reward_1 = max(0.0, target_1_pct - total_cost)
    net_reward_2 = max(0.0, target_2_pct - total_cost)

    return {
        "reference_price": _round_price(price),
        "trigger_price": _round_price(trigger),
        "zone_low": _round_price(trigger * (1.0 - 0.0015)),
        "zone_high": _round_price(trigger * (1.0 + 0.0015)),
        "stop_price": _round_price(trigger * (1.0 - stop_distance / 100.0)),
        "stop_distance_pct": round(stop_distance, 4),
        "target_1_price": _round_price(trigger * (1.0 + target_1_pct / 100.0)),
        "target_2_price": _round_price(trigger * (1.0 + target_2_pct / 100.0)),
        "breakeven_after_cost_price": _round_price(trigger * (1.0 + total_cost / 100.0)),
        "trailing_after_tp1_pct": round(max(0.50, stop_distance * 0.50), 4),
        "net_reward_risk_1": round(net_reward_1 / downside_after_cost, 4),
        "net_reward_risk_2": round(net_reward_2 / downside_after_cost, 4),
    }


def _price(symbol_evidence):
    price = symbol_evidence.get("price") or {}
    return _number(
        price.get("lastPrice")
        or price.get("price")
        or (symbol_evidence.get("minute_candles_summary") or {}).get("latest_close"),
        0.0,
    )


def _number(value, default=None):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value, low, high):
    return max(low, min(high, value))


def _round_price(value):
    return round(float(value), 4) if value is not None else None


def _dedupe(items):
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result
