"""Short, serialized focused data collection loop."""

import argparse
import os
import sys
import time

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
    parser = argparse.ArgumentParser(description="Collect focused Toss evidence into SQLite.")
    parser.add_argument("--symbols", default=",".join(config.FOCUSED_NASDAQ_WATCHLIST))
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--interval-sec", type=int, default=60)
    parser.add_argument("--minute-count", type=int, default=60)
    parser.add_argument("--daily-count", type=int, default=20)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--skip-env-file", action="store_true")
    args = parser.parse_args(argv)

    if not args.skip_env_file:
        load_local_env()
    client = TossInvestClient()
    missing = client.validate_config()
    if missing:
        print("TOSS_COLLECT_LOOP_STATUS=blocked")
        print("TOSS_COLLECT_LOOP_REASON=missing_env:{}".format(",".join(missing)))
        return 2

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    store = TossRuntimeStore(db_path=args.db_path) if args.db_path else TossRuntimeStore()
    try:
        total_events = 0
        for index in range(max(1, args.iterations)):
            pre_evaluated = store.evaluate_due_paper_candidates()
            evidence = collect_focused_evidence(
                client,
                symbols,
                minute_count=args.minute_count,
                daily_count=args.daily_count,
            )
            events = detect_events(evidence)
            store.save_evidence(evidence, events=events)
            evaluated = store.evaluate_due_paper_candidates()
            total_events += len(events)
            print("TOSS_COLLECT_LOOP_ITERATION={} EVENTS={} PAPER_EVALUATED={}".format(index + 1, len(events), pre_evaluated + evaluated))
            if index + 1 < args.iterations:
                time.sleep(max(1, args.interval_sec))
        print("TOSS_COLLECT_LOOP_STATUS=ok")
        print("TOSS_COLLECT_LOOP_SYMBOLS={}".format(",".join(symbols)))
        print("TOSS_COLLECT_LOOP_EVENTS={}".format(total_events))
        print("TOSS_COLLECT_LOOP_DB={}".format(os.path.abspath(store.db_path)))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
