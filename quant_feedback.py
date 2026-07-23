"""Quant-style paper feedback for the Toss read-only runtime.

This mirrors the useful parts of Kiwoom Core Quant feedback: cost-aware
expectancy, decision buckets, and clustered samples. It never places orders.
"""

from datetime import datetime, timedelta

try:
    from . import config
except ImportError:  # pragma: no cover
    import config


DEFAULT_DAYS = 5
DEFAULT_MIN_SAMPLE = 5
DEFAULT_CLUSTER_WINDOW_MINUTES = 10
ROUND_TRIP_COST_PCT = getattr(config, "PAPER_ROUND_TRIP_COST_PCT", 0.0)


def build_quant_feedback_snapshot(conn, symbols=None, days=DEFAULT_DAYS, min_sample=DEFAULT_MIN_SAMPLE):
    symbols = [str(item).strip().upper() for item in symbols or [] if str(item).strip()]
    rows = _load_rows(conn, symbols=symbols, days=days)
    overview = _metric_block(rows)
    by_symbol = []
    for symbol in sorted(set(row["symbol"] for row in rows)):
        block = _metric_block([row for row in rows if row["symbol"] == symbol])
        block["symbol"] = symbol
        by_symbol.append(block)
    by_symbol.sort(key=lambda item: (item.get("evaluated_count") or 0, item.get("expectancy_pct") or -999), reverse=True)

    by_decision = []
    for decision in sorted(set(row.get("decision") or "UNKNOWN" for row in rows)):
        block = _metric_block([row for row in rows if (row.get("decision") or "UNKNOWN") == decision])
        block["decision"] = decision
        by_decision.append(block)
    by_decision.sort(key=lambda item: (item.get("evaluated_count") or 0, item.get("expectancy_pct") or -999), reverse=True)

    symbol_guidance = {}
    for item in by_symbol:
        symbol_guidance[item["symbol"]] = _guidance(item, min_sample=min_sample)

    return {
        "schema": "toss_quant_feedback_v1",
        "generated_at": _now(),
        "window_days": int(days or 0),
        "window_start": _window_start(days),
        "window_end": _now(),
        "min_sample": int(min_sample),
        "round_trip_cost_pct": round(float(ROUND_TRIP_COST_PCT or 0.0), 4),
        "cost_source": "TOSSINVEST_PAPER_ROUND_TRIP_COST_PCT env; default 0.0 when unset",
        "overview": overview,
        "by_symbol": by_symbol,
        "by_decision": by_decision,
        "symbol_guidance": symbol_guidance,
        "guidance": _guidance(overview, min_sample=min_sample),
    }


def attach_quant_feedback_to_evidence(evidence, snapshot):
    snapshot = snapshot or {}
    evidence["quant_feedback"] = snapshot
    by_symbol = {item.get("symbol"): item for item in snapshot.get("by_symbol") or [] if item.get("symbol")}
    guidance = snapshot.get("symbol_guidance") or {}
    for symbol, item in (evidence.get("symbol_evidence") or {}).items():
        item["quant_feedback"] = by_symbol.get(symbol, _empty_symbol_block(symbol))
        item["quant_feedback_guidance"] = guidance.get(symbol, {"label": "no_feedback", "summary": "No evaluated paper feedback yet."})
    return evidence


def compact_quant_feedback(snapshot, symbols=None, max_items=8):
    snapshot = snapshot or {}
    symbols = [str(item).strip().upper() for item in symbols or [] if str(item).strip()]
    symbol_set = set(symbols)
    by_symbol = []
    for item in snapshot.get("by_symbol") or []:
        if symbol_set and item.get("symbol") not in symbol_set:
            continue
        by_symbol.append(_compact_metric(item))
        if len(by_symbol) >= max_items:
            break
    return {
        "schema": snapshot.get("schema"),
        "generated_at": snapshot.get("generated_at"),
        "window_days": snapshot.get("window_days"),
        "round_trip_cost_pct": snapshot.get("round_trip_cost_pct"),
        "cost_source": snapshot.get("cost_source"),
        "overview": _compact_metric(snapshot.get("overview") or {}),
        "by_symbol": by_symbol,
        "by_decision": [_compact_metric(item) for item in (snapshot.get("by_decision") or [])[:max_items]],
        "symbol_guidance": {symbol: (snapshot.get("symbol_guidance") or {}).get(symbol) for symbol in symbols if symbol in (snapshot.get("symbol_guidance") or {})},
        "guidance": snapshot.get("guidance"),
    }


def _load_rows(conn, symbols=None, days=DEFAULT_DAYS):
    clauses = ["p.status = 'evaluated'"]
    params = []
    window_start = _window_start(days)
    if window_start:
        clauses.append("p.evaluated_at >= ?")
        params.append(window_start)
    if symbols:
        clauses.append("p.symbol IN ({})".format(",".join("?" for _ in symbols)))
        params.extend(symbols)
    sql = """
        SELECT
            p.id,
            p.created_at,
            p.evaluated_at,
            p.analysis_id,
            p.symbol,
            p.horizon_min,
            p.result_return_pct,
            p.max_return_pct,
            p.min_return_pct,
            p.outcome,
            s.final_decision,
            s.interest_score,
            s.risk_level,
            s.confidence
        FROM paper_trade_candidates p
        LEFT JOIN structured_analysis s
            ON s.analysis_id = p.analysis_id AND s.symbol = p.symbol
        WHERE {where_sql}
        ORDER BY p.created_at ASC, p.id ASC
    """.format(where_sql=" AND ".join(clauses))
    result = []
    for row in conn.execute(sql, params).fetchall():
        item = dict(row)
        item["symbol"] = str(item.get("symbol") or "").upper()
        item["decision"] = item.get("final_decision") or "UNKNOWN"
        item["return_pct"] = _to_float(item.get("result_return_pct"))
        item["net_return_pct"] = None if item["return_pct"] is None else item["return_pct"] - float(ROUND_TRIP_COST_PCT or 0.0)
        item["mfe_pct"] = _to_float(item.get("max_return_pct"))
        item["mae_pct"] = _to_float(item.get("min_return_pct"))
        result.append(item)
    return result


