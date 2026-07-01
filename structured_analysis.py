"""Best-effort structured extraction from GPT focused reports."""

import json
import re


DECISIONS = ["WATCH", "OBSERVE", "HOLD", "RISK", "AVOID"]
LEVELS = ["LOW", "MEDIUM", "HIGH"]


def extract_structured_analysis(text, symbols):
    text = text or ""
    json_by_symbol = _extract_json_symbol_map(text)
    results = []
    for symbol in symbols or []:
        json_item = json_by_symbol.get(str(symbol).upper())
        if json_item:
            results.append(_structured_from_json_item(symbol, json_item))
            continue
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


def _structured_from_json_item(symbol, item):
    return {
        "symbol": symbol,
        "final_decision": _normalize_choice(
            item.get("decision") or item.get("final_decision"),
            DECISIONS,
            "unknown",
        ),
        "interest_score": _normalize_score(item.get("interest_score") or item.get("score")),
        "risk_level": _normalize_choice(item.get("risk_level"), LEVELS, "unknown"),
        "confidence": _normalize_choice(item.get("confidence"), LEVELS, "unknown"),
        "summary": _compact(item.get("summary") or item.get("rationale") or json.dumps(item, ensure_ascii=False)),
        "valid": True,
    }


def _extract_json_symbol_map(text):
    payload = _extract_structured_json_payload(text)
    rows = []
    if isinstance(payload, dict):
        rows = payload.get("symbols") or payload.get("items") or payload.get("analysis") or []
    elif isinstance(payload, list):
        rows = payload
    result = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or row.get("ticker") or row.get("code") or "").upper()
        if symbol and symbol not in result:
            result[symbol] = row
    return result


def _extract_structured_json_payload(text):
    text = text or ""
    candidates = []
    marker_match = re.search(r"GPT_STRUCTURED_JSON\s*:?", text, flags=re.IGNORECASE)
    if marker_match:
        candidates.extend(_json_candidates_from(text[marker_match.end():]))
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        candidates.append(stripped)
    candidates.extend(_json_candidates_from(text))
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _json_candidates_from(text):
    candidates = []
    for start, opener, closer in (
        (text.find("{"), "{", "}"),
        (text.find("["), "[", "]"),
    ):
        if start < 0:
            continue
        end = _matching_json_end(text, start, opener, closer)
        if end is not None:
            candidates.append(text[start:end + 1])
    return candidates


def _matching_json_end(text, start, opener, closer):
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return idx
    return None


def _section_for_symbol(text, symbol):
    marker = str(symbol).upper()
    idx = _symbol_marker_positions(text, [marker]).get(marker, -1)
    if idx < 0:
        return ""
    next_idx = len(text)
    pattern = r"(?:^|\n)\s*(?:(?:#{1,6}\s*)\S[^\n]*|(?:[-#*\s_]*)(?:\*{0,2}\s*SYMBOL\s*\*{0,2}\s*:\s*\*{0,2}\s*)[A-Z0-9.\-]{1,10}\b)"
    for match in re.finditer(pattern, text[idx + 1:], flags=re.IGNORECASE):
        candidate = idx + 1 + match.start()
        if candidate > idx + 20:
            next_idx = candidate
            break
    return text[idx:next_idx]


def _symbol_marker_positions(text, symbols):
    positions = {}
    for match in re.finditer(r"(?:^|\n)\s*#{1,6}\s*([A-Z0-9.\-]{1,10})\s*(?:\n|$)", text or "", flags=re.IGNORECASE):
        symbol = match.group(1).upper()
        if symbol in symbols and symbol not in positions:
            positions[symbol] = match.start()
    for match in re.finditer(r"(?:^|\n)\s*[-#*\s_]*(?:\*{0,2}\s*SYMBOL\s*\*{0,2}\s*:\s*\*{0,2}\s*)([A-Z0-9.\-]{1,10})\b", text or "", flags=re.IGNORECASE):
        symbol = match.group(1).upper()
        if symbol in symbols:
            current = positions.get(symbol)
            if current is None or match.start() < current:
                positions[symbol] = match.start()
    return positions


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
        r"\*{0,2}\s*INTEREST_SCORE\s*\*{0,2}\s*:\s*\*{0,2}\s*(\d{1,3})",
        r"\*{0,2}\s*INTEREST\s*\*{0,2}\s*:\s*\*{0,2}\s*(\d{1,3})",
        r"\*{0,2}\s*SCORE\s*\*{0,2}\s*:\s*\*{0,2}\s*(\d{1,3})",
        r"interest[^0-9]*(\d{1,3})",
        r"score[^0-9]*(\d{1,3})",
    ]
    for pattern in patterns:
        match = re.search(pattern, section, flags=re.IGNORECASE)
        if match:
            value = int(match.group(1))
            return _normalize_score(value)
    return None


def _find_labeled_value(section, label, allowed):
    pattern = r"\*{{0,2}}\s*{}\s*\*{{0,2}}\s*:\s*\*{{0,2}}\s*([A-Z]+)".format(re.escape(label))
    match = re.search(pattern, section, flags=re.IGNORECASE)
    if not match:
        return "unknown"
    value = match.group(1).upper()
    return value if value in allowed else "unknown"


def _compact(section, limit=600):
    text = " ".join((section or "").split())
    text = re.sub(r"(?:\s*-{3,}\s*)+$", "", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _normalize_choice(value, allowed, default):
    value = str(value or "").upper()
    return value if value in allowed else default


def _normalize_score(value):
    try:
        value = int(float(value))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, value))
