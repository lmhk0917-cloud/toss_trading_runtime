"""Import Yahoo historical returns into Toss relationship observations."""

import argparse
import os
import sys

import pandas as pd

try:
    from .store import TossRuntimeStore
except ImportError:  # pragma: no cover
    from store import TossRuntimeStore


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CSV_DIR = os.path.join(PROJECT_ROOT, "market_data_exports", "csv")
DEFAULT_DOMESTIC = ["005930", "000660"]
DEFAULT_US = ["NVDA", "MU", "QQQ", "SOXX", "AMD", "AVGO", "TSM", "SMH", "SPY"]
HISTORICAL_LAG_LABELS = ["same_date_us_kr", "us_t_minus_1_to_kr_t"]
DAILY_HISTORY_SOURCE = "yahoo_history_csv"
DAILY_HISTORY_TIMEFRAME = "1d"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import historical KR-US relationship rows from Yahoo CSV exports.")
    parser.add_argument("--csv-dir", default=DEFAULT_CSV_DIR)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--domestic-codes", default=",".join(DEFAULT_DOMESTIC))
    parser.add_argument("--us-symbols", default=",".join(DEFAULT_US))
    parser.add_argument("--no-clear", action="store_true")
    args = parser.parse_args(argv)

    domestic_codes = [item.strip() for item in args.domestic_codes.split(",") if item.strip()]
    us_symbols = [item.strip().upper() for item in args.us_symbols.split(",") if item.strip()]
    rows = build_historical_rows(args.csv_dir, domestic_codes, us_symbols)
    store = TossRuntimeStore(db_path=args.db_path) if args.db_path else TossRuntimeStore()
    try:
        deleted = 0
        if not args.no_clear:
            deleted = store.delete_relationship_observations(
                domestic_codes=domestic_codes,
                us_symbols=us_symbols,
                lag_labels=HISTORICAL_LAG_LABELS,
            )
        inserted = store.save_relationship_observations(rows)
    finally:
        store.close()
    print("HISTORICAL_RELATIONSHIP_STATUS=ok")
    print("HISTORICAL_RELATIONSHIP_DELETED={}".format(deleted))
    print("HISTORICAL_RELATIONSHIP_INSERTED={}".format(inserted))
    print("HISTORICAL_RELATIONSHIP_DOMESTIC={}".format(",".join(domestic_codes)))
    print("HISTORICAL_RELATIONSHIP_US={}".format(",".join(us_symbols)))
    return 0


def build_historical_rows(csv_dir, domestic_codes, us_symbols):
    returns = {}
    for code in domestic_codes:
        returns[code] = load_return_series(os.path.join(csv_dir, "{}_KS.csv".format(code)))
    for symbol in us_symbols:
        returns[symbol] = load_return_series(os.path.join(csv_dir, "{}.csv".format(symbol)))

    rows = []
    for code in domestic_codes:
        kr = returns.get(code)
        if kr is None or kr.empty:
            continue
        for us_symbol in us_symbols:
            us = returns.get(us_symbol)
            if us is None or us.empty:
                continue
            rows.extend(same_date_rows(code, kr, us_symbol, us))
            rows.extend(lag1_rows(code, kr, us_symbol, us))
    return rows


def load_return_series(path):
    frame = pd.read_csv(path)
    if frame.empty or "date" not in frame.columns:
        return pd.Series(dtype=float)
    if "daily_return_pct" not in frame.columns:
        frame["daily_return_pct"] = frame["adj_close"].pct_change(fill_method=None) * 100
    frame["date"] = pd.to_datetime(frame["date"])
    series = frame.set_index("date")["daily_return_pct"].dropna()
    return series.sort_index()


def same_date_rows(code, kr, us_symbol, us):
    aligned = pd.concat([kr.rename("kr"), us.rename("us")], axis=1, join="inner").dropna()
    rows = []
    for date, item in aligned.iterrows():
        rows.append(row_payload(
            code=code,
            us_symbol=us_symbol,
            date=date,
            driver_return=float(item["us"]),
            response_return=float(item["kr"]),
            lag_label="same_date_us_kr",
        ))
    return rows


def lag1_rows(code, kr, us_symbol, us):
    rows = []
    us_dates = list(us.index)
    us_pos = 0
    for kr_date, kr_return in kr.items():
        while us_pos + 1 < len(us_dates) and us_dates[us_pos + 1] < kr_date:
            us_pos += 1
        if not us_dates or us_dates[us_pos] >= kr_date:
            continue
        us_date = us_dates[us_pos]
        rows.append(row_payload(
            code=code,
            us_symbol=us_symbol,
            date=kr_date,
            driver_return=float(us.loc[us_date]),
            response_return=float(kr_return),
            lag_label="us_t_minus_1_to_kr_t",
            driver_date=us_date,
        ))
    return rows


def row_payload(code, us_symbol, date, driver_return, response_return, lag_label, driver_date=None):
    date_text = pd.Timestamp(date).date().isoformat()
    driver_date_text = pd.Timestamp(driver_date).date().isoformat() if driver_date is not None else date_text
    return {
        "observed_at": "{} 15:40:00".format(date_text),
        "source_market": "KR",
        "source_symbol": code,
        "target_market": "US",
        "target_symbol": us_symbol,
        "source_return_pct": response_return,
        "target_return_pct": driver_return,
        "lag_label": lag_label,
        "data_source": DAILY_HISTORY_SOURCE,
        "observation_timeframe": DAILY_HISTORY_TIMEFRAME,
        "observation_granularity": "daily_close_to_close",
        "intraday_source": False,
        "tick_source": False,
        "resolution_warning": "Daily historical close-to-close data; do not treat as minute or tick evidence.",
        "valid_for": [
            "long_horizon_correlation",
            "lead_lag_daily_regression",
            "historical_context",
        ],
        "not_valid_for": [
            "intraday_entry_timing",
            "minute_signal_confirmation",
            "tick_flow_detection",
        ],
        "driver_market": "US",
        "driver_symbol": us_symbol,
        "driver_date": driver_date_text,
        "driver_return_pct": driver_return,
        "response_market": "KR",
        "response_symbol": code,
        "response_date": date_text,
        "response_return_pct": response_return,
    }


if __name__ == "__main__":
    sys.exit(main())
