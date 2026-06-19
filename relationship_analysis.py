"""Relationship and lead-lag evidence between KR and US semiconductor themes."""

from datetime import datetime


DEFAULT_DOMESTIC_CODES = ("005930", "000660")
DEFAULT_US_SYMBOLS = ("NVDA", "MU", "QQQ", "SOXX")


def build_relationship_evidence(
    store,
    domestic_codes=None,
    us_symbols=None,
    min_samples=5,
):
    domestic_codes = [str(item).strip() for item in domestic_codes or DEFAULT_DOMESTIC_CODES if str(item).strip()]
    us_symbols = [str(item).strip().upper() for item in us_symbols or DEFAULT_US_SYMBOLS if str(item).strip()]
    observations = store.relationship_observations(domestic_codes=domestic_codes, us_symbols=us_symbols)
    pair_results = _pair_results(observations, min_samples=min_samples)
    domestic_snapshot = store.domestic_snapshot(codes=domestic_codes)
    us_feedback = store.return_feedback_by_symbol()
    proxy = _proxy_alignment(domestic_snapshot, us_feedback, domestic_codes, us_symbols)
    data_quality = {
        "min_samples": int(min_samples),
        "paired_observation_count": len(observations),
        "has_paired_observations": bool(pair_results),
        "has_valid_pair_results": any(
            row.get("relationship_regime") != "insufficient_evidence"
            for row in pair_results
        ),
        "uses_proxy_alignment": not bool(pair_results),
        "warning": None,
    }
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
            "Proxy alignment is directional context, not correlation.",
            "When data_quality.warning is present, GPT must state that relationship strength is not proven.",
        ],
    }


def _pair_results(observations, min_samples):
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
        source_values = [_to_float(row.get("source_return_pct")) for row in rows]
        target_values = [_to_float(row.get("target_return_pct")) for row in rows]
        corr = _pearson(source_values, target_values)
        regime = _regime(corr, len(rows), min_samples)
        results.append({
            "source_symbol": source_symbol,
            "target_symbol": target_symbol,
            "lag_label": lag_label,
            "paired_sample_count": len(rows),
            "correlation": corr,
            "relationship_regime": regime,
            "avg_source_return_pct": round(sum(source_values) / len(source_values), 4) if source_values else 0.0,
            "avg_target_return_pct": round(sum(target_values) / len(target_values), 4) if target_values else 0.0,
            "latest_observed_at": max(str(row.get("observed_at") or "") for row in rows),
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


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
