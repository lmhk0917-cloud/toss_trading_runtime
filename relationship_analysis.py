"""Relationship and lead-lag evidence between KR and US semiconductor themes."""

import json
import os
import sqlite3
from datetime import datetime

try:
    from . import config
except ImportError:  # pragma: no cover
    import config


DEFAULT_DOMESTIC_CODES = ("005930", "000660")
DEFAULT_US_SYMBOLS = ("NVDA", "MU", "QQQ", "SOXX")


def build_relationship_evidence(
    store,
    domestic_codes=None,
    us_symbols=None,
    min_samples=5,
    kiwoom_db_path=None,
):
    domestic_codes = [str(item).strip() for item in domestic_codes or DEFAULT_DOMESTIC_CODES if str(item).strip()]
    us_symbols = [str(item).strip().upper() for item in us_symbols or DEFAULT_US_SYMBOLS if str(item).strip()]
    observations = store.relationship_observations(domestic_codes=domestic_codes, us_symbols=us_symbols)
    kiwoom_daily = load_kiwoom_daily_returns(kiwoom_db_path or config.KIWOOM_PERSONAL_DB_PATH, domestic_codes)
    pair_results = _pair_results(observations, min_samples=min_samples, kiwoom_daily=kiwoom_daily)
    domestic_snapshot = store.domestic_snapshot(codes=domestic_codes)
    us_feedback = store.return_feedback_by_symbol()
    proxy = _proxy_alignment(domestic_snapshot, us_feedback, domestic_codes, us_symbols)
    data_quality = {
        "min_samples": int(min_samples),
        "paired_observation_count": len(observations),
        "daily_historical_observation_count": _count_daily_historical(observations),
        "kiwoom_daily_return_count": sum(len(value) for value in kiwoom_daily.values()),
        "has_paired_observations": bool(pair_results),
        "has_valid_pair_results": any(
            row.get("relationship_regime") != "insufficient_evidence"
            for row in pair_results
        ),
        "uses_proxy_alignment": not bool(pair_results),
        "warning": None,
        "resolution_warning": None,
    }
    if data_quality["daily_historical_observation_count"]:
        data_quality["resolution_warning"] = (
            "Some paired observations are daily historical close-to-close rows. "
            "Use them for long-horizon relationship checks only, not minute/tick timing."
        )
    if not data_quality["has_valid_pair_results"]:
        data_quality["warning"] = "insufficient paired KR-US observations; do not claim correlation strength"
    regime = _overall_regime(pair_results)
    return {
        "mode": "kr_us_semiconductor_relationship",
        "generated_at": _now(),
        "domestic_codes": domestic_codes,
        "us_symbols": us_symbols,
        "data_quality": data_quality,
        "relationship_regime": regime,
        "pairs": pair_results,
        "proxy_alignment": proxy,
        "interpretation_rules": [
            "Only pair results with paired_sample_count >= min_samples may be described as correlation evidence.",
            "Daily historical observations are not minute or tick evidence and must not be used for intraday timing.",
            "Proxy alignment is directional context, not correlation.",
            "When data_quality.warning is present, GPT must state that relationship strength is not proven.",
        ],
    }


def _pair_results(observations, min_samples, kiwoom_daily=None):
    kiwoom_daily = kiwoom_daily or {}
    grouped = {}
    for row in observations:
        key = (
            row.get("source_symbol"),
            row.get("target_symbol"),
            row.get("lag_label") or "same_session",
        )
        grouped.setdefault(key, []).append(row)
    results = []
    for (source_symbol, target_symbol, lag_label), rows in sorted(grouped.items()):
        source_values, target_values, direction = _analysis_return_vectors(rows)
        resolution = _resolution_summary(rows)
        corr = _pearson(source_values, target_values)
        regression = _regression_beta(source_values, target_values)
        directional = _directional_stats(source_values, target_values)
        lead_score = _lead_score(corr, regression, directional, len(rows), min_samples)
        gap_effect = _gap_effect(rows, source_symbol, kiwoom_daily)
        regime = _regime(corr, len(rows), min_samples)
        results.append({
            "source_symbol": source_symbol,
            "target_symbol": target_symbol,
            "analysis_direction": direction,
            "resolution": resolution,
            "lag_label": lag_label,
            "paired_sample_count": len(rows),
            "correlation": corr,
            "regression": regression,
            "directional_stats": directional,
            "lead_score": lead_score,
            "relationship_regime": regime,
            "avg_source_return_pct": round(sum(source_values) / len(source_values), 4) if source_values else 0.0,
            "avg_target_return_pct": round(sum(target_values) / len(target_values), 4) if target_values else 0.0,
            "latest_observed_at": max(str(row.get("observed_at") or "") for row in rows),
            "gap_effect": gap_effect,
        })
    return results


