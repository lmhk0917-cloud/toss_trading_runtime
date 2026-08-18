"""Quant-style paper feedback for the Toss read-only runtime.

This mirrors the useful parts of Kiwoom Core Quant feedback: cost-aware
expectancy, decision buckets, and clustered samples. It never places orders.
"""

import json
from datetime import datetime, timedelta

try:
    from . import config
    from .adaptive_guard import build_adaptive_guard
except ImportError:  # pragma: no cover
    import config
    from adaptive_guard import build_adaptive_guard


DEFAULT_DAYS = 5
DEFAULT_MIN_SAMPLE = 5
DEFAULT_CLUSTER_WINDOW_MINUTES = 10
SHORT_WINDOW_DAYS = 5
MEDIUM_WINDOW_DAYS = 20
MEDIUM_MIN_SAMPLE = 20
ROUND_TRIP_COST_PCT = getattr(config, "PAPER_ROUND_TRIP_COST_PCT", 0.0)
LONG_DECISIONS = set(("INTEREST", "BUY", "LONG", "관심"))
CAUTION_DECISIONS = set(("RISK", "AVOID", "REDUCE", "SELL", "위험", "회피"))


def build_quant_feedback_snapshot(conn, symbols=None, days=DEFAULT_DAYS, min_sample=DEFAULT_MIN_SAMPLE, as_of=None):
    symbols = [str(item).strip().upper() for item in symbols or [] if str(item).strip()]
    as_of_dt = _parse_dt(as_of) or datetime.now()
    rows = _load_rows(conn, symbols=symbols, days=days, as_of=as_of_dt)
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

    by_regime = []
    for regime in sorted(set(row.get("regime") or "UNKNOWN" for row in rows)):
        block = _metric_block([row for row in rows if (row.get("regime") or "UNKNOWN") == regime])
        block["regime"] = regime
        block["guidance"] = _guidance(block, min_sample=min_sample)
        by_regime.append(block)
    by_regime.sort(
        key=lambda item: (
            item.get("directional_evaluated_count") or item.get("evaluated_count") or 0,
            item.get("avg_net_directional_return_pct")
            if item.get("avg_net_directional_return_pct") is not None
            else -999,
        ),
        reverse=True,
    )

    symbol_guidance = {}
    for item in by_symbol:
        symbol_guidance[item["symbol"]] = _guidance(item, min_sample=min_sample)

    return {
        "schema": "toss_quant_feedback_v1",
        "generated_at": _format_dt(as_of_dt),
        "window_days": int(days or 0),
        "window_start": _window_start(days, as_of=as_of_dt),
        "window_end": _format_dt(as_of_dt),
        "min_sample": int(min_sample),
        "round_trip_cost_pct": round(float(ROUND_TRIP_COST_PCT or 0.0), 4),
        "cost_source": getattr(config, "PAPER_COST_SOURCE", "configured paper round-trip cost"),
        "overview": overview,
        "by_symbol": by_symbol,
        "by_decision": by_decision,
        "by_regime": by_regime,
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


def build_quant_feedback_windows(
    conn,
    symbols=None,
    short_days=SHORT_WINDOW_DAYS,
    medium_days=MEDIUM_WINDOW_DAYS,
    short_min_sample=DEFAULT_MIN_SAMPLE,
    medium_min_sample=MEDIUM_MIN_SAMPLE,
):
    """Build short and medium feedback from the same evaluated paper rows."""
    return {
        "schema": "toss_quant_feedback_windows_v1",
        "generated_at": _now(),
        "short_5d": build_quant_feedback_snapshot(
            conn,
            symbols=symbols,
            days=short_days,
            min_sample=short_min_sample,
        ),
        "medium_20d": build_quant_feedback_snapshot(
            conn,
            symbols=symbols,
            days=medium_days,
            min_sample=medium_min_sample,
        ),
    }


def attach_quant_feedback_windows_to_evidence(evidence, windows):
    """Attach per-symbol windows and their conservative adaptive guard."""
    windows = windows or {}
    evidence["quant_feedback_windows"] = {
        "schema": windows.get("schema"),
        "generated_at": windows.get("generated_at"),
        "short_5d": compact_quant_feedback(windows.get("short_5d") or {}),
        "medium_20d": compact_quant_feedback(windows.get("medium_20d") or {}),
    }
    short = windows.get("short_5d") or {}
    medium = windows.get("medium_20d") or {}
    short_by_symbol = {
        item.get("symbol"): item
        for item in short.get("by_symbol") or []
        if item.get("symbol")
    }
    medium_by_symbol = {
        item.get("symbol"): item
        for item in medium.get("by_symbol") or []
        if item.get("symbol")
    }
    short_guidance = short.get("symbol_guidance") or {}
    medium_guidance = medium.get("symbol_guidance") or {}
    for symbol, item in (evidence.get("symbol_evidence") or {}).items():
        symbol_windows = {
            "short_5d": {
                "window_days": short.get("window_days"),
                "metrics": short_by_symbol.get(symbol, _empty_symbol_block(symbol)),
                "guidance": short_guidance.get(symbol, _no_feedback_guidance()),
            },
            "medium_20d": {
                "window_days": medium.get("window_days"),
                "metrics": medium_by_symbol.get(symbol, _empty_symbol_block(symbol)),
                "guidance": medium_guidance.get(symbol, _no_feedback_guidance()),
            },
        }
        item["quant_feedback_windows"] = symbol_windows
        item["adaptive_guard"] = build_adaptive_guard(
            symbol_windows["short_5d"],
            symbol_windows["medium_20d"],
            min_short=int(short.get("min_sample") or DEFAULT_MIN_SAMPLE),
            min_medium=int(medium.get("min_sample") or MEDIUM_MIN_SAMPLE),
        )
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
        "by_regime": [_compact_metric(item) for item in (snapshot.get("by_regime") or [])[:max_items]],
        "symbol_guidance": {symbol: (snapshot.get("symbol_guidance") or {}).get(symbol) for symbol in symbols if symbol in (snapshot.get("symbol_guidance") or {})},
        "guidance": snapshot.get("guidance"),
    }


