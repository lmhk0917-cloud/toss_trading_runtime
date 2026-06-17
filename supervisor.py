"""Session-aware supervisor for Toss focused runtime."""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

try:
    from . import config
    from .client import TossInvestClient
    from .env_loader import load_local_env
    from .event_detector import detect_events
    from .focused_analysis import collect_focused_evidence
    from .store import TossRuntimeStore
except ImportError:  # pragma: no cover
    import config
    from client import TossInvestClient
    from env_loader import load_local_env
    from event_detector import detect_events
    from focused_analysis import collect_focused_evidence
    from store import TossRuntimeStore


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run Toss focused collection supervisor.")
    parser.add_argument("--symbols", default=",".join(config.FOCUSED_NASDAQ_WATCHLIST))
    parser.add_argument("--until-kst", default=None, help="Stop time HH:MM in KST local machine time.")
    parser.add_argument("--max-minutes", type=int, default=30)
    parser.add_argument("--interval-sec", type=int, default=60)
    parser.add_argument("--minute-count", type=int, default=30)
    parser.add_argument("--daily-count", type=int, default=10)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--summary-json", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "reports",
        "supervisor_latest.json",
    ))
    parser.add_argument("--skip-env-file", action="store_true")
    args = parser.parse_args(argv)

    if not args.skip_env_file:
        load_local_env()
    client = TossInvestClient()
    missing = client.validate_config()
    if missing:
        print("TOSS_SUPERVISOR_STATUS=blocked")
        print("TOSS_SUPERVISOR_REASON=missing_env:{}".format(",".join(missing)))
        return 2

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    stop_at = _resolve_stop_time(args.until_kst, args.max_minutes)
    store = TossRuntimeStore(db_path=args.db_path) if args.db_path else TossRuntimeStore()
    start_counts = _counts(store)
    iterations = 0
    failures = 0
    total_events = 0
    last_error = None
    last_session = None
    try:
        while datetime.now() < stop_at:
            iterations += 1
            try:
                evaluated_before = store.evaluate_due_paper_candidates()
                evidence = collect_focused_evidence(
                    client,
                    symbols,
                    minute_count=args.minute_count,
                    daily_count=args.daily_count,
                )
                events = detect_events(evidence)
                store.save_evidence(evidence, events=events)
                evaluated_after = store.evaluate_due_paper_candidates()
                total_events += len(events)
                session = ((evidence.get("sessions") or {}).get("US") or {}).get("session")
                last_session = session
                last_error = None
                print("TOSS_SUPERVISOR_ITERATION={} SESSION={} EVENTS={} PAPER_EVALUATED={}".format(
                    iterations,
                    session,
                    len(events),
                    evaluated_before + evaluated_after,
                ))
            except Exception as exc:
                failures += 1
                last_error = str(exc)
                print("TOSS_SUPERVISOR_ITERATION={} ERROR={}".format(iterations, exc))
            _write_summary(args.summary_json, _summary_payload(
                args=args,
                store=store,
                start_counts=start_counts,
                iterations=iterations,
                failures=failures,
                total_events=total_events,
                stop_at=stop_at,
                status="running",
                last_session=last_session,
                last_error=last_error,
            ))
            if datetime.now() + timedelta(seconds=args.interval_sec) > stop_at:
                break
            time.sleep(max(1, args.interval_sec))
        end_counts = _counts(store)
        status = "ok" if failures == 0 else "partial"
        _write_summary(args.summary_json, _summary_payload(
            args=args,
            store=store,
            start_counts=start_counts,
            iterations=iterations,
            failures=failures,
            total_events=total_events,
            stop_at=stop_at,
            status=status,
            last_session=last_session,
            last_error=last_error,
        ))
        print("TOSS_SUPERVISOR_STATUS={}".format(status))
        print("TOSS_SUPERVISOR_ITERATIONS={}".format(iterations))
        print("TOSS_SUPERVISOR_FAILURES={}".format(failures))
        print("TOSS_SUPERVISOR_EVENTS={}".format(total_events))
        print("TOSS_SUPERVISOR_DB={}".format(os.path.abspath(store.db_path)))
        print("TOSS_SUPERVISOR_SUMMARY_JSON={}".format(os.path.abspath(args.summary_json)))
        for key in sorted(end_counts):
            print("TOSS_SUPERVISOR_DELTA_{}={}".format(key, end_counts[key] - start_counts.get(key, 0)))
        return 0 if failures == 0 else 1
    finally:
        store.close()


def _counts(store):
    return {
        "prices": store.count_rows("price_snapshots"),
        "candles": store.count_rows("candle_snapshots"),
        "contexts": store.count_rows("market_context_snapshots"),
        "events": store.count_rows("event_logs"),
        "paper": store.count_rows("paper_trade_candidates"),
    }


def _resolve_stop_time(until_kst, max_minutes):
    now = datetime.now()
    if until_kst:
        hour, minute = [int(part) for part in until_kst.split(":", 1)]
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target = target + timedelta(days=1)
        return target
    return now + timedelta(minutes=max(1, max_minutes))


def _summary_payload(args, store, start_counts, iterations, failures, total_events, stop_at, status, last_session, last_error):
    current_counts = _counts(store)
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
        "status": status,
        "symbols": [item.strip().upper() for item in args.symbols.split(",") if item.strip()],
        "stop_at": stop_at.strftime("%Y-%m-%d %H:%M:%S.%f"),
        "iterations": iterations,
        "failures": failures,
        "total_events": total_events,
        "last_session": last_session,
        "last_error": last_error,
        "db_path": os.path.abspath(store.db_path),
        "row_counts": current_counts,
        "row_deltas": {
            key: current_counts[key] - start_counts.get(key, 0)
            for key in sorted(current_counts)
        },
        "health": store.operational_summary().get("health"),
    }


def _write_summary(path, payload):
    if not path:
        return
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    tmp_path = abs_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, abs_path)


if __name__ == "__main__":
    sys.exit(main())
