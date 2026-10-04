"""Diagnose and restore missing US-session minute bars from Toss or Yahoo candles.

This utility deliberately does not write synthetic rows into ``trade_ticks``.
Recovered candle rows are stored in ``historical_bars`` with source metadata so
downstream relationship/backtest code can use them as minute bars without
confusing them with real tick evidence.
"""

import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    from . import config
    from .client import TossInvestClient
    from .env_loader import load_local_env
    from .store import DEFAULT_DB_PATH
except ImportError:  # pragma: no cover
    import config
    from client import TossInvestClient
    from env_loader import load_local_env
    from store import DEFAULT_DB_PATH


TOSS_SOURCE = "toss_candles_1m_backfill"
YAHOO_SOURCE = "yahoo_chart_1m_backfill"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Restore missing Nasdaq minute bars from candle APIs.")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--symbols", default=",".join(config.FOCUSED_NASDAQ_WATCHLIST))
    parser.add_argument("--start", required=True, help="KST start, e.g. 2026-09-03 00:00:00")
    parser.add_argument("--end", required=True, help="KST end, e.g. 2026-09-03 05:00:00")
    parser.add_argument("--expected-interval-sec", type=int, default=60)
    parser.add_argument("--gap-threshold-sec", type=int, default=180)
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--provider", choices=("toss", "yahoo"), default="toss")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--skip-env-file", action="store_true")
    args = parser.parse_args(argv)

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    start = _parse_dt(args.start)
    end = _parse_dt(args.end)
    if end <= start:
        raise SystemExit("--end must be after --start")

    with sqlite3.connect(args.db_path, timeout=120) as conn:
        conn.row_factory = sqlite3.Row
        if args.restore:
            conn.execute("PRAGMA busy_timeout = 120000")
            ensure_historical_bars(conn)
        else:
            conn.execute("PRAGMA query_only = ON")
        report = diagnose_existing(conn, symbols, start, end, args.gap_threshold_sec)

    print("TOSS_GAP_DIAGNOSE_STATUS=ok")
    print("TOSS_GAP_WINDOW={}..{}".format(_fmt_dt(start), _fmt_dt(end)))
    print("TOSS_GAP_SYMBOLS={}".format(",".join(symbols)))
    print("TOSS_GAP_EXISTING={}".format(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str)))

    if not args.restore:
        print("TOSS_GAP_RESTORE_STATUS=skipped")
        print("TOSS_GAP_RESTORE_REASON=diagnose_only")
        return 0

    client = None
    if args.provider == "toss":
        if not args.skip_env_file:
            load_local_env()
        client = TossInvestClient()
        missing = client.validate_config()
        if missing:
            print("TOSS_GAP_RESTORE_STATUS=blocked")
            print("TOSS_GAP_RESTORE_REASON=missing_env:{}".format(",".join(missing)))
            return 2

    restored = {}
    with sqlite3.connect(args.db_path, timeout=120) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 120000")
        ensure_historical_bars(conn)
        for symbol in symbols:
            if args.provider == "toss":
                candles = fetch_toss_candles_in_window(client, symbol, start, end, args.count, args.max_pages)
            else:
                candles = fetch_yahoo_candles_in_window(symbol, start, end)
            inserted = insert_candles(conn, symbol, candles, start, end)
            times = [_candle_time(item) for item in candles if _candle_time(item)]
            restored[symbol] = {
                "fetched": len(candles),
                "inserted": inserted,
                "oldest": _fmt_dt(min(times)) if times else None,
                "latest": _fmt_dt(max(times)) if times else None,
            }
        conn.commit()
        after = diagnose_existing(conn, symbols, start, end, args.gap_threshold_sec)

    print("TOSS_GAP_RESTORE_STATUS=ok")
    print("TOSS_GAP_PROVIDER={}".format(args.provider))
    print("TOSS_GAP_RESTORED={}".format(json.dumps(restored, ensure_ascii=False, sort_keys=True, default=str)))
    print("TOSS_GAP_AFTER={}".format(json.dumps(after, ensure_ascii=False, sort_keys=True, default=str)))
    return 0


