"""Deterministic paper-order execution and deployment-readiness metrics.

This module never calls a broker. It replays stored prices through the same
entry, stop, take-profit, trailing-stop, and timeout assumptions used by a
trade plan so paper results can be audited as a position state machine.
"""

import math
from datetime import datetime, timedelta


SCHEMA = "paper_execution_v1"
ASSUMPTION_VERSION = "paper_fill_v2_explicit_holding_clock"
TERMINAL_STATES = set((
    "rejected_by_guard",
    "canceled_no_fill",
    "canceled_gap_above_zone",
    "closed_stop",
    "closed_breakeven",
    "closed_trailing",
    "closed_tp2",
    "closed_timeout",
))


def simulate_order(plan, price_points, created_at, asof=None, entry_timeout_minutes=15):
    """Replay one long-only paper plan against timestamped stored prices."""
    plan = plan or {}
    entry = plan.get("entry") or {}
    risk = plan.get("risk") or {}
    exit_plan = plan.get("exit") or {}
    created_dt = _parse_dt(created_at)
    asof_dt = _parse_dt(asof) or datetime.now()
    points = _normalize_points(price_points)
    events = [_event("order_created", created_at, None, "planned")]
    blockers = entry.get("blockers") or []
    candidate_status = str(entry.get("status") or "blocked")

    if candidate_status not in ("paper_candidate", "shadow_candidate"):
        events.append(_event(
            "order_rejected",
            created_at,
            None,
            "rejected_by_guard",
            note=",".join(blockers) or candidate_status,
        ))
        return _result(
            plan,
            "rejected_by_guard",
            events,
            terminal=True,
            exit_reason="guard_blocked",
        )

    if not created_dt:
        events.append(_event(
            "order_rejected",
            created_at,
            None,
            "rejected_by_guard",
            note="invalid_created_at",
        ))
        return _result(
            plan,
            "rejected_by_guard",
            events,
            terminal=True,
            exit_reason="invalid_created_at",
        )

    trigger = _number(entry.get("trigger_price") or entry.get("reference_price"))
    zone_low = _number(entry.get("zone_low"), trigger)
    zone_high = _number(entry.get("zone_high"), trigger)
    stop = _number(risk.get("initial_stop_price"))
    target_1 = _number(exit_plan.get("take_profit_1_price"))
    target_2 = _number(exit_plan.get("take_profit_2_price"))
    max_hold = int(_number(risk.get("max_holding_minutes"), 60) or 60)
    holding_clock = risk.get("holding_clock", "plan_created")
    if holding_clock not in ("plan_created", "entry_fill") or max_hold <= 0:
        events.append(_event(
            "order_rejected", created_at, None, "rejected_by_guard",
            note="invalid_holding_policy",
        ))
        return _result(
            plan, "rejected_by_guard", events, terminal=True,
            exit_reason="invalid_holding_policy",
        )
    trailing_pct = _number(exit_plan.get("trailing_after_tp1_pct"), 0.0) or 0.0
    breakeven_stop = _number(exit_plan.get("move_stop_after_tp1_to"), stop)
    entry_expiry = created_dt + timedelta(
        minutes=max(1, min(int(entry_timeout_minutes), max_hold))
    )
    horizon_end = created_dt + timedelta(minutes=max_hold)

    if not all(value is not None and value > 0 for value in (
        trigger, zone_low, zone_high, stop, target_1, target_2,
    )):
        events.append(_event(
            "order_rejected",
            created_at,
            None,
            "rejected_by_guard",
            note="invalid_plan_levels",
        ))
        return _result(
            plan,
            "rejected_by_guard",
            events,
            terminal=True,
            exit_reason="invalid_plan_levels",
        )

    eligible_points = [
        point for point in points
        if created_dt <= point["dt"] <= min(asof_dt, horizon_end)
    ]
    fill_point = None
    saw_gap_above = False
    for point in eligible_points:
        if point["dt"] > entry_expiry:
            break
        if point["price"] > zone_high:
            saw_gap_above = True
            continue
        if trigger <= point["price"] <= zone_high and point["price"] >= zone_low:
            fill_point = point
            break

    if not fill_point:
        if asof_dt < entry_expiry:
            return _result(
                plan,
                "waiting_entry",
                events,
                terminal=False,
                exit_reason=None,
            )
        state = "canceled_gap_above_zone" if saw_gap_above else "canceled_no_fill"
        reason = "entry_gap_above_zone" if saw_gap_above else "entry_timeout"
        events.append(_event("entry_canceled", _format_dt(entry_expiry), None, state, note=reason))
        return _result(
            plan,
            state,
            events,
            terminal=True,
            exit_reason=reason,
        )

    if holding_clock == "entry_fill":
        horizon_end = fill_point["dt"] + timedelta(minutes=max_hold)
        eligible_points = [
            point for point in points
            if fill_point["dt"] <= point["dt"] <= min(asof_dt, horizon_end)
        ]

    rates = _cost_rates(plan)
    raw_entry = fill_point["price"]
    fill_price = raw_entry * (1.0 + rates["slippage_each_side_pct"] / 100.0)
    initial_cost = fill_price * (1.0 + rates["buy_cost_pct"] / 100.0)
    events.append(_event(
        "entry_filled",
        fill_point["at"],
        fill_price,
        "open",
        fraction=1.0,
        note="stored_price_in_entry_zone",
    ))

    remaining = 1.0
    exit_legs = []
    tp1_hit = False
    high_water = raw_entry
    active_stop = stop
    post_fill_points = [
        point for point in eligible_points
        if point["dt"] >= fill_point["dt"]
    ]
    terminal_state = None
    exit_reason = None

    for point in post_fill_points[1:]:
        price = point["price"]
        high_water = max(high_water, price)
        if not tp1_hit:
            if price <= active_stop:
                _append_exit(exit_legs, events, point, price, remaining, "stop", rates)
                remaining = 0.0
                terminal_state = "closed_stop"
                exit_reason = "initial_stop"
                break
            if price >= target_1:
                fraction = min(0.50, remaining)
                _append_exit(exit_legs, events, point, price, fraction, "tp1", rates)
                remaining -= fraction
                tp1_hit = True
                active_stop = max(active_stop, breakeven_stop or active_stop)
                events.append(_event(
                    "stop_moved",
                    point["at"],
                    active_stop,
                    "partial",
                    note="cost_breakeven_after_tp1",
                ))

        if tp1_hit and remaining > 0:
            if price >= target_2:
                _append_exit(exit_legs, events, point, price, remaining, "tp2", rates)
                remaining = 0.0
                terminal_state = "closed_tp2"
                exit_reason = "target_2"
                break
            trailing_stop = high_water * (1.0 - trailing_pct / 100.0)
            active_stop = max(active_stop, trailing_stop)
            if price <= active_stop:
                reason = "breakeven" if active_stop <= (breakeven_stop or active_stop) else "trailing"
                _append_exit(exit_legs, events, point, price, remaining, reason, rates)
                remaining = 0.0
                terminal_state = (
                    "closed_breakeven" if reason == "breakeven" else "closed_trailing"
                )
                exit_reason = reason
                break

    if remaining > 0 and asof_dt >= horizon_end:
        timeout_points = [
            point for point in post_fill_points
            if point["dt"] <= horizon_end
        ]
        timeout_point = timeout_points[-1] if timeout_points else fill_point
        _append_exit(
            exit_legs,
            events,
            timeout_point,
            timeout_point["price"],
            remaining,
            "timeout",
            rates,
        )
        remaining = 0.0
        terminal_state = "closed_timeout"
        exit_reason = "max_holding_time"

    status = terminal_state or ("partial" if tp1_hit else "open")
    # Closed-position excursions must not include prices after the actual exit.
    excursion_end = _parse_dt(exit_legs[-1]["at"]) if remaining == 0 and exit_legs else None
    raw_prices = [
        point["price"] for point in post_fill_points
        if excursion_end is None or point["dt"] <= excursion_end
    ] or [raw_entry]
    mfe_pct = (max(raw_prices) / raw_entry - 1.0) * 100.0
    mae_pct = (min(raw_prices) / raw_entry - 1.0) * 100.0
    latest_point = post_fill_points[-1] if post_fill_points else fill_point
    performance = _performance(
        initial_cost=initial_cost,
        fill_price=fill_price,
        exit_legs=exit_legs,
        remaining=remaining,
        mark_price=latest_point["price"],
        rates=rates,
    )
    result = _result(
        plan,
        status,
        events,
        terminal=status in TERMINAL_STATES,
        exit_reason=exit_reason,
    )
    result.update({
        "entry_filled_at": fill_point["at"],
        "holding_clock": holding_clock,
        "holding_deadline": _format_dt(horizon_end),
        "entry_price": round(fill_price, 6),
        "raw_entry_price": round(raw_entry, 6),
        "remaining_fraction": round(remaining, 6),
        "average_exit_price": _weighted_exit_price(exit_legs),
        "exit_at": exit_legs[-1]["at"] if remaining == 0 and exit_legs else None,
        "active_stop_price": round(active_stop, 6),
        "tp1_hit": tp1_hit,
        "mfe_pct": round(mfe_pct, 4),
        "mae_pct": round(mae_pct, 4),
        "exit_legs": exit_legs,
        "cost_rates": rates,
    })
    result.update(performance)
    return result


