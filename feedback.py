"""Feedback-based score adjustment helpers."""


def build_feedback_adjustments(paper_feedback_summary):
    adjustments = {}
    for item in paper_feedback_summary or []:
        symbol = item.get("symbol")
        if not symbol:
            continue
        bucket = adjustments.setdefault(symbol, {
            "samples": 0,
            "sum_return_pct": 0.0,
            "wins": 0,
            "worst_path_return_pct": None,
            "best_path_return_pct": None,
        })
        count = int(item.get("count") or 0)
        bucket["samples"] += count
        bucket["sum_return_pct"] += float(item.get("avg_return_pct") or 0.0) * count
        bucket["wins"] += int(round(float(item.get("win_rate") or 0.0) * count))
        bucket["worst_path_return_pct"] = _merge_min(bucket["worst_path_return_pct"], _to_float(item.get("worst_path_return_pct")))
        bucket["best_path_return_pct"] = _merge_max(bucket["best_path_return_pct"], _to_float(item.get("best_path_return_pct")))
    result = {}
    for symbol, item in adjustments.items():
        samples = item["samples"]
        avg_return = item["sum_return_pct"] / samples if samples else 0.0
        win_rate = item["wins"] / samples if samples else 0.0
        adjustment = 0
        reason = "insufficient feedback"
        if samples >= 3:
            if avg_return >= 0.15 and win_rate >= 0.55:
                adjustment = 8
                reason = "paper returns are positive"
            elif avg_return > 0 and win_rate >= 0.50:
                adjustment = 4
                reason = "paper returns are mildly positive"
            elif avg_return <= -0.15 or win_rate <= 0.40:
                adjustment = -8
                reason = "paper returns are weak"
            elif avg_return < 0:
                adjustment = -4
                reason = "paper returns are mildly weak"
            worst_path = item["worst_path_return_pct"]
            if worst_path is not None and worst_path <= -0.4:
                adjustment -= 3
                reason += "; adverse path risk"
        result[symbol] = {
            "samples": samples,
            "avg_return_pct": round(avg_return, 4),
            "win_rate": round(win_rate, 4),
            "best_path_return_pct": round(item["best_path_return_pct"] or 0.0, 4),
            "worst_path_return_pct": round(item["worst_path_return_pct"] or 0.0, 4),
            "score_adjustment": adjustment,
            "reason": reason,
        }
    return result


def attach_feedback_adjustments(evidence, paper_feedback_summary):
    adjustments = build_feedback_adjustments(paper_feedback_summary)
    for symbol, item in (evidence.get("symbol_evidence") or {}).items():
        item["feedback_adjustment"] = adjustments.get(symbol, {
            "samples": 0,
            "avg_return_pct": 0.0,
            "win_rate": 0.0,
            "score_adjustment": 0,
            "reason": "no evaluated feedback yet",
        })
    evidence["feedback_adjustments"] = adjustments
    return evidence


def _to_float(value):
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _merge_max(left, right):
    if left is None:
        return right
    return max(left, right)


def _merge_min(left, right):
    if left is None:
        return right
    return min(left, right)
