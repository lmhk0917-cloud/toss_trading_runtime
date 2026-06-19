"""Domestic Korean-market evidence builder using Toss and imported Kiwoom feedback."""

from datetime import datetime

try:
    from .focused_analysis import summarize_candles
    from .market_calendar import current_kr_session
    from .relationship_analysis import build_relationship_evidence
    from .security import sanitize_payload
except ImportError:  # pragma: no cover
    from focused_analysis import summarize_candles
    from market_calendar import current_kr_session
    from relationship_analysis import build_relationship_evidence
    from security import sanitize_payload


def collect_domestic_evidence(client, store, codes, minute_count=60, daily_count=20):
    codes = [str(item).strip() for item in codes or [] if str(item).strip()]
    evidence = {
        "mode": "domestic_kr",
        "broker": "tossinvest",
        "collected_at": _now(),
        "symbols": codes,
        "market": "KR",
        "errors": [],
        "prices": None,
        "stocks": None,
        "exchange_rate": None,
        "kr_market_calendar": None,
        "sessions": {},
        "domestic_feedback": store.domestic_snapshot(codes=codes),
        "market_relationship": build_relationship_evidence(store, domestic_codes=codes),
        "symbol_evidence": {},
        "safe_for_analysis": False,
    }

    if client:
        _capture(evidence, "prices", lambda: client.get_prices(codes))
        _capture(evidence, "stocks", lambda: client.get_stocks(codes))
        _capture(evidence, "exchange_rate", lambda: client.get_exchange_rate(base_currency="USD", quote_currency="KRW"))
        _capture(evidence, "kr_market_calendar", client.get_kr_market_calendar)
    else:
        evidence["errors"].append("toss client unavailable; analysis uses imported feedback only")

    evidence["sessions"] = {
        "KRX_NXT": current_kr_session(evidence.get("kr_market_calendar")),
    }

    prices = _by_symbol(((evidence.get("prices") or {}).get("result") or []))
    stocks = _by_symbol(((evidence.get("stocks") or {}).get("result") or []))
    imported_by_code = {item.get("code"): item for item in evidence.get("domestic_feedback") or []}
    for code in codes:
        item = {
            "symbol": code,
            "code": code,
            "imported_feedback": imported_by_code.get(code),
            "price": prices.get(code),
            "stock": stocks.get(code),
            "minute_candles_summary": None,
            "daily_candles_summary": None,
            "errors": [],
        }
        if client:
            try:
                minute_response = client.get_candles(code, interval="1m", count=minute_count)
                item["minute_candles_summary"] = summarize_candles(
                    ((minute_response.get("result") or {}).get("candles") or []),
                    label="1m",
                )
            except Exception as exc:
                item["errors"].append("1m candles failed: {}".format(exc))
            try:
                daily_response = client.get_candles(code, interval="1d", count=daily_count)
                item["daily_candles_summary"] = summarize_candles(
                    ((daily_response.get("result") or {}).get("candles") or []),
                    label="1d",
                )
            except Exception as exc:
                item["errors"].append("1d candles failed: {}".format(exc))
        evidence["symbol_evidence"][code] = item

    evidence["data_quality"] = _data_quality(evidence)
    evidence["safe_for_analysis"] = evidence["data_quality"]["safe_for_analysis"]
    return sanitize_payload(evidence)


def _capture(evidence, key, loader):
    try:
        evidence[key] = loader()
    except Exception as exc:
        evidence["errors"].append("{} failed: {}".format(key, exc))


def _data_quality(evidence):
    feedback_codes = [
        item.get("code") for item in evidence.get("domestic_feedback") or []
        if item.get("feedback") or item.get("signal")
    ]
    price_codes = [
        str(item.get("symbol") or "") for item in ((evidence.get("prices") or {}).get("result") or [])
        if _to_float(item.get("lastPrice")) > 0
    ]
    return {
        "safe_for_analysis": bool(feedback_codes or price_codes),
        "feedback_codes": sorted(set(feedback_codes)),
        "price_codes": sorted(set(price_codes)),
        "top_level_errors": evidence.get("errors") or [],
        "missing_kr_calendar": evidence.get("kr_market_calendar") is None,
        "missing_exchange_rate": evidence.get("exchange_rate") is None,
        "feedback_only": bool(feedback_codes) and not bool(price_codes),
    }


def _by_symbol(items):
    result = {}
    for item in items:
        symbol = str(item.get("symbol") or "")
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
