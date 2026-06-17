"""Focused watchlist analysis evidence builder."""

from datetime import datetime

try:
    from .market_calendar import current_kr_session, current_us_session
    from .security import sanitize_payload
except ImportError:  # pragma: no cover
    from market_calendar import current_kr_session, current_us_session
    from security import sanitize_payload


def collect_focused_evidence(client, symbols, account_seq=None, minute_count=120, daily_count=60):
    symbols = [str(item).strip().upper() for item in symbols if str(item).strip()]
    evidence = {
        "mode": "focused_watchlist",
        "broker": "tossinvest",
        "collected_at": _now(),
        "symbols": symbols,
        "errors": [],
        "accounts": None,
        "prices": None,
        "stocks": None,
        "exchange_rate": None,
        "us_market_calendar": None,
        "kr_market_calendar": None,
        "sessions": {},
        "holdings": None,
        "buying_power_usd": None,
        "symbol_evidence": {},
        "safe_for_analysis": False,
    }

    _capture(evidence, "accounts", client.get_accounts)
    _capture(evidence, "prices", lambda: client.get_prices(symbols))
    _capture(evidence, "stocks", lambda: client.get_stocks(symbols))
    _capture(evidence, "exchange_rate", lambda: client.get_exchange_rate(base_currency="USD", quote_currency="KRW"))
    _capture(evidence, "us_market_calendar", client.get_us_market_calendar)
    _capture(evidence, "kr_market_calendar", client.get_kr_market_calendar)

    evidence["sessions"] = {
        "US": current_us_session(evidence.get("us_market_calendar")),
        "KRX_NXT": current_kr_session(evidence.get("kr_market_calendar")),
    }

    account_seq = account_seq or _first_account_seq(evidence.get("accounts"))
    if account_seq:
        _capture(evidence, "holdings", lambda: client.get_holdings(account_seq=account_seq))
        _capture(evidence, "buying_power_usd", lambda: client.get_buying_power(currency="USD", account_seq=account_seq))

    prices = _by_symbol(((evidence.get("prices") or {}).get("result") or []))
    stocks = _by_symbol(((evidence.get("stocks") or {}).get("result") or []))
    for symbol in symbols:
        item = {
            "symbol": symbol,
            "price": prices.get(symbol),
            "stock": stocks.get(symbol),
            "minute_candles_summary": None,
            "daily_candles_summary": None,
            "errors": [],
        }
        try:
            minute_response = client.get_candles(symbol, interval="1m", count=minute_count)
            item["minute_candles_summary"] = summarize_candles(
                ((minute_response.get("result") or {}).get("candles") or []),
                label="1m",
            )
        except Exception as exc:
            item["errors"].append("1m candles failed: {}".format(exc))

        try:
            daily_response = client.get_candles(symbol, interval="1d", count=daily_count)
            item["daily_candles_summary"] = summarize_candles(
                ((daily_response.get("result") or {}).get("candles") or []),
                label="1d",
            )
        except Exception as exc:
            item["errors"].append("1d candles failed: {}".format(exc))

        evidence["symbol_evidence"][symbol] = item

    evidence["data_quality"] = _data_quality(evidence)
    evidence["safe_for_analysis"] = evidence["data_quality"]["safe_for_analysis"]
    return sanitize_payload(evidence)