def build_readiness_report(
    rows,
    min_closed=100,
    min_recent=30,
    min_profit_factor=1.20,
    max_drawdown_floor_pct=-10.0,
):
    """Return a strict shadow-readiness report from closed paper executions."""
    normalized = [dict(row) for row in rows or []]
    normalized.sort(key=lambda item: item.get("exit_at") or item.get("updated_at") or "")
    candidates = [
        row for row in normalized
        if row.get("status") not in ("rejected_by_guard",)
    ]
    filled = [row for row in candidates if row.get("entry_filled_at")]
    closed = [
        row for row in filled
        if row.get("status") in TERMINAL_STATES
        and row.get("net_return_pct") is not None
    ]
    recent = closed[-int(min_recent):]
    returns = [_number(row.get("net_return_pct"), 0.0) for row in closed]
    recent_returns = [_number(row.get("net_return_pct"), 0.0) for row in recent]
    expectancy = _avg(returns)
    recent_expectancy = _avg(recent_returns)
    profit_factor = _profit_factor(returns)
    ci_low, ci_high = _mean_ci95(returns)
    max_drawdown = _max_drawdown_pct(returns)
    checks = [
        _check("closed_sample", len(closed) >= int(min_closed), len(closed), min_closed),
        _check("recent_sample", len(recent) >= int(min_recent), len(recent), min_recent),
        _check("net_expectancy", expectancy is not None and expectancy > 0, expectancy, "> 0"),
        _check(
            "recent_net_expectancy",
            recent_expectancy is not None and recent_expectancy > 0,
            recent_expectancy,
            "> 0",
        ),
        _check(
            "profit_factor",
            profit_factor is not None and profit_factor >= float(min_profit_factor),
            profit_factor,
            min_profit_factor,
        ),
        _check(
            "mean_ci95_low",
            ci_low is not None and ci_low > 0,
            ci_low,
            "> 0",
        ),
        _check(
            "max_drawdown",
            max_drawdown is not None and max_drawdown >= float(max_drawdown_floor_pct),
            max_drawdown,
            ">= {}".format(max_drawdown_floor_pct),
        ),
    ]
    passed = bool(checks) and all(item["passed"] for item in checks)
    if len(closed) < int(min_closed) or len(recent) < int(min_recent):
        readiness = "paper_collecting"
    elif passed:
        readiness = "shadow_ready"
    else:
        readiness = "edge_not_proven"
    status_counts = {}
    for row in normalized:
        key = row.get("status") or "unknown"
        status_counts[key] = status_counts.get(key, 0) + 1
    return {
        "schema": "deployment_readiness_v1",
        "generated_at": _format_dt(datetime.now()),
        "readiness": readiness,
        "live_order_enabled": False,
        "live_deployment_ready": False,
        "paper_or_shadow_only": True,
        "assumption_version": ASSUMPTION_VERSION,
        "counts": {
            "all": len(normalized),
            "candidates": len(candidates),
            "filled": len(filled),
            "closed": len(closed),
            "recent_closed": len(recent),
            "status": status_counts,
        },
        "metrics": {
            "fill_rate_pct": _rate(len(filled), len(candidates)),
            "win_rate_pct": _rate(len([value for value in returns if value > 0]), len(returns)),
            "net_expectancy_pct": expectancy,
            "recent_net_expectancy_pct": recent_expectancy,
            "profit_factor": profit_factor,
            "mean_ci95_low_pct": ci_low,
            "mean_ci95_high_pct": ci_high,
            "max_drawdown_pct": max_drawdown,
            "best_trade_pct": max(returns) if returns else None,
            "worst_trade_pct": min(returns) if returns else None,
        },
        "checks": checks,
        "recommendation": (
            "Continue shadow validation; live orders remain disabled."
            if readiness == "shadow_ready"
            else "Keep collecting paper executions; live orders remain disabled."
        ),
    }