def _load_rows(conn, symbols=None, days=DEFAULT_DAYS, as_of=None):
    clauses = ["p.status = 'evaluated'"]
    params = []
    window_start = _window_start(days, as_of=as_of)
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
    raw_rows = conn.execute(sql, params).fetchall()
    regimes = _load_analysis_regimes(conn, [row["analysis_id"] for row in raw_rows])
    result = []
    for row in raw_rows:
        item = dict(row)
        item["symbol"] = str(item.get("symbol") or "").upper()
        item["decision"] = item.get("final_decision") or "UNKNOWN"
        item["regime"] = regimes.get(item.get("analysis_id"), "UNKNOWN")
        item["return_pct"] = _to_float(item.get("result_return_pct"))
        item["net_return_pct"] = None if item["return_pct"] is None else item["return_pct"] - float(ROUND_TRIP_COST_PCT or 0.0)
        item["decision_side"] = _decision_side(item["decision"])
        item["directional_return_pct"] = _directional_return(item["return_pct"], item["decision_side"])
        item["net_directional_return_pct"] = (
            None
            if item["directional_return_pct"] is None
            else item["directional_return_pct"] - float(ROUND_TRIP_COST_PCT or 0.0)
        )
        item["mfe_pct"] = _to_float(item.get("max_return_pct"))
        item["mae_pct"] = _to_float(item.get("min_return_pct"))
        result.append(item)
    return result


def _load_analysis_regimes(conn, analysis_ids):
    ids = sorted(set(item for item in analysis_ids if item is not None))
    regimes = {}
    for start in range(0, len(ids), 500):
        batch = ids[start:start + 500]
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            "SELECT id, evidence_json FROM analysis_results WHERE id IN ({})".format(placeholders),
            batch,
        ).fetchall()
        for row in rows:
            regimes[row["id"]] = _relationship_regime(row["evidence_json"])
    return regimes