def _proxy_alignment(domestic_snapshot, us_feedback, domestic_codes, us_symbols):
    domestic = {}
    for row in domestic_snapshot or []:
        code = row.get("code")
        if code not in domestic_codes:
            continue
        feedback = row.get("feedback") or []
        samples = sum(_to_int(item.get("sample_count")) for item in feedback)
        weighted_return = sum(_to_float(item.get("avg_return_pct")) * _to_int(item.get("sample_count")) for item in feedback)
        domestic[code] = {
            "sample_count": samples,
            "avg_return_pct": round(weighted_return / samples, 4) if samples else 0.0,
            "signal": row.get("signal") or {},
        }
    us = {}
    for symbol in us_symbols:
        item = us_feedback.get(symbol) or {}
        us[symbol] = {
            "sample_count": _to_int(item.get("samples")),
            "avg_return_pct": _to_float(item.get("avg_return_pct")),
            "worst_path_return_pct": _to_float(item.get("worst_path_return_pct")),
        }
    return {
        "domestic": domestic,
        "us": us,
        "note": "Directional proxy only; not paired correlation evidence.",
    }


def _analysis_return_vectors(rows):
    source_values = []
    target_values = []
    direction = "source_return_to_target_return"
    for row in rows:
        payload = _parse_json(row.get("payload_json"))
        if payload.get("driver_return_pct") is not None and payload.get("response_return_pct") is not None:
            source_values.append(_to_float(payload.get("driver_return_pct")))
            target_values.append(_to_float(payload.get("response_return_pct")))
            direction = "{}_to_{}".format(
                payload.get("driver_symbol") or "driver",
                payload.get("response_symbol") or "response",
            )
        else:
            source_values.append(_to_float(row.get("source_return_pct")))
            target_values.append(_to_float(row.get("target_return_pct")))
    return source_values, target_values, direction


def _count_daily_historical(rows):
    return sum(1 for row in rows if _is_daily_historical(row))


def _is_daily_historical(row):
    payload = _parse_json(row.get("payload_json"))
    return (
        payload.get("observation_timeframe") == "1d"
        or payload.get("resolution") == "1d"
        or payload.get("data_source") == "yahoo_history_csv"
    )


def _resolution_summary(rows):
    daily_count = _count_daily_historical(rows)
    payload = _parse_json((rows[-1] if rows else {}).get("payload_json"))
    return {
        "timeframe": payload.get("observation_timeframe") or payload.get("resolution") or "unknown",
        "granularity": payload.get("observation_granularity") or "unknown",
        "daily_historical_count": daily_count,
        "intraday_count": max(0, len(rows) - daily_count),
        "intraday_source": bool(payload.get("intraday_source")) if payload else None,
        "tick_source": bool(payload.get("tick_source")) if payload else None,
        "warning": payload.get("resolution_warning") if daily_count else None,
    }


def _overall_regime(pair_results):
    strong = [row for row in pair_results if row.get("relationship_regime") == "strong"]
    weak = [row for row in pair_results if row.get("relationship_regime") == "weak"]
    mixed = [row for row in pair_results if row.get("relationship_regime") == "mixed"]
    if strong:
        return "strong"
    if mixed:
        return "mixed"
    if weak:
        return "weak"
    return "insufficient_evidence"


def _regime(correlation, sample_count, min_samples):
    if sample_count < int(min_samples) or correlation is None:
        return "insufficient_evidence"
    if abs(correlation) >= 0.65:
        return "strong"
    if abs(correlation) <= 0.25:
        return "weak"
    return "mixed"


