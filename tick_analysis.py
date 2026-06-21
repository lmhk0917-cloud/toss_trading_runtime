"""Trade-tick and orderbook analysis for Toss REST polling."""

from datetime import datetime


def analyze_tick_flow(symbol, trades, orderbook=None, analyzed_at=None):
    symbol = str(symbol or "").upper()
    rows = _normalize_trades(trades)
    orderbook = orderbook or {}
    analyzed_at = analyzed_at or _now()
    prices = [row["price"] for row in rows if row["price"] > 0]
    volumes = [row["volume"] for row in rows if row["volume"] > 0]
    latest_price = prices[0] if prices else 0.0
    oldest_price = prices[-1] if prices else 0.0
    price_change_pct = ((latest_price - oldest_price) / oldest_price) * 100.0 if oldest_price > 0 else 0.0
    volume_sum = sum(volumes)
    best_bid, bid_volume = _best_level(orderbook.get("bids") or [], prefer_high=True)
    best_ask, ask_volume = _best_level(orderbook.get("asks") or [], prefer_high=False)
    spread = best_ask - best_bid if best_ask > 0 and best_bid > 0 else 0.0
    midpoint = (best_ask + best_bid) / 2.0 if best_ask > 0 and best_bid > 0 else 0.0
    spread_pct = (spread / midpoint) * 100.0 if midpoint > 0 else 0.0
    depth_total = bid_volume + ask_volume
    imbalance = ((bid_volume - ask_volume) / depth_total) if depth_total > 0 else 0.0
    signal, severity = _classify(price_change_pct, len(rows), volume_sum, spread_pct, imbalance)
    return {
        "analyzed_at": analyzed_at,
        "symbol": symbol,
        "trade_count": len(rows),
        "latest_price": round(latest_price, 6),
        "oldest_price": round(oldest_price, 6),
        "price_change_pct": round(price_change_pct, 4),
        "volume_sum": round(volume_sum, 4),
        "best_bid": round(best_bid, 6),
        "best_ask": round(best_ask, 6),
        "bid_volume": round(bid_volume, 4),
        "ask_volume": round(ask_volume, 4),
        "spread": round(spread, 6),
        "spread_pct": round(spread_pct, 4),
        "orderbook_imbalance": round(imbalance, 4),
        "signal": signal,
        "severity": severity,
        "data_quality": _data_quality(len(rows), bool(orderbook.get("bids") or orderbook.get("asks")), spread_pct),
    }


def tick_events_from_analysis(analysis):
    if not analysis:
        return []
    signal = analysis.get("signal") or "TICK_NEUTRAL"
    if signal == "TICK_NEUTRAL":
        return []
    symbol = analysis.get("symbol")
    message = (
        "tick_flow signal={signal} trades={trade_count} change={change}% "
        "spread={spread}% imbalance={imbalance}"
    ).format(
        signal=signal,
        trade_count=analysis.get("trade_count") or 0,
        change=analysis.get("price_change_pct") or 0,
        spread=analysis.get("spread_pct") or 0,
        imbalance=analysis.get("orderbook_imbalance") or 0,
    )
    return [{
        "detected_at": analysis.get("analyzed_at") or _now(),
        "symbol": symbol,
        "event_type": signal,
        "severity": analysis.get("severity") or "info",
        "message": message,
        "value": analysis.get("price_change_pct"),
        "payload": analysis,
    }]


def _normalize_trades(trades):
    result = []
    for row in trades or []:
        if not isinstance(row, dict):
            continue
        result.append({
            "price": _to_float(_pick(row, "price", "lastPrice", "tradePrice", "executionPrice")),
            "volume": _to_float(_pick(row, "volume", "quantity", "tradeVolume", "executionVolume")),
            "timestamp": _pick(row, "timestamp", "tradeTimestamp", "executedAt", "time"),
        })
    result.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    return result


def _classify(price_change_pct, trade_count, volume_sum, spread_pct, imbalance):
    if trade_count <= 0:
        return "TICK_NO_DATA", "warning"
    if spread_pct >= 0.20:
        return "WIDE_SPREAD", "warning"
    if price_change_pct >= 0.30 and trade_count >= 3:
        return "TICK_MOMENTUM_UP", "info"
    if price_change_pct <= -0.30 and trade_count >= 3:
        return "TICK_MOMENTUM_DOWN", "warning"
    if imbalance >= 0.25:
        return "ORDERBOOK_BID_IMBALANCE", "info"
    if imbalance <= -0.25:
        return "ORDERBOOK_ASK_IMBALANCE", "warning"
    if volume_sum <= 0:
        return "TICK_VOLUME_MISSING", "warning"
    return "TICK_NEUTRAL", "info"


def _data_quality(trade_count, has_orderbook, spread_pct):
    warnings = []
    if trade_count <= 0:
        warnings.append("no_recent_trades")
    if not has_orderbook:
        warnings.append("no_orderbook_snapshot")
    if spread_pct >= 0.20:
        warnings.append("wide_spread")
    return {"status": "warning" if warnings else "ok", "warnings": warnings}


def _best_level(levels, prefer_high):
    parsed = []
    for row in levels or []:
        if not isinstance(row, dict):
            continue
        price = _to_float(row.get("price"))
        volume = _to_float(row.get("volume"))
        if price > 0:
            parsed.append((price, volume))
    if not parsed:
        return 0.0, 0.0
    parsed.sort(key=lambda item: item[0], reverse=prefer_high)
    return parsed[0]


def _pick(row, *keys):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _to_float(value):
    try:
        if value in (None, ""):
            return 0.0
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