def summarize_candles(candles, label):
    closes = [_to_float(item.get("closePrice")) for item in candles]
    typical_prices = [
        (_to_float(item.get("highPrice")) + _to_float(item.get("lowPrice")) + _to_float(item.get("closePrice"))) / 3.0
        for item in candles
    ]
    highs = [_to_float(item.get("highPrice")) for item in candles]
    lows = [_to_float(item.get("lowPrice")) for item in candles]
    volumes = [_to_float(item.get("volume")) for item in candles]
    closes = [item for item in closes if item > 0]
    volumes = [item for item in volumes if item >= 0]
    newest = closes[0] if closes else 0.0
    oldest = closes[-1] if len(closes) >= 2 else 0.0
    change_pct = ((newest - oldest) / oldest * 100.0) if oldest > 0 else 0.0
    avg_volume = sum(volumes) / len(volumes) if volumes else 0.0
    latest_volume = volumes[0] if volumes else 0.0
    high = max([item for item in highs if item > 0] or [0.0])
    low = min([item for item in lows if item > 0] or [0.0])
    range_position = ((newest - low) / (high - low) * 100.0) if high > low and newest > 0 else None
    ma5 = _avg(closes[:5])
    ma20 = _avg(closes[:20])
    vwap = _vwap(typical_prices, volumes)
    rsi14 = _rsi(closes[:15])
    return {
        "label": label,
        "sample": len(candles),
        "latest_close": newest,
        "oldest_close": oldest,
        "change_pct": round(change_pct, 4),
        "latest_volume": latest_volume,
        "avg_volume": round(avg_volume, 4),
        "volume_ratio": round(latest_volume / avg_volume, 4) if avg_volume > 0 else 0.0,
        "range_high": high,
        "range_low": low,
        "range_position_pct": round(range_position, 4) if range_position is not None else None,
        "ma5": round(ma5, 4) if ma5 else None,
        "ma20": round(ma20, 4) if ma20 else None,
        "ma5_vs_ma20_pct": round(((ma5 - ma20) / ma20) * 100.0, 4) if ma5 and ma20 else None,
        "rsi14": round(rsi14, 4) if rsi14 is not None else None,
        "vwap": round(vwap, 4) if vwap else None,
        "vwap_distance_pct": round(((newest - vwap) / vwap) * 100.0, 4) if newest and vwap else None,
        "latest_timestamp": (candles[0] or {}).get("timestamp") if candles else None,
    }


def _capture(evidence, key, loader):
    try:
        evidence[key] = loader()
    except Exception as exc:
        evidence["errors"].append("{} failed: {}".format(key, exc))


def _data_quality(evidence):
    prices = ((evidence.get("prices") or {}).get("result") or [])
    symbol_evidence = evidence.get("symbol_evidence") or {}
    symbols_with_price = set()
    for item in prices:
        symbol = str(item.get("symbol") or "").upper()
        if symbol and _to_float(item.get("lastPrice")) > 0:
            symbols_with_price.add(symbol)
    symbols_with_minute = []
    symbols_with_daily = []
    for symbol, item in symbol_evidence.items():
        minute = item.get("minute_candles_summary") or {}
        daily = item.get("daily_candles_summary") or {}
        if int(minute.get("sample") or 0) > 0 and _to_float(minute.get("latest_close")) > 0:
            symbols_with_minute.append(symbol)
        if int(daily.get("sample") or 0) > 0 and _to_float(daily.get("latest_close")) > 0:
            symbols_with_daily.append(symbol)
    critical_errors = [
        item for item in evidence.get("errors") or []
        if item.startswith("prices failed") or item.startswith("stocks failed")
    ]
    safe = bool(symbols_with_price) and bool(symbols_with_minute) and not critical_errors
    return {
        "safe_for_analysis": safe,
        "top_level_errors": evidence.get("errors") or [],
        "critical_errors": critical_errors,
        "symbols_with_price": sorted(symbols_with_price),
        "symbols_with_minute_candles": sorted(symbols_with_minute),
        "symbols_with_daily_candles": sorted(symbols_with_daily),
        "missing_us_calendar": evidence.get("us_market_calendar") is None,
        "missing_kr_calendar": evidence.get("kr_market_calendar") is None,
        "missing_exchange_rate": evidence.get("exchange_rate") is None,
    }


def _first_account_seq(accounts_response):
    result = (accounts_response or {}).get("result") or []
    if not result:
        return None
    account_seq = result[0].get("accountSeq")
    if account_seq in (None, ""):
        return None
    return str(account_seq)


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


def _avg(values):
    values = [item for item in values if item > 0]
    return sum(values) / len(values) if values else 0.0


def _vwap(prices, volumes):
    total_volume = 0.0
    weighted = 0.0
    for price, volume in zip(prices, volumes):
        if price > 0 and volume > 0:
            weighted += price * volume
            total_volume += volume
    return weighted / total_volume if total_volume > 0 else 0.0


def _rsi(recent_first_closes):
    closes = [item for item in reversed(recent_first_closes) if item > 0]
    if len(closes) < 2:
        return None
    gains = []
    losses = []
    for prev, cur in zip(closes[:-1], closes[1:]):
        change = cur - prev
        if change >= 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))
    if not gains:
        return None
    avg_gain = sum(gains) / len(gains)
    avg_loss = sum(losses) / len(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