def _regression_beta(left, right):
    if len(left) != len(right) or len(left) < 2:
        return {"alpha": None, "beta": None, "r_squared": None}
    avg_left = sum(left) / len(left)
    avg_right = sum(right) / len(right)
    left_var = sum((x - avg_left) ** 2 for x in left)
    if left_var <= 0:
        return {"alpha": None, "beta": None, "r_squared": None}
    covariance = sum((x - avg_left) * (y - avg_right) for x, y in zip(left, right))
    beta = covariance / left_var
    alpha = avg_right - beta * avg_left
    corr = _pearson(left, right)
    return {
        "model": "target_return = alpha + beta * source_return",
        "alpha": round(alpha, 6),
        "beta": round(beta, 6),
        "r_squared": round(corr * corr, 6) if corr is not None else None,
    }


def _directional_stats(left, right):
    return {
        "hit_ratio_up": _hit_ratio(left, right, threshold=0.0, direction="up"),
        "hit_ratio_down": _hit_ratio(left, right, threshold=0.0, direction="down"),
        "large_up": _conditional_return(left, right, threshold=2.0, direction="up"),
        "large_down": _conditional_return(left, right, threshold=2.0, direction="down"),
        "upside_corr": _conditional_corr(left, right, threshold=0.0, direction="up"),
        "downside_corr": _conditional_corr(left, right, threshold=0.0, direction="down"),
    }


def _hit_ratio(left, right, threshold=0.0, direction="up"):
    rows = _conditional_pairs(left, right, threshold=threshold, direction=direction)
    if not rows:
        return {"event_count": 0, "hit_ratio": None}
    if direction == "up":
        hits = [1 for _x, y in rows if y > 0]
    else:
        hits = [1 for _x, y in rows if y < 0]
    return {
        "event_count": len(rows),
        "hit_ratio": round(sum(hits) / len(rows), 4),
    }


def _conditional_return(left, right, threshold=0.0, direction="up"):
    rows = _conditional_pairs(left, right, threshold=threshold, direction=direction)
    target_values = [y for _x, y in rows]
    if not target_values:
        return {
            "event_count": 0,
            "avg_target_return_pct": None,
            "median_target_return_pct": None,
            "max_target_return_pct": None,
            "min_target_return_pct": None,
        }
    ordered = sorted(target_values)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return {
        "event_count": len(target_values),
        "avg_target_return_pct": round(sum(target_values) / len(target_values), 4),
        "median_target_return_pct": round(median, 4),
        "max_target_return_pct": round(max(target_values), 4),
        "min_target_return_pct": round(min(target_values), 4),
    }


def _conditional_corr(left, right, threshold=0.0, direction="up"):
    rows = _conditional_pairs(left, right, threshold=threshold, direction=direction)
    if len(rows) < 2:
        return {"event_count": len(rows), "correlation": None}
    return {
        "event_count": len(rows),
        "correlation": _pearson([x for x, _y in rows], [y for _x, y in rows]),
    }


def _conditional_pairs(left, right, threshold=0.0, direction="up"):
    rows = []
    for x, y in zip(left, right):
        if direction == "up" and x >= threshold:
            rows.append((x, y))
        elif direction == "down" and x <= -threshold:
            rows.append((x, y))
    return rows


def _lead_score(correlation, regression, directional, sample_count, min_samples):
    if sample_count < int(min_samples) or correlation is None:
        return None
    corr_score = min(1.0, abs(correlation))
    r2 = regression.get("r_squared")
    beta = regression.get("beta")
    beta_reliability = 0.0 if r2 is None or beta is None else min(1.0, abs(beta)) * min(1.0, r2)
    up = ((directional.get("hit_ratio_up") or {}).get("hit_ratio"))
    down = ((directional.get("hit_ratio_down") or {}).get("hit_ratio"))
    hit_values = [value for value in (up, down) if value is not None]
    hit_score = sum(hit_values) / len(hit_values) if hit_values else 0.0
    sample_score = min(1.0, sample_count / float(max(int(min_samples), 20)))
    score = (
        0.35 * corr_score
        + 0.25 * beta_reliability
        + 0.25 * hit_score
        + 0.15 * sample_score
    ) * 100
    return round(score, 2)


