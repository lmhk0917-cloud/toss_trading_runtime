"""Best-effort structured extraction from GPT focused reports."""

import re


DECISIONS = ["WATCH", "OBSERVE", "HOLD", "RISK", "AVOID"]
LEVELS = ["LOW", "MEDIUM", "HIGH"]


def extract_structured_analysis(text, symbols):
    text = text or ""
    results = []
    for symbol in symbols or []:
        section = _section_for_symbol(text, symbol)
        results.append({
            "symbol": symbol,
            "final_decision": _find_decision(section),
            "interest_score": _find_score(section),
            "risk_level": _find_labeled_value(section, "RISK_LEVEL", LEVELS),
            "confidence": _find_labeled_value(section, "CONFIDENCE", LEVELS),
            "summary": _compact(section),
            "valid": bool(section.strip()),
        })
    return results


def _section_for_symbol(text, symbol):
    marker = str(symbol).upper()
    idx = text.upper().find(marker)
    if idx < 0:
        return ""
    next_idx = len(text)
    pattern = r"\n\s*(?:[-#*\s_]*)(?:SYMBOL\s*:\s*)[A-Z0-9.\-]{1,10}\b"
    for match in re.finditer(pattern, text[idx + 1:], flags=re.IGNORECASE):
        candidate = idx + 1 + match.start()
        if candidate > idx + 20:
            next_idx = candidate
            break
    return text[idx:next_idx]


def _find_decision(section):
    labeled = _find_labeled_value(section, "DECISION", DECISIONS)
    if labeled != "unknown":
        return labeled
    upper = section.upper()
    for item in DECISIONS:
        if item in upper:
            return item
    return "unknown"


def _find_score(section):
    patterns = [
        r"INTEREST_SCORE\s*:\s*(\d{1,3})",
        r"INTEREST\s*:\s*(\d{1,3})",
        r"SCORE\s*:\s*(\d{1,3})",
        r"interest[^0-9]*(\d{1,3})",
        r"score[^0-9]*(\d{1,3})",
    ]
    for pattern in patterns:
        match = re.search(pattern, section, flags=re.IGNORECASE)
        if match:
            value = int(match.group(1))
            return max(0, min(100, value))
    return None


def _find_labeled_value(section, label, allowed):
    pattern = r"{}\s*:\s*[*_\s]*([A-Z]+)".format(re.escape(label))
    match = re.search(pattern, section, flags=re.IGNORECASE)
    if not match:
        return "unknown"
    value = match.group(1).upper()
    return value if value in allowed else "unknown"


def _compact(section, limit=600):
    text = " ".join((section or "").split())
    if len(text) <= limit:
        return text
    return text[:limit] + "..."