def _result(plan, status, events, terminal, exit_reason):
    validation = plan.get("validation") or {}
    guard = validation.get("adaptive_guard") or {}
    return {
        "schema": SCHEMA,
        "assumption_version": ASSUMPTION_VERSION,
        "status": status,
        "terminal": bool(terminal),
        "exit_reason": exit_reason,
        "orders_called": False,
        "live_order_enabled": False,
        "risk_multiplier": (plan.get("risk") or {}).get("paper_risk_multiplier"),
        "adaptive_state": guard.get("state"),
        "events": events,
        "assumptions": {
            "entry": "Fill only at stored prices inside the configured trigger/zone.",
            "gap": "Do not chase prices above the entry zone.",
            "slippage": "Apply configured slippage to each simulated fill.",
            "costs": "Apply configured commissions, tax, regulatory, and FX buffers.",
            "path": "Use stored sequential prices; no intratick high/low is invented.",
            "position_size": "Use one normalized unit; no account sizing or broker order.",
        },
    }


def _append_exit(exit_legs, events, point, raw_price, fraction, reason, rates):
    fill_price = raw_price * (1.0 - rates["slippage_each_side_pct"] / 100.0)
    leg = {
        "at": point["at"],
        "raw_price": round(raw_price, 6),
        "fill_price": round(fill_price, 6),
        "fraction": round(fraction, 6),
        "reason": reason,
    }
    exit_legs.append(leg)
    events.append(_event(
        "position_exit",
        point["at"],
        fill_price,
        "partial" if fraction < 1.0 else "closed",
        fraction=fraction,
        note=reason,
    ))


