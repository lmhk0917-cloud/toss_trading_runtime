"""Run domestic Korean-market GPT analysis."""

import argparse
import json
import os
import sys
from datetime import datetime

try:
    from . import config
    from .client import TossInvestClient
    from .domestic_analysis import collect_domestic_evidence
    from .env_loader import load_local_env
    from .event_detector import detect_events
    from .openai_gpt import OPENAI_API_KEY_ENV, TossGptAnalyzer
    from .security import sanitize_payload
    from .store import TossRuntimeStore
    from .structured_analysis import extract_structured_analysis
except ImportError:  # pragma: no cover
    import config
    from client import TossInvestClient
    from domestic_analysis import collect_domestic_evidence
    from env_loader import load_local_env
    from event_detector import detect_events
    from openai_gpt import OPENAI_API_KEY_ENV, TossGptAnalyzer
    from security import sanitize_payload
    from store import TossRuntimeStore
    from structured_analysis import extract_structured_analysis


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run domestic KR GPT analysis.")
    parser.add_argument("--symbols", default="005930,000660")
    parser.add_argument("--minute-count", type=int, default=60)
    parser.add_argument("--daily-count", type=int, default=20)
    parser.add_argument("--report-dir", default=os.path.join("toss_trading_runtime", "reports"))
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--max-tokens", type=int, default=1800)
    parser.add_argument("--skip-env-file", action="store_true")
    args = parser.parse_args(argv)

    if not args.skip_env_file:
        load_local_env()
    if not os.environ.get(OPENAI_API_KEY_ENV):
        print("TOSS_DOMESTIC_ANALYSIS_STATUS=blocked")
        print("TOSS_DOMESTIC_ANALYSIS_REASON=missing_env:{}".format(OPENAI_API_KEY_ENV))
        return 2

    codes = [item.strip() for item in args.symbols.split(",") if item.strip()]
    store = TossRuntimeStore(db_path=args.db_path) if args.db_path else TossRuntimeStore()
    client = None
    if os.environ.get(config.CLIENT_ID_ENV) and os.environ.get(config.CLIENT_SECRET_ENV):
        client = TossInvestClient()
    try:
        evidence = collect_domestic_evidence(
            client,
            store,
            codes,
            minute_count=args.minute_count,
            daily_count=args.daily_count,
        )
    finally:
        store.close()

    if not evidence.get("safe_for_analysis"):
        report_path = _write_report(args.report_dir, {
            "status": "failed",
            "stage": "domestic_evidence",
            "symbols": codes,
            "evidence": evidence,
        })
        print("TOSS_DOMESTIC_ANALYSIS_STATUS=failed")
        print("TOSS_DOMESTIC_ANALYSIS_STAGE=domestic_evidence")
        print("TOSS_DOMESTIC_ANALYSIS_REPORT={}".format(report_path))
        return 1

    analyzer = TossGptAnalyzer(max_tokens=args.max_tokens)
    events = detect_events(evidence)
    gpt = analyzer.analyze_domestic_evidence(evidence, symbols=codes)
    structured = extract_structured_analysis(gpt.get("analysis"), codes)
    store = TossRuntimeStore(db_path=args.db_path) if args.db_path else TossRuntimeStore()
    try:
        analysis_id = store.save_analysis_result(evidence, gpt, events=events, mode="domestic_kr")
        store.save_structured_analysis(analysis_id, structured)
        db_path = os.path.abspath(store.db_path)
    finally:
        store.close()

    report = {
        "status": "ok",
        "stage": "domestic_gpt_analysis",
        "generated_at": _now(),
        "symbols": codes,
        "evidence": evidence,
        "events": events,
        "gpt": gpt,
        "structured_analysis": structured,
        "db": {"path": db_path, "analysis_id": analysis_id},
        "safety": {
            "orders_called": False,
            "order_mode": config.ORDER_MODE,
            "allow_real_order": config.ALLOW_REAL_ORDER,
        },
    }
    report_path = _write_report(args.report_dir, report)
    print("TOSS_DOMESTIC_ANALYSIS_STATUS=ok")
    print("TOSS_DOMESTIC_ANALYSIS_SYMBOLS={}".format(",".join(codes)))
    print("TOSS_DOMESTIC_ANALYSIS_MODEL={}".format(gpt.get("model")))
    print("TOSS_DOMESTIC_ANALYSIS_REPORT={}".format(report_path))
    return 0


def _write_report(report_dir, report):
    os.makedirs(report_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.abspath(os.path.join(report_dir, "toss_domestic_analysis_{}.json".format(stamp)))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(sanitize_payload(report), handle, ensure_ascii=False, indent=2, default=str)
    return path


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


if __name__ == "__main__":
    sys.exit(main())
