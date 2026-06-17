"""Read-only US-stock screener based on Toss prices and candles."""

from datetime import datetime


def screen_symbols(client, symbols, candle_count=60):
    symbols = [str(item).strip().upper() for item in symbols if str(item).strip()]
    prices_response = client.get_prices(symbols)
    stocks_response = client.get_stocks(symbols)
    prices = _by_symbol(prices_response.get("result") or [])
    stocks = _by_symbol(stocks_response.get("result") or [])

    rows = []
    errors = []
    for symbol in symbols:
        try:
            candle_response = client.get_candles(symbol, interval="1m", count=candle_count)
            candles = (candle_response.get("result") or {}).get("candles") or []
            row = score_symbol(symbol, prices.get(symbol), stocks.get(symbol), candles)
            rows.append(row)
        except Exception as exc:
            errors.append("{} candles failed: {}".format(symbol, exc))

    rows.sort(key=lambda item: (item.get("score") or 0), reverse=True)
    return {
        "generated_at": _now(),
        "symbols": symbols,
        "candle_count": candle_count,
        "errors": errors,
        "results": rows,
    }


def score_symbol(symbol, price, stock_info, candles):
    closes = [_to_float(item.get("closePrice")) for item in candles]
    volumes = [_to_float(item.get("volume")) for item in candles]
    closes = [item for item in closes if item > 0]
    volumes = [item for item in volumes if item >= 0]

    last_price = _to_float((price or {}).get("lastPrice"))
    if last_price <= 0 and closes:
        last_price = closes[0]

    candle_change_pct = 0.0
    if len(closes) >= 2 and closes[-1] > 0:
        # Toss returns recent-first in observed responses; use newest vs oldest defensively.
        newest = closes[0]
        oldest = closes[-1]
        candle_change_pct = ((newest - oldest) / oldest) * 100.0

    avg_volume = sum(volumes) / len(volumes) if volumes else 0.0
    latest_volume = volumes[0] if volumes else 0.0
    volume_ratio = latest_volume / avg_volume if avg_volume > 0 else 0.0

    score = 0
    reasons = []
    warnings = []

    if candle_change_pct >= 1.0:
        score += 25
        reasons.append("1m candle momentum is positive")
    elif candle_change_pct <= -1.0:
        score -= 15
        warnings.append("short-term candle momentum is negative")

    if volume_ratio >= 1.5:
        score += 25
        reasons.append("latest volume is above recent average")
    elif volume_ratio > 0:
        score += 5

    if last_price > 0:
        score += 10
    else:
        warnings.append("last price is missing")

    status = (stock_info or {}).get("status")
    if status and status != "ACTIVE":
        score -= 50
        warnings.append("stock status is {}".format(status))

    market = (stock_info or {}).get("market")
    security_type = (stock_info or {}).get("securityType")
    if market in ("NASDAQ", "NYSE", "AMEX"):
        score += 10
    if security_type in ("FOREIGN_STOCK", "FOREIGN_ETF", "ETF"):
        score += 5

    return {
        "symbol": symbol,
        "score": int(score),
        "last_price": last_price,
        "currency": (price or {}).get("currency"),
        "price_timestamp": (price or {}).get("timestamp"),
        "market": market,
        "security_type": security_type,
        "status": status,
        "candle_sample": len(candles),
        "candle_change_pct": round(candle_change_pct, 4),
        "latest_volume": latest_volume,
        "avg_volume": round(avg_volume, 4),
        "volume_ratio": round(volume_ratio, 4),
        "reasons": reasons,
        "warnings": warnings,
    }


def _by_symbol(items):
    result = {}
    for item in items:
        symbol = str(item.get("symbol") or "").upper()
        if symbol:
            result[symbol] = item
    return result


def _to_float(value):
    try:
        if value in (None, ""):
            return 0.0
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