def ensure_historical_bars(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS historical_bars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            code TEXT NOT NULL,
            symbol TEXT NOT NULL,
            currency TEXT,
            timeframe TEXT NOT NULL,
            bar_time TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            source TEXT NOT NULL,
            payload_json TEXT,
            UNIQUE(symbol, timeframe, bar_time, source)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_historical_bars_symbol_time ON historical_bars(symbol, timeframe, bar_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_historical_bars_code_time ON historical_bars(code, timeframe, bar_time)")


def diagnose_existing(conn, symbols, start, end, gap_threshold_sec):
    output = {}
    for symbol in symbols:
        output[symbol] = {
            "price_snapshots": summarize_table(conn, "price_snapshots", symbol, "collected_at", start, end),
            "trade_ticks": summarize_table(conn, "trade_ticks", symbol, "trade_timestamp", start, end),
            "historical_bars": summarize_table(conn, "historical_bars", symbol, "bar_time", start, end, timeframe="1m"),
        }
        for summary in output[symbol].values():
            if summary.get("times"):
                summary["max_gap_sec"] = max_gap_seconds(summary["times"])
                summary["has_large_gap"] = summary["max_gap_sec"] > gap_threshold_sec
            summary.pop("times", None)
    return output


def summarize_table(conn, table, symbol, time_col, start, end, timeframe=None):
    if not has_table(conn, table):
        return {"exists": False, "count": 0}
    clauses = ["symbol = ?", "{} >= ?".format(time_col), "{} <= ?".format(time_col)]
    params = [symbol, _fmt_dt(start), _fmt_dt(end)]
    if timeframe and has_column(conn, table, "timeframe"):
        clauses.append("lower(timeframe) = ?")
        params.append(timeframe.lower())
    sql = "SELECT {time_col} AS t FROM {table} WHERE {where} ORDER BY {time_col}".format(
        time_col=time_col,
        table=table,
        where=" AND ".join(clauses),
    )
    times = [_parse_dt(row["t"]) for row in conn.execute(sql, params).fetchall() if row["t"]]
    return {
        "exists": True,
        "count": len(times),
        "oldest": _fmt_dt(times[0]) if times else None,
        "latest": _fmt_dt(times[-1]) if times else None,
        "times": times,
    }


def has_table(conn, table):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def has_column(conn, table, column):
    return column in [row[1] for row in conn.execute("PRAGMA table_info({})".format(table)).fetchall()]


def fetch_toss_candles_in_window(client, symbol, start, end, count=500, max_pages=20):
    all_rows = {}
    before = _api_before(end + timedelta(minutes=2))
    for _ in range(max_pages):
        payload = client.get_candles(symbol, interval="1m", count=count, before=before)
        candles = ((payload or {}).get("result") or {}).get("candles") or []
        if not candles:
            break
        page_times = []
        for item in candles:
            ts = _candle_time(item)
            if not ts:
                continue
            page_times.append(ts)
            if start <= ts <= end:
                item["_source"] = TOSS_SOURCE
                all_rows[_fmt_dt(ts)] = item
        oldest = min(page_times) if page_times else None
        if not oldest or oldest <= start:
            break
        before = _api_before(oldest - timedelta(seconds=1))
    return [all_rows[key] for key in sorted(all_rows)]


def fetch_yahoo_candles_in_window(symbol, start, end):
    period1 = int((start - timedelta(hours=9, minutes=5)).replace(tzinfo=timezone.utc).timestamp())
    period2 = int((end - timedelta(hours=9) + timedelta(minutes=5)).replace(tzinfo=timezone.utc).timestamp())
    query = urllib.parse.urlencode({
        "period1": period1,
        "period2": period2,
        "interval": "1m",
        "includePrePost": "true",
        "events": "div,splits",
    })
    url = "https://query1.finance.yahoo.com/v8/finance/chart/{}?{}".format(urllib.parse.quote(symbol.upper()), query)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = (((payload or {}).get("chart") or {}).get("result") or [None])[0] or {}
    timestamps = result.get("timestamp") or []
    quote = ((((result.get("indicators") or {}).get("quote") or [None])[0]) or {})
    rows = []
    for index, raw_ts in enumerate(timestamps):
        ts = datetime.fromtimestamp(int(raw_ts), timezone.utc).replace(tzinfo=None) + timedelta(hours=9)
        if not (start <= ts <= end):
            continue
        rows.append({
            "timestamp": _fmt_dt(ts),
            "openPrice": _list_get(quote.get("open"), index),
            "highPrice": _list_get(quote.get("high"), index),
            "lowPrice": _list_get(quote.get("low"), index),
            "closePrice": _list_get(quote.get("close"), index),
            "volume": _list_get(quote.get("volume"), index),
            "currency": ((result.get("meta") or {}).get("currency")) or "USD",
            "_source_url": url,
            "_source": YAHOO_SOURCE,
        })
    return [row for row in rows if _num(row.get("closePrice")) is not None]


def insert_candles(conn, symbol, candles, start, end):
    inserted = 0
    for item in candles:
        ts = _candle_time(item)
        if not ts or not (start <= ts <= end):
            continue
        payload = dict(item)
        source = item.get("_source") or TOSS_SOURCE
        source_name = "Yahoo Chart API" if source == YAHOO_SOURCE else "Toss candle API"
        payload.update({
            "source": source,
            "resolution_warning": "Recovered from {} 1m candle data; this is minute OHLCV data, not trade tick evidence.".format(source_name),
            "intraday_source": True,
            "tick_source": False,
        })
        cur = conn.execute("""
            INSERT OR IGNORE INTO historical_bars (
                market, code, symbol, currency, timeframe, bar_time,
                open, high, low, close, volume, source, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "US",
            symbol,
            symbol,
            item.get("currency") or "USD",
            "1m",
            _fmt_dt(ts),
            _num(item.get("openPrice") or item.get("open")),
            _num(item.get("highPrice") or item.get("high")),
            _num(item.get("lowPrice") or item.get("low")),
            _num(item.get("closePrice") or item.get("close")),
            _num(item.get("volume")),
            source,
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        ))
        inserted += cur.rowcount
    return inserted


def max_gap_seconds(times):
    if len(times) < 2:
        return 0
    return max((b - a).total_seconds() for a, b in zip(times, times[1:]))


def _candle_time(item):
    for key in ("timestamp", "time", "dateTime", "datetime", "barTime"):
        value = item.get(key)
        if value:
            return _parse_dt(value)
    return None


def _api_before(value):
    return value.strftime("%Y-%m-%dT%H:%M:%S%z") if value.tzinfo else value.strftime("%Y-%m-%dT%H:%M:%S")


def _parse_dt(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    raise ValueError("unsupported datetime: {}".format(value))


def _fmt_dt(value):
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _list_get(values, index):
    if not values or index >= len(values):
        return None
    return values[index]


if __name__ == "__main__":
    sys.exit(main())
