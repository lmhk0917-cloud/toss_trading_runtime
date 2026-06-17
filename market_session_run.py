"""One-command market session runner for focused Toss analysis."""

import argparse
import json
import os
import sys
from datetime import datetime

try:
    from . import config
    from .env_loader import load_local_env
    from . import ops_report
    from . import run_focused_analysis
    from . import supervisor
except ImportError:  # pragma: no cover
    import config
    from env_loader import load_local_env
    import ops_report
    import run_focused_analysis
    import supervisor


DEFAULT_REPORT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "reports",
)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run focused collection, GPT analysis, and ops report in sequence.")
    parser.add_argument("--symbols", default=",".join(config.FOCUSED_NASDAQ_WATCHLIST))
    parser.add_argument("--collect-minutes", type=int, default=1)
    parser.add_argument("--until-kst", default=None)
    parser.add_argument("--interval-sec", type=int, default=30)
    parser.add_argument("--minute-count", type=int, default=60)
    parser.add_argument("--daily-count", type=int, default=20)
    parser.add_argument("--max-tokens", type=int, default=1800)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR)
    parser.add_argument("--skip-gpt", action="store_true")
    parser.add_argument("--skip-env-file", action="store_true")
    args = parser.parse_args(argv)

    if not args.skip_env_file:
        load_local_env()

    os.makedirs(args.report_dir, exist_ok=True)
    session_path = os.path.abspath(os.path.join(args.report_dir, "market_session_latest.json"))
    session = _new_session(args)
    _write_json(session_path, session)

    supervisor_summary = os.path.abspath(os.path.join(args.report_dir, "supervisor_latest.json"))
    supervisor_args = [
        "--symbols", args.symbols,
        "--interval-sec", str(args.interval_sec),
        "--minute-count", str(args.minute_count),
        "--daily-count", str(args.daily_count),
        "--summary-json", supervisor_summary,
        "--skip-env-file",
    ]
    if args.db_path:
        supervisor_args.extend(["--db-path", args.db_path])
    if args.until_kst:
        supervisor_args.extend(["--until-kst", args.until_kst])
    else:
        supervisor_args.extend(["--max-minutes", str(max(1, args.collect_minutes))])

    collect_code = _run_stage(
        session,
        session_path,
        "collect",
        supervisor.main,
        supervisor_args,
        continue_on_codes=(0, 1),
    )

    if collect_code == 2:
        session["status"] = "blocked"
        session["finished_at"] = _now()
        _write_json(session_path, session)
        print("TOSS_MARKET_SESSION_STATUS=blocked")
        print("TOSS_MARKET_SESSION_REPORT={}".format(session_path))
        return 2

    if not args.skip_gpt:
        focused_args = [
            "--symbols", args.symbols,
            "--minute-count", str(args.minute_count),
            "--daily-count", str(args.daily_count),
            "--max-tokens", str(args.max_tokens),
            "--report-dir", args.report_dir,
            "--skip-env-file",
        ]
        if args.db_path:
            focused_args.extend(["--db-path", args.db_path])
        _run_stage(
            session,
            session_path,
            "focused_gpt_analysis",
            run_focused_analysis.main,
            focused_args,
            continue_on_codes=(0,),
        )
    else:
        session["stages"].append({
            "name": "focused_gpt_analysis",
            "status": "skipped",
            "started_at": _now(),
            "finished_at": _now(),
            "return_code": 0,
        })
        _write_json(session_path, session)

    ops_html = os.path.abspath(os.path.join(args.report_dir, "ops_latest.html"))
    ops_args = ["--html", ops_html]
    if args.db_path:
        ops_args.extend(["--db-path", args.db_path])
    _run_stage(
        session,
        session_path,
        "ops_report",
        ops_report.main,
        ops_args,
        continue_on_codes=(0,),
    )

    failed = [stage for stage in session["stages"] if stage.get("status") == "failed"]
    session["status"] = "ok" if not failed else "partial"
    session["finished_at"] = _now()
    _write_json(session_path, session)

    print("TOSS_MARKET_SESSION_STATUS={}".format(session["status"]))
    print("TOSS_MARKET_SESSION_REPORT={}".format(session_path))
    print("TOSS_MARKET_SESSION_OPS_HTML={}".format(ops_html))
    return 0 if not failed else 1


def _new_session(args):
    return {
        "status": "running",
        "started_at": _now(),
        "finished_at": None,
        "symbols": [item.strip().upper() for item in args.symbols.split(",") if item.strip()],
        "settings": {
            "collect_minutes": args.collect_minutes,
            "until_kst": args.until_kst,
            "interval_sec": args.interval_sec,
            "minute_count": args.minute_count,
            "daily_count": args.daily_count,
            "max_tokens": args.max_tokens,
            "skip_gpt": bool(args.skip_gpt),
            "orders_enabled": False,
            "screening_enabled": bool(config.ENABLE_TEMP_SCREENING),
        },
        "stages": [],
    }


def _run_stage(session, session_path, name, fn, argv, continue_on_codes=(0,)):
    stage = {
        "name": name,
        "status": "running",
        "started_at": _now(),
        "finished_at": None,
        "return_code": None,
        "argv": list(argv),
    }
    session["stages"].append(stage)
    _write_json(session_path, session)
    try:
        code = fn(argv)
        if code is None:
            code = 0
        stage["return_code"] = int(code)
        stage["status"] = "ok" if int(code) in continue_on_codes else "failed"
    except Exception as exc:
        stage["return_code"] = 1
        stage["status"] = "failed"
        stage["error"] = str(exc)
    stage["finished_at"] = _now()
    _write_json(session_path, session)
    return stage["return_code"]


def _write_json(path, payload):
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    tmp_path = abs_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    os.replace(tmp_path, abs_path)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


if __name__ == "__main__":
    sys.exit(main())
