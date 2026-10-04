"""Multi-window loss guard for research and paper-trading decisions.

The guard adapts participation and paper-risk limits. It deliberately does not
optimize stop or take-profit levels from a short recent window.
"""


SCHEMA = "adaptive_guard_v1"


def build_adaptive_guard(short_window=None, medium_window=None, min_short=5, min_medium=20):
    short = _observation(short_window, min_short)
    medium = _observation(medium_window, min_medium)
    state = "insufficient"
    risk_multiplier = 0.50
    allow_shadow_promotion = False
    blocker = None

    if short["sufficient"] and medium["sufficient"]:
        if short["quality"] == "positive" and medium["quality"] == "positive":
            state = "consistent_positive"
            risk_multiplier = 1.00
            allow_shadow_promotion = True
        elif short["quality"] == "positive" and medium["quality"] == "negative":
            state = "short_positive_medium_negative"
            risk_multiplier = 0.50
            blocker = "adaptive_short_only_improvement"
        elif short["quality"] == "negative" and medium["quality"] == "positive":
            state = "deteriorating"
            risk_multiplier = 0.25
            blocker = "adaptive_recent_deterioration"
        elif short["quality"] == "negative" and medium["quality"] == "negative":
            state = "consistent_negative"
            risk_multiplier = 0.00
            blocker = "adaptive_consistent_negative"
        else:
            state = "mixed"
            risk_multiplier = 0.50
            blocker = "adaptive_windows_mixed"
    elif short["sufficient"] and short["quality"] == "negative":
        state = "recent_negative_medium_insufficient"
        risk_multiplier = 0.25
        blocker = "adaptive_recent_negative"

    return {
        "schema": SCHEMA,
        "state": state,
        "risk_multiplier": risk_multiplier,
        "evidence_reflection_enabled": True,
        "paper_validation_enabled": True,
        "live_trade_blocked": True,
        "allow_shadow_promotion": allow_shadow_promotion,
        "allow_parameter_relaxation": allow_shadow_promotion,
        "fixed_exit_rule_promoted": False,
        "blocker": blocker,
        "short_window": short,
        "medium_window": medium,
        "policy": (
            "Tighten immediately on deterioration; relax only when both 5d and "
            "20d cost-aware feedback are positive. Exit levels are not tuned "
            "from short-window performance. Defensive states still keep "
            "paper-only evidence reflection active for test feedback."
        ),
    }


def _observation(window, minimum):
    window = window or {}
    metrics = window.get("metrics") or {}
    guidance = window.get("guidance") or {}
    count = int(
        guidance.get("sample_count")
        or metrics.get("evaluated_count")
        or metrics.get("evaluated_60m_count")
        or 0
    )
    expectancy = _number(
        metrics.get("expectancy_pct"),
        _number(metrics.get("avg_net_return_60m_pct")),
    )
    profit_factor = _number(
        metrics.get("net_profit_factor"),
        _number(metrics.get("profit_factor_60m")),
    )
    label = str(guidance.get("label") or "sample_too_small")
    sufficient = count >= int(minimum)
    quality = "mixed"
    if sufficient:
        if label == "positive_expectancy" and (expectancy or 0) > 0 and (profit_factor or 0) >= 1:
            quality = "positive"
        elif label == "negative_expectancy" or (
            expectancy is not None and expectancy < 0
        ) or (
            profit_factor is not None and profit_factor < 1
        ):
            quality = "negative"
    return {
        "window_days": window.get("window_days"),
        "sample_count": count,
        "minimum_sample": int(minimum),
        "sufficient": sufficient,
        "quality": quality,
        "guidance_label": label,
        "net_expectancy_pct": expectancy,
        "net_profit_factor": profit_factor,
    }


def _number(value, default=None):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
