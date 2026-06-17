"""Run a read-only Toss screening pass."""

import argparse
import json
import os
import sys
from datetime import datetime

try:
    from . import config
    from .client import TossInvestClient
    from .env_loader import load_local_env
    from .market_calendar import current_kr_session, current_us_session
    from .screener import screen_symbols
    from .security import sanitize_payload
except ImportError:  # pragma: no cover
    import config
    from client import TossInvestClient
    from env_loader import load_local_env
    from market_calendar import current_kr_session, current_us_session
    from screener import screen_symbols
    from security import sanitize_payload


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run Toss read-only US-stock screening.")
    parser.add_argument("--symbols", default=",".join(config.DEFAULT_WATCHLIST))
    parser.add_argument("--candle-count", type=int, default=60)
    parser.add_argument("--report-dir", default=os.path.join("toss_trading_runtime", "reports"))
    parser.add_argument("--skip-env-file", action="store_true")
    args = parser.parse_args(argv)

    if not config.ENABLE_TEMP_SCREENING:
        print("TOSS_SCREENER_STATUS=disabled")
        print("TOSS_SCREENER_REASON=temporary_disabled_for_focused_analysis")
        return 2

    if not args.skip_env_file:
        load_local_env()
    client = TossInvestClient()
    missing = client.validate_config()
    if missing:
        print("TOSS_SCREENER_STATUS=blocked")
        print("TOSS_SCREENER_REASON=missing_env:{}".format(",".join(missing)))
        return 2

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    us_calendar = client.get_us_market_calendar()
    kr_calendar = client.get_kr_market_calendar()
    result = screen_symbols(client, symbols, candle_count=args.candle_count)
    report = {
        "status": "ok" if not result.get("errors") else "partial",
        "generated_at": _now(),
        "symbols": symbols,
        "sessions": {
            "US": current_us_session(us_calendar),
            "KRX_NXT": current_kr_session(kr_calendar),
        },
        "us_market_calendar": us_calendar,
        "kr_market_calendar": kr_calendar,
        "screening": result,
        "safety": {
            "orders_called": False,
            "order_mode": config.ORDER_MODE,
            "allow_real_order": config.ALLOW_REAL_ORDER,
        },
    }
    path = _write_report(args.report_dir, report)
    print("TOSS_SCREENER_STATUS={}".format(report["status"]))
    print("TOSS_SCREENER_TOP={}".format(",".join([item["symbol"] for item in result.get("results", [])[:5]])))
    print("TOSS_SCREENER_REPORT={}".format(path))
    return 0 if report["status"] in ("ok", "partial") else 1


def _write_report(report_dir, report):
    os.makedirs(report_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.abspath(os.path.join(report_dir, "toss_screening_{}.json".format(stamp)))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(sanitize_payload(report), handle, ensure_ascii=False, indent=2, default=str)
    return path


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


if __name__ == "__main__":
    sys.exit(main())