def _metric_block(rows):
    returns = _values(rows, "return_pct")
    net_returns = _values(rows, "net_return_pct")
    mfe = _values(rows, "mfe_pct")
    mae = _values(rows, "mae_pct")
    clusters = _cluster_representatives(rows)
    cluster_returns = _values(clusters, "return_pct")
    cluster_net = _values(clusters, "net_return_pct")
    return {
        "evaluated_count": len(rows),
        "avg_return_pct": _avg(returns),
        "avg_net_return_pct": _avg(net_returns),
        "expectancy_pct": _avg(net_returns),
        "win_rate_pct": _win_rate(returns),
        "net_win_rate_pct": _win_rate(net_returns),
        "profit_factor": _profit_factor(returns),
        "net_profit_factor": _profit_factor(net_returns),
        "avg_mfe_pct": _avg(mfe),
        "avg_mae_pct": _avg(mae),
        "adverse_path_rate_pct": _rate([value <= -0.4 for value in mae]),
        "outcome_counts": _counts(rows, "outcome"),
        "cluster_window_minutes": DEFAULT_CLUSTER_WINDOW_MINUTES,
        "cluster_count": len(clusters),
        "evaluated_cluster_count": len(clusters),
        "avg_cluster_return_pct": _avg(cluster_returns),
        "avg_cluster_net_return_pct": _avg(cluster_net),
        "cluster_win_rate_pct": _win_rate(cluster_returns),
        "cluster_profit_factor": _profit_factor(cluster_returns),
    }


def _guidance(metrics, min_sample=DEFAULT_MIN_SAMPLE):
    count = int(metrics.get("evaluated_count") or 0)
    expectancy = metrics.get("expectancy_pct")
    win_rate = metrics.get("net_win_rate_pct")
    profit_factor = metrics.get("net_profit_factor") or 0
    adverse = metrics.get("adverse_path_rate_pct") or 0
    if count < int(min_sample):
        return {"label": "sample_too_small", "summary": "Do not tune from this sample yet.", "sample_count": count}
    if expectancy is not None and expectancy > 0 and (win_rate or 0) >= 50 and profit_factor >= 1:
        return {"label": "positive_expectancy", "summary": "Feedback supports keeping this setup when live evidence matches.", "sample_count": count}
    if expectancy is not None and (expectancy < 0 or (win_rate is not None and win_rate < 45) or adverse >= 50):
        return {"label": "negative_expectancy", "summary": "Require stronger confirmation or lower GPT confidence for this bucket.", "sample_count": count}
    return {"label": "mixed_expectancy", "summary": "Keep as watch-only until the next feedback window clarifies.", "sample_count": count}


def _cluster_representatives(rows, window_minutes=DEFAULT_CLUSTER_WINDOW_MINUTES):
    clusters = []
    active = {}
    window_delta = timedelta(minutes=window_minutes)
    for row in sorted(rows, key=lambda item: (item.get("symbol") or "", item.get("decision") or "", item.get("horizon_min") or 0, item.get("created_at") or "")):
        created_at = _parse_dt(row.get("created_at"))
        if not created_at:
            continue
        key = (row.get("symbol"), row.get("decision"), row.get("horizon_min"))
        current = active.get(key)
        if current is None or created_at - current["started_at"] > window_delta:
            current = {"started_at": created_at, "rows": []}
            active[key] = current
            clusters.append(current)
        current["rows"].append(row)
    representatives = []
    for cluster in clusters:
        representatives.append(cluster["rows"][0])
    return representatives


def _compact_metric(item):
    keys = [
        "symbol", "decision", "evaluated_count", "avg_return_pct", "avg_net_return_pct",
        "expectancy_pct", "win_rate_pct", "net_win_rate_pct", "profit_factor",
        "net_profit_factor", "avg_mfe_pct", "avg_mae_pct", "adverse_path_rate_pct",
        "cluster_count", "avg_cluster_net_return_pct", "cluster_win_rate_pct",
    ]
    return {key: item.get(key) for key in keys if key in item}


def _empty_symbol_block(symbol):
    block = _metric_block([])
    block["symbol"] = symbol
    return block


def _values(rows, key):
    return [float(row[key]) for row in rows if row.get(key) is not None]


def _avg(values):
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _win_rate(values):
    if not values:
        return None
    return round(len([value for value in values if value > 0]) / len(values) * 100.0, 2)


def _profit_factor(values):
    if not values:
        return None
    gains = sum(value for value in values if value > 0)
    losses = abs(sum(value for value in values if value < 0))
    if losses == 0:
        return 999.0 if gains > 0 else None
    return round(gains / losses, 4)


def _rate(values):
    if not values:
        return None
    return round(len([value for value in values if value]) / len(values) * 100.0, 2)


def _counts(rows, key):
    counts = {}
    for row in rows:
        value = row.get(key) or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts


def _window_start(days):
    if not days:
        return None
    return (datetime.now() - timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S.%f")


def _parse_dt(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            pass
    return None


def _to_float(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
