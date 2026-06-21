"""Export collected daily historical data as a shared package for other projects."""

import argparse
import json
import os
import sys
from datetime import datetime

import pandas as pd

try:
    from .import_historical_relationships import (
        DEFAULT_DOMESTIC,
        DEFAULT_US,
        build_historical_rows,
    )
except ImportError:  # pragma: no cover
    from import_historical_relationships import DEFAULT_DOMESTIC, DEFAULT_US, build_historical_rows


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EXPORT_ROOT = os.path.join(
    PROJECT_ROOT,
    "market_data_exports",
    "daily_history",
    "yahoo_finance_10y_ai_semiconductor_1d",
)
DEFAULT_CSV_DIR = os.path.join(DEFAULT_EXPORT_ROOT, "csv")
LEGACY_CSV_DIR = os.path.join(PROJECT_ROOT, "market_data_exports", "csv")
DEFAULT_OUTPUT = os.path.join(DEFAULT_EXPORT_ROOT, "shared", "historical_market_data_v1_daily_kr_us_ai_semiconductor.json")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export daily historical CSVs into a shared JSON package.")
    parser.add_argument("--csv-dir", default=DEFAULT_CSV_DIR)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--domestic-codes", default=",".join(DEFAULT_DOMESTIC))
    parser.add_argument("--us-symbols", default=",".join(DEFAULT_US))
    parser.add_argument("--skip-relationships", action="store_true")
    args = parser.parse_args(argv)

    domestic_codes = [item.strip() for item in args.domestic_codes.split(",") if item.strip()]
    us_symbols = [item.strip().upper() for item in args.us_symbols.split(",") if item.strip()]
    package = build_package(
        csv_dir=args.csv_dir,
        domestic_codes=domestic_codes,
        us_symbols=us_symbols,
        include_relationships=not args.skip_relationships,
    )
    output_dir = os.path.dirname(args.output)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
    with open(args.output, "w", encoding="utf-8") as fp:
        json.dump(package, fp, ensure_ascii=False, indent=2, default=str)
        fp.write("\n")
    print("SHARED_HISTORICAL_EXPORT_STATUS=ok")
    print("SHARED_HISTORICAL_EXPORT_OUTPUT={}".format(args.output))
    print("SHARED_HISTORICAL_EXPORT_BARS={}".format(len(package.get("bars") or [])))
    print("SHARED_HISTORICAL_EXPORT_RELATIONSHIPS={}".format(len(package.get("relationship_observations") or [])))
    return 0


def build_package(csv_dir, domestic_codes, us_symbols, include_relationships=True):
    symbols = []
    bars = []
    for code in domestic_codes:
        symbol_id = "{}.KS".format(code)
        path = resolve_csv_path(csv_dir, [
            "krx_{}_1d.csv".format(code),
            "krx_{}".format(code),
            "{}_KS.csv".format(code),
        ])
        symbol_bars = load_daily_bars(path, market="KRX", code=code, symbol=symbol_id, currency="KRW")
        symbols.append(symbol_summary("KRX", code, symbol_id, "KRW", symbol_bars))
        bars.extend(symbol_bars)
    for symbol in us_symbols:
        path = resolve_csv_path(csv_dir, [
            "us_{}_1d.csv".format(symbol.lower()),
            "us_{}".format(symbol.lower()),
            "{}.csv".format(symbol),
        ])
        symbol_bars = load_daily_bars(path, market="US", code=symbol, symbol=symbol, currency="USD")
        symbols.append(symbol_summary("US", symbol, symbol, "USD", symbol_bars))
        bars.extend(symbol_bars)

    relationships = []
    if include_relationships:
        relationships = build_historical_rows(csv_dir, domestic_codes, us_symbols)

    return {
        "schema": "historical_market_data_v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
        "source": "yahoo_history_csv",
        "resolution": {
            "timeframe": "1d",
            "granularity": "daily_close_to_close",
            "intraday_source": False,
            "tick_source": False,
            "warning": "This package contains daily historical OHLCV data only. It must not be treated as minute or tick evidence.",
        },
        "symbols": symbols,
        "bars": bars,
        "relationship_observations": relationships,
    }


def load_daily_bars(path, market, code, symbol, currency):
    if not os.path.exists(path):
        return []
    frame = pd.read_csv(path)
    if frame.empty or "date" not in frame.columns:
        return []
    rows = []
    for _, row in frame.iterrows():
        date_text = pd.Timestamp(row.get("date")).date().isoformat()
        rows.append({
            "market": market,
            "code": str(code),
            "symbol": symbol,
            "currency": currency,
            "timeframe": "1d",
            "bar_time": "{} 00:00:00".format(date_text),
            "date": date_text,
            "open": _number(row.get("open")),
            "high": _number(row.get("high")),
            "low": _number(row.get("low")),
            "close": _number(row.get("close")),
            "adj_close": _number(row.get("adj_close")),
            "volume": _number(row.get("volume")),
            "daily_return_pct": _number(row.get("daily_return_pct")),
            "source": "yahoo_history_csv",
            "resolution": "1d",
            "intraday_source": False,
            "tick_source": False,
        })
    return rows


def resolve_csv_path(csv_dir, names):
    search_dirs = [csv_dir]
    if os.path.abspath(csv_dir) != os.path.abspath(LEGACY_CSV_DIR):
        search_dirs.append(LEGACY_CSV_DIR)
    for directory in search_dirs:
        for name in names:
            path = os.path.join(directory, name)
            if os.path.exists(path):
                return path
            if "." not in os.path.basename(name):
                matches = sorted(
                    item for item in os.listdir(directory) if item.lower().startswith(name.lower()) and item.lower().endswith(".csv")
                ) if os.path.exists(directory) else []
                if matches:
                    return os.path.join(directory, matches[0])
    return os.path.join(csv_dir, names[0])


def symbol_summary(market, code, symbol, currency, bars):
    return {
        "market": market,
        "code": str(code),
        "symbol": symbol,
        "currency": currency,
        "bar_count": len(bars),
        "oldest_date": bars[0]["date"] if bars else None,
        "latest_date": bars[-1]["date"] if bars else None,
        "timeframe": "1d",
    }


def _number(value):
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    sys.exit(main())