def _relationship_regime(raw_json):
    try:
        payload = json.loads(raw_json or "{}")
    except (TypeError, ValueError):
        return "UNKNOWN"
    relationship = payload.get("market_relationship") if isinstance(payload, dict) else None
    if not isinstance(relationship, dict):
        return "UNKNOWN"
    return str(relationship.get("relationship_regime") or "UNKNOWN")


def _decision_side(decision):
    value = str(decision or "").strip().upper()
    if value in LONG_DECISIONS:
        return "long_candidate"
    if value in CAUTION_DECISIONS:
        return "caution_or_avoid"
    return "neutral"


def _directional_return(value, side):
    if value is None:
        return None
    if side == "long_candidate":
        return value
    if side == "caution_or_avoid":
        return -value
    return None


def _metric_block(rows):
    returns = _values(rows, "return_pct")
    net_returns = _values(rows, "net_return_pct")
    mfe = _values(rows, "mfe_pct")
    mae = _values(rows, "mae_pct")
    clusters = _cluster_representatives(rows)
    cluster_returns = _values(clusters, "return_pct")
    cluster_net = _values(clusters, "net_return_pct")
    directional_returns = _values(rows, "directional_return_pct")
    net_directional_returns = _values(rows, "net_directional_return_pct")
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
        "directional_evaluated_count": len(directional_returns),
        "avg_directional_return_pct": _avg(directional_returns),
        "avg_net_directional_return_pct": _avg(net_directional_returns),
        "directional_success_pct": _win_rate(directional_returns),
        "net_directional_success_pct": _win_rate(net_directional_returns),
        "directional_profit_factor": _profit_factor(directional_returns),
        "net_directional_profit_factor": _profit_factor(net_directional_returns),
        "directional_metric_basis": "long=raw_return,caution_or_avoid=-raw_return; net subtracts round-trip cost",
    }


def _guidance(metrics, min_sample=DEFAULT_MIN_SAMPLE):
    directional_count = int(metrics.get("directional_evaluated_count") or 0)
    if directional_count >= int(min_sample):
        count = directional_count
        expectancy = metrics.get("avg_net_directional_return_pct")
        win_rate = metrics.get("net_directional_success_pct")
        profit_factor = metrics.get("net_directional_profit_factor") or 0
        basis = "net_directional"
    else:
        count = int(metrics.get("evaluated_count") or 0)
        expectancy = metrics.get("expectancy_pct")
        win_rate = metrics.get("net_win_rate_pct")
        profit_factor = metrics.get("net_profit_factor") or 0
        basis = "net_raw"
    adverse = metrics.get("adverse_path_rate_pct") or 0
    if count < int(min_sample):
        return {"label": "sample_too_small", "summary": "Do not tune from this sample yet.", "sample_count": count, "metric_basis": basis}
    if expectancy is not None and expectancy > 0 and (win_rate or 0) >= 50 and profit_factor >= 1:
        return {"label": "positive_expectancy", "summary": "Feedback supports keeping this setup when live evidence matches.", "sample_count": count, "metric_basis": basis}
    if expectancy is not None and (expectancy < 0 or (win_rate is not None and win_rate < 45) or adverse >= 50):
        return {"label": "negative_expectancy", "summary": "Require stronger confirmation or lower GPT confidence for this bucket.", "sample_count": count, "metric_basis": basis}
    return {"label": "mixed_expectancy", "summary": "Keep as watch-only until the next feedback window clarifies.", "sample_count": count, "metric_basis": basis}


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
        "regime", "directional_evaluated_count", "avg_directional_return_pct",
        "avg_net_directional_return_pct", "directional_success_pct",
        "net_directional_success_pct", "directional_profit_factor",
        "net_directional_profit_factor", "directional_metric_basis",
    ]
    return {key: item.get(key) for key in keys if key in item}


def _empty_symbol_block(symbol):
    block = _metric_block([])
    block["symbol"] = symbol
    return block


def _no_feedback_guidance():
    return {
        "label": "sample_too_small",
        "summary": "No evaluated paper feedback yet.",
        "sample_count": 0,
    }

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


def _window_start(days, as_of=None):
    if not days:
        return None
    return _format_dt((as_of or datetime.now()) - timedelta(days=int(days)))


def _format_dt(value):
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")


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
