"""Periodic GPT analysis loop for an active Toss market session."""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta

try:
    from . import config
    from . import run_focused_analysis
    from .env_loader import load_local_env
except ImportError:  # pragma: no cover
    import config
    import run_focused_analysis
    from env_loader import load_local_env


DEFAULT_LOCK_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "reports",
    "periodic_gpt.lock",
)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run focused GPT analysis periodically during a market session.")
    parser.add_argument("--symbols", default=",".join(config.FOCUSED_NASDAQ_WATCHLIST))
    parser.add_argument("--until-kst", default=None)
    parser.add_argument("--max-minutes", type=int, default=120)
    parser.add_argument("--interval-min", type=int, default=30)
    parser.add_argument("--minute-count", type=int, default=120)
    parser.add_argument("--daily-count", type=int, default=20)
    parser.add_argument("--max-tokens", type=int, default=2200)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--report-dir", default=os.path.join("toss_trading_runtime", "reports"))
    parser.add_argument("--lock-path", default=DEFAULT_LOCK_PATH)
    parser.add_argument("--initial-delay-sec", type=int, default=0)
    parser.add_argument("--skip-env-file", action="store_true")
    args = parser.parse_args(argv)

    if not args.skip_env_file:
        load_local_env()

    stop_at = _resolve_stop_time(args.until_kst, args.max_minutes)
    if args.initial_delay_sec > 0:
        time.sleep(args.initial_delay_sec)

    iteration = 0
    failures = 0
    skipped = 0
    while datetime.now() < stop_at:
        iteration += 1
        if _lock_exists(args.lock_path):
            skipped += 1
            print("TOSS_PERIODIC_GPT_ITERATION={} STATUS=skipped REASON=lock_exists".format(iteration))
        else:
            code = _run_once(args)
            if code == 0:
                print("TOSS_PERIODIC_GPT_ITERATION={} STATUS=ok".format(iteration))
            else:
                failures += 1
                print("TOSS_PERIODIC_GPT_ITERATION={} STATUS=failed CODE={}".format(iteration, code))
        next_at = datetime.now() + timedelta(minutes=max(1, args.interval_min))
        if next_at >= stop_at:
            break
        time.sleep(max(1, int((next_at - datetime.now()).total_seconds())))

    print("TOSS_PERIODIC_GPT_STATUS={}".format("ok" if failures == 0 else "partial"))
    print("TOSS_PERIODIC_GPT_ITERATIONS={}".format(iteration))
    print("TOSS_PERIODIC_GPT_FAILURES={}".format(failures))
    print("TOSS_PERIODIC_GPT_SKIPPED={}".format(skipped))
    return 0 if failures == 0 else 1


def _run_once(args):
    os.makedirs(os.path.dirname(os.path.abspath(args.lock_path)), exist_ok=True)
    if _lock_exists(args.lock_path):
        return 0
    with open(args.lock_path, "w", encoding="utf-8") as handle:
        handle.write("{}\n".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")))
    try:
        focused_args = [
            "--symbols", args.symbols,
            "--minute-count", str(args.minute_count),
            "--daily-count", str(args.daily_count),
            "--max-tokens", str(args.max_tokens),
            "--report-dir", args.report_dir,
            "--skip-evidence-save",
            "--skip-env-file",
        ]
        if args.db_path:
            focused_args.extend(["--db-path", args.db_path])
        code = run_focused_analysis.main(focused_args)
        return int(code or 0)
    finally:
        try:
            os.remove(args.lock_path)
        except OSError:
            pass


def _lock_exists(path):
    if not os.path.exists(path):
        return False
    try:
        age_sec = time.time() - os.path.getmtime(path)
    except OSError:
        return False
    if age_sec > 60 * 45:
        return False
    return True


def _resolve_stop_time(until_kst, max_minutes):
    now = datetime.now()
    if until_kst:
        hour, minute = [int(part) for part in until_kst.split(":", 1)]
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target = target + timedelta(days=1)
        return target
    return now + timedelta(minutes=max(1, max_minutes))


if __name__ == "__main__":
    sys.exit(main())