def _gap_effect(rows, domestic_symbol, kiwoom_daily):
    by_date = kiwoom_daily.get(str(domestic_symbol)) or {}
    matched = []
    for row in rows:
        date_key = _date_key(row.get("observed_at"))
        daily = by_date.get(date_key)
        if daily:
            item = dict(daily)
            item["source_return_pct"] = _to_float(row.get("source_return_pct"))
            item["target_return_pct"] = _to_float(row.get("target_return_pct"))
            matched.append(item)
    if not matched:
        return {
            "status": "not_available",
            "reason": "No matching Kiwoom daily open/close returns for relationship observation dates.",
        }
    open_values = [item["open_return_pct"] for item in matched if item.get("open_return_pct") is not None]
    close_values = [item["close_return_pct"] for item in matched if item.get("close_return_pct") is not None]
    intraday_values = [item["intraday_return_pct"] for item in matched if item.get("intraday_return_pct") is not None]
    source_values = [item["source_return_pct"] for item in matched]
    reversal_rows = [
        item for item in matched
        if item.get("open_return_pct") is not None
        and item.get("intraday_return_pct") is not None
        and item.get("open_return_pct") > 0
        and item.get("intraday_return_pct") < 0
    ]
    return {
        "status": "ok",
        "matched_count": len(matched),
        "avg_open_return_pct": _avg(open_values),
        "avg_close_return_pct": _avg(close_values),
        "avg_intraday_return_pct": _avg(intraday_values),
        "gap_open_corr": _pearson(source_values, open_values) if len(source_values) == len(open_values) else None,
        "close_follow_corr": _pearson(source_values, close_values) if len(source_values) == len(close_values) else None,
        "intraday_reversal_corr": _pearson(open_values, intraday_values) if len(open_values) == len(intraday_values) else None,
        "reversal_count": len(reversal_rows),
        "reversal_rate": round(len(reversal_rows) / len(matched), 4) if matched else None,
        "source": "kiwoom_personal_ver1_ticks",
    }


def load_kiwoom_daily_returns(db_path, codes=None):
    codes = [str(item).strip() for item in codes or [] if str(item).strip()]
    if not db_path or not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not _has_table(conn, "ticks"):
            return {}
        result = {}
        for code in codes:
            rows = conn.execute("""
                SELECT id, code, price, open_price, received_at
                FROM ticks
                WHERE code = ? AND price IS NOT NULL AND received_at IS NOT NULL
                ORDER BY received_at ASC, id ASC
            """, (code,)).fetchall()
            result[code] = _daily_returns_from_ticks(rows)
        return result
    finally:
        conn.close()


def _daily_returns_from_ticks(rows):
    grouped = {}
    for row in rows:
        date_key = _date_key(row["received_at"])
        if not date_key:
            continue
        grouped.setdefault(date_key, []).append(row)
    result = {}
    previous_close = None
    for date_key in sorted(grouped):
        day_rows = grouped[date_key]
        first = day_rows[0]
        last = day_rows[-1]
        open_price = _to_float(first["open_price"]) or _to_float(first["price"])
        close_price = _to_float(last["price"])
        if previous_close and open_price and close_price:
            result[date_key] = {
                "date": date_key,
                "open_price": open_price,
                "close_price": close_price,
                "previous_close": previous_close,
                "open_return_pct": round((open_price - previous_close) / previous_close * 100, 4),
                "close_return_pct": round((close_price - previous_close) / previous_close * 100, 4),
                "intraday_return_pct": round((close_price - open_price) / open_price * 100, 4),
            }
        if close_price:
            previous_close = close_price
    return result


def _has_table(conn, table):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _avg(values):
    values = [value for value in values if value is not None]
    return round(sum(values) / len(values), 4) if values else None


def _date_key(value):
    if not value:
        return ""
    return str(value).split("T")[0].split(" ")[0]


def _pearson(left, right):
    if len(left) != len(right) or len(left) < 2:
        return None
    avg_left = sum(left) / len(left)
    avg_right = sum(right) / len(right)
    numerator = sum((x - avg_left) * (y - avg_right) for x, y in zip(left, right))
    left_var = sum((x - avg_left) ** 2 for x in left)
    right_var = sum((y - avg_right) ** 2 for y in right)
    denominator = (left_var * right_var) ** 0.5
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _to_float(value):
    try:
        if value in (None, ""):
            return 0.0
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _to_int(value):
    try:
        if value in (None, ""):
            return 0
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _parse_json(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
