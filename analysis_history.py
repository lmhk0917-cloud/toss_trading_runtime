"""Helpers for carrying prior structured analysis into the next GPT run."""


def build_previous_analysis_context(latest_by_symbol):
    context = {}
    for symbol, item in (latest_by_symbol or {}).items():
        if not item:
            continue
        context[symbol] = {
            "previous_analysis_id": item.get("analysis_id"),
            "previous_decision": item.get("final_decision"),
            "previous_interest_score": item.get("interest_score"),
            "previous_risk_level": item.get("risk_level"),
            "previous_confidence": item.get("confidence"),
            "previous_summary": item.get("summary"),
        }
    return context


def attach_previous_analysis_context(evidence, previous_context):
    evidence["previous_analysis_context"] = previous_context or {}
    for symbol, item in (evidence.get("symbol_evidence") or {}).items():
        item["previous_analysis"] = (previous_context or {}).get(symbol, {
            "previous_analysis_id": None,
            "previous_decision": None,
            "previous_interest_score": None,
            "previous_risk_level": None,
            "previous_confidence": None,
            "previous_summary": None,
        })
    return evidence


def compare_structured_to_previous(structured, previous_context):
    comparisons = []
    previous_context = previous_context or {}
    for item in structured or []:
        symbol = item.get("symbol")
        previous = previous_context.get(symbol) or {}
        current_score = _to_int_or_none(item.get("interest_score"))
        previous_score = _to_int_or_none(previous.get("previous_interest_score"))
        score_delta = None
        if current_score is not None and previous_score is not None:
            score_delta = current_score - previous_score
        comparisons.append({
            "symbol": symbol,
            "previous_decision": previous.get("previous_decision"),
            "current_decision": item.get("final_decision"),
            "previous_interest_score": previous_score,
            "current_interest_score": current_score,
            "interest_score_delta": score_delta,
            "previous_risk_level": previous.get("previous_risk_level"),
            "current_risk_level": item.get("risk_level"),
            "previous_confidence": previous.get("previous_confidence"),
            "current_confidence": item.get("confidence"),
        })
    return comparisons


def _to_int_or_none(value):
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None
