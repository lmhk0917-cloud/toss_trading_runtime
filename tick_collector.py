"""Collect Toss recent trades and orderbook snapshots for focused symbols."""

import argparse
import json
import os
import sys
import time
from datetime import datetime

try:
    from . import config
    from .client import TossInvestClient, TossInvestClientError
    from .env_loader import load_local_env
    from .store import TossRuntimeStore
    from .tick_analysis import analyze_tick_flow, tick_events_from_analysis
except ImportError:  # pragma: no cover
    import config
    from client import TossInvestClient, TossInvestClientError
    from env_loader import load_local_env
    from store import TossRuntimeStore
    from tick_analysis import analyze_tick_flow, tick_events_from_analysis


DEFAULT_SUMMARY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "reports",
    "tick_collector_latest.json",
)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Collect Toss recent trades/orderbook snapshots into SQLite.")
    parser.add_argument("--symbols", default=",".join(config.FOCUSED_NASDAQ_WATCHLIST))
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--interval-sec", type=int, default=10)
    parser.add_argument("--trade-count", type=int, default=50)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--summary-json", default=DEFAULT_SUMMARY_PATH)
    parser.add_argument("--skip-env-file", action="store_true")
    args = parser.parse_args(argv)

    if not args.skip_env_file:
        load_local_env()
    client = TossInvestClient()
    missing = client.validate_config()
    if missing:
        print("TOSS_TICK_COLLECTOR_STATUS=blocked")
        print("TOSS_TICK_COLLECTOR_REASON=missing_env:{}".format(",".join(missing)))
        return 2

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    store = TossRuntimeStore(db_path=args.db_path) if args.db_path else TossRuntimeStore()
    summary = {
        "started_at": _now(),
        "symbols": symbols,
        "iterations_requested": max(1, int(args.iterations)),
        "trade_count_requested": max(1, min(50, int(args.trade_count))),
        "orders_enabled": False,
        "rows": [],
        "errors": [],
    }
    try:
        for index in range(max(1, int(args.iterations))):
            iteration = _collect_iteration(client, store, symbols, max(1, min(50, int(args.trade_count))))
            iteration["iteration"] = index + 1
            summary["rows"].append(iteration)
            print(
                "TOSS_TICK_COLLECTOR_ITERATION={} TRADES_INSERTED={} ORDERBOOKS={} ANALYSES={} ERRORS={}".format(
                    index + 1,
                    iteration.get("trade_rows_inserted") or 0,
                    iteration.get("orderbook_rows_inserted") or 0,
                    iteration.get("analysis_rows_inserted") or 0,
                    len(iteration.get("errors") or []),
                )
            )
            if index + 1 < max(1, int(args.iterations)):
                time.sleep(max(1, int(args.interval_sec)))
        summary["finished_at"] = _now()
        summary["status"] = "ok" if not any(row.get("errors") for row in summary["rows"]) else "warning"
        _write_summary(args.summary_json, summary)
        print("TOSS_TICK_COLLECTOR_STATUS={}".format(summary["status"]))
        print("TOSS_TICK_COLLECTOR_DB={}".format(os.path.abspath(store.db_path)))
        print("TOSS_TICK_COLLECTOR_SUMMARY={}".format(os.path.abspath(args.summary_json)))
        return 0 if summary["status"] in ("ok", "warning") else 1
    finally:
        store.close()


def _collect_iteration(client, store, symbols, trade_count):
    collected_at = _now()
    result = {
        "collected_at": collected_at,
        "trade_rows_inserted": 0,
        "orderbook_rows_inserted": 0,
        "analysis_rows_inserted": 0,
        "event_rows_inserted": 0,
        "symbols": {},
        "errors": [],
    }
    prices_cache = None
    for symbol in symbols:
        symbol_result = {"trades_inserted": 0, "orderbook_inserted": 0, "analysis": None, "errors": []}
        trades = []
        orderbook = {}
        try:
            trades_payload = client.get_trades(symbol, count=trade_count)
            trades = trades_payload.get("result") if isinstance(trades_payload, dict) else []
            inserted = store.save_trade_ticks(symbol, trades_payload, collected_at=collected_at, source="toss_trades")
            symbol_result["trades_inserted"] = inserted
            result["trade_rows_inserted"] += inserted
        except Exception as exc:
            symbol_result["errors"].append("trades: {}".format(exc))
            if prices_cache is None:
                try:
                    prices_cache = client.get_prices(symbols)
                except Exception as price_exc:
                    prices_cache = {"result": []}
                    symbol_result["errors"].append("price_fallback: {}".format(price_exc))
            price_row = _price_row(prices_cache, symbol)
            if price_row:
                inserted = store.save_price_poll_tick(symbol, price_row, collected_at=collected_at)
                symbol_result["trades_inserted"] += inserted
                result["trade_rows_inserted"] += inserted
                trades = [{
                    "timestamp": price_row.get("timestamp") or collected_at,
                    "price": price_row.get("lastPrice") or price_row.get("price"),
                    "volume": price_row.get("volume") or 0,
                    "currency": price_row.get("currency"),
                }]
        try:
            orderbook_payload = client.get_orderbook(symbol)
            orderbook = orderbook_payload.get("result") if isinstance(orderbook_payload, dict) else {}
            inserted = store.save_orderbook_snapshot(symbol, orderbook_payload, collected_at=collected_at)
            symbol_result["orderbook_inserted"] = inserted
            result["orderbook_rows_inserted"] += inserted
        except Exception as exc:
            symbol_result["errors"].append("orderbook: {}".format(exc))

        analysis = analyze_tick_flow(symbol, trades, orderbook, analyzed_at=collected_at)
        store.save_tick_analysis(analysis)
        result["analysis_rows_inserted"] += 1
        symbol_result["analysis"] = analysis
        for event in tick_events_from_analysis(analysis):
            store.save_event(event)
            result["event_rows_inserted"] += 1
        if symbol_result["errors"]:
            result["errors"].extend(["{} {}".format(symbol, item) for item in symbol_result["errors"]])
        result["symbols"][symbol] = symbol_result
    return result


def _price_row(payload, symbol):
    for row in ((payload or {}).get("result") or []):
        if str(row.get("symbol") or "").upper() == str(symbol).upper():
            return row
    return None


def _write_summary(path, payload):
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    os.replace(tmp_path, path)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


if __name__ == "__main__":
    sys.exit(main())
