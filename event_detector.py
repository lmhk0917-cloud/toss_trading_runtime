"""Deterministic event detection for focused Toss evidence."""

from datetime import datetime


def detect_events(evidence):
    events = []
    for error in evidence.get("errors") or []:
        events.append(_event("GLOBAL", "DATA_GAP", "warning", "top-level evidence gap", 1, {"error": error}))
    sessions = evidence.get("sessions") or {}
    us_session = (sessions.get("US") or {}).get("session")
    for symbol, item in (evidence.get("symbol_evidence") or {}).items():
        minute = item.get("minute_candles_summary") or {}
        daily = item.get("daily_candles_summary") or {}
        _detect_candle_events(events, symbol, minute, "1m")
        _detect_candle_events(events, symbol, daily, "1d")
        if us_session == "preMarket" and _is_us_symbol(item):
            events.append(_event(symbol, "US_PREMARKET_CONTEXT", "info", "US premarket data has lower reliability than regular-session confirmation", None, {"session": us_session}))
        if item.get("errors"):
            events.append(_event(symbol, "DATA_GAP", "warning", "symbol has missing evidence", len(item.get("errors") or []), {"errors": item.get("errors")}))
    return events


def _detect_candle_events(events, symbol, summary, label):
    if not summary:
        return
    change_pct = _to_float(summary.get("change_pct"))
    volume_ratio = _to_float(summary.get("volume_ratio"))
    range_pos = summary.get("range_position_pct")
    rsi14 = summary.get("rsi14")
    ma_spread = summary.get("ma5_vs_ma20_pct")
    vwap_distance = summary.get("vwap_distance_pct")
    if change_pct >= 1.0:
        events.append(_event(symbol, "{}_MOMENTUM_UP".format(label), "positive", "{} momentum is positive".format(label), change_pct, summary))
    elif change_pct <= -1.0:
        events.append(_event(symbol, "{}_MOMENTUM_DOWN".format(label), "negative", "{} momentum is negative".format(label), change_pct, summary))
    if volume_ratio >= 2.0:
        events.append(_event(symbol, "{}_VOLUME_SPIKE".format(label), "positive", "{} volume is above recent average".format(label), volume_ratio, summary))
    if rsi14 is not None:
        rsi = _to_float(rsi14)
        if rsi >= 70:
            events.append(_event(symbol, "{}_RSI_OVERBOUGHT".format(label), "warning", "{} RSI is overbought".format(label), rsi, summary))
        elif rsi <= 30 and rsi > 0:
            events.append(_event(symbol, "{}_RSI_OVERSOLD".format(label), "info", "{} RSI is oversold".format(label), rsi, summary))
    if ma_spread is not None:
        spread = _to_float(ma_spread)
        if spread >= 0.15:
            events.append(_event(symbol, "{}_MA5_ABOVE_MA20".format(label), "positive", "{} short MA is above long MA".format(label), spread, summary))
        elif spread <= -0.15:
            events.append(_event(symbol, "{}_MA5_BELOW_MA20".format(label), "negative", "{} short MA is below long MA".format(label), spread, summary))
    if vwap_distance is not None:
        distance = _to_float(vwap_distance)
        if abs(distance) <= 0.2:
            events.append(_event(symbol, "{}_NEAR_VWAP".format(label), "info", "{} price is near approximate VWAP".format(label), distance, summary))
        elif distance >= 1.0:
            events.append(_event(symbol, "{}_ABOVE_VWAP".format(label), "positive", "{} price is above approximate VWAP".format(label), distance, summary))
        elif distance <= -1.0:
            events.append(_event(symbol, "{}_BELOW_VWAP".format(label), "negative", "{} price is below approximate VWAP".format(label), distance, summary))
    if range_pos is not None:
        pos = _to_float(range_pos)
        if pos >= 80:
            events.append(_event(symbol, "{}_NEAR_RANGE_HIGH".format(label), "info", "{} price is near sample range high".format(label), pos, summary))
        elif pos <= 20:
            events.append(_event(symbol, "{}_NEAR_RANGE_LOW".format(label), "info", "{} price is near sample range low".format(label), pos, summary))


def _is_us_symbol(item):
    stock = item.get("stock") or {}
    return stock.get("market") in ("NASDAQ", "NYSE", "AMEX", "US_ETC")


def _event(symbol, event_type, severity, message, value, payload):
    return {
        "detected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
        "symbol": symbol,
        "event_type": event_type,
        "severity": severity,
        "message": message,
        "value": value,
        "payload": payload,
    }


def _to_float(value):
    try:
        if value in (None, ""):
            return 0.0
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0