def _performance(initial_cost, fill_price, exit_legs, remaining, mark_price, rates):
    gross_proceeds = sum(leg["fill_price"] * leg["fraction"] for leg in exit_legs)
    net_proceeds = sum(
        leg["fill_price"]
        * leg["fraction"]
        * (1.0 - rates["sell_cost_pct"] / 100.0)
        for leg in exit_legs
    )
    if remaining > 0:
        mark_fill = mark_price * (1.0 - rates["slippage_each_side_pct"] / 100.0)
        gross_proceeds += mark_fill * remaining
        net_proceeds += (
            mark_fill
            * remaining
            * (1.0 - rates["sell_cost_pct"] / 100.0)
        )
    gross_return = (gross_proceeds / fill_price - 1.0) * 100.0
    net_return = (net_proceeds / initial_cost - 1.0) * 100.0
    return {
        "gross_return_pct": round(gross_return, 4),
        "net_return_pct": round(net_return, 4),
        "net_pnl_per_unit": round(net_proceeds - initial_cost, 6),
        "cost_model_applied": True,
    }


def _cost_rates(plan):
    cost = plan.get("cost_model") or {}
    slippage = _number(cost.get("slippage_each_side_pct"), 0.0) or 0.0
    fx = _number(cost.get("fx_round_trip_buffer_pct"), 0.0) or 0.0
    buy = _first_number(cost, ("buy_fee_pct", "buy_commission_pct"), 0.0)
    sell = _first_number(cost, ("sell_fee_pct", "sell_commission_pct"), 0.0)
    sell += _number(cost.get("sell_tax_pct"), 0.0) or 0.0
    sell += _number(cost.get("sell_regulatory_buffer_pct"), 0.0) or 0.0
    buy += fx / 2.0
    sell += fx / 2.0
    return {
        "buy_cost_pct": round(buy, 6),
        "sell_cost_pct": round(sell, 6),
        "slippage_each_side_pct": round(slippage, 6),
    }


def _normalize_points(points):
    result = []
    for item in points or []:
        at = item.get("at") or item.get("collected_at") or item.get("received_at")
        dt = _parse_dt(at)
        price = _number(item.get("price"))
        if dt and price is not None and price > 0:
            result.append({"at": str(at), "dt": dt, "price": price})
    result.sort(key=lambda item: item["dt"])
    return result


def _event(event_type, at, price, state, fraction=None, note=None):
    return {
        "event_type": event_type,
        "event_at": str(at) if at is not None else None,
        "price": round(price, 6) if price is not None else None,
        "state": state,
        "fraction": fraction,
        "note": note,
    }


def _weighted_exit_price(legs):
    total = sum(_number(item.get("fraction"), 0.0) for item in legs)
    if total <= 0:
        return None
    value = sum(
        _number(item.get("fill_price"), 0.0)
        * _number(item.get("fraction"), 0.0)
        for item in legs
    ) / total
    return round(value, 6)


def _parse_dt(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not value:
        return None
    text = str(value).strip().replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1]
    if "+" in text[10:]:
        text = text.split("+", 1)[0]
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _format_dt(value):
    dt = _parse_dt(value)
    if not dt:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


def _first_number(mapping, keys, default=None):
    for key in keys:
        value = _number(mapping.get(key))
        if value is not None:
            return value
    return default


def _number(value, default=None):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _avg(values):
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _profit_factor(values):
    if not values:
        return None
    gains = sum(value for value in values if value > 0)
    losses = abs(sum(value for value in values if value < 0))
    if losses == 0:
        return 999.0 if gains > 0 else None
    return round(gains / losses, 4)


def _mean_ci95(values):
    if len(values) < 2:
        return None, None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    margin = 1.96 * math.sqrt(variance) / math.sqrt(len(values))
    return round(mean - margin, 4), round(mean + margin, 4)


def _max_drawdown_pct(returns):
    if not returns:
        return None
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= max(0.000001, 1.0 + value / 100.0)
        peak = max(peak, equity)
        drawdown = (equity / peak - 1.0) * 100.0
        worst = min(worst, drawdown)
    return round(worst, 4)


def _rate(numerator, denominator):
    if not denominator:
        return None
    return round(float(numerator) / float(denominator) * 100.0, 2)


def _check(name, passed, actual, required):
    return {
        "name": name,
        "passed": bool(passed),
        "actual": actual,
        "required": required,
    }
