"""Run focused watchlist analysis for a small fixed symbol set."""

import argparse
import json
import os
import sys
from datetime import datetime

try:
    from . import config
    from .analysis_history import attach_previous_analysis_context, build_previous_analysis_context, compare_structured_to_previous
    from .client import TossInvestClient
    from .env_loader import load_local_env
    from .event_detector import detect_events
    from .feedback import attach_feedback_adjustments
    from .focused_analysis import collect_focused_evidence
    from .openai_gpt import OPENAI_API_KEY_ENV, TossGptAnalyzer
    from .relationship_analysis import build_relationship_evidence
    from .security import sanitize_payload
    from .store import TossRuntimeStore
    from .structured_analysis import extract_structured_analysis
except ImportError:  # pragma: no cover
    import config
    from analysis_history import attach_previous_analysis_context, build_previous_analysis_context, compare_structured_to_previous
    from client import TossInvestClient
    from env_loader import load_local_env
    from event_detector import detect_events
    from feedback import attach_feedback_adjustments
    from focused_analysis import collect_focused_evidence
    from openai_gpt import OPENAI_API_KEY_ENV, TossGptAnalyzer
    from relationship_analysis import build_relationship_evidence
    from security import sanitize_payload
    from store import TossRuntimeStore
    from structured_analysis import extract_structured_analysis


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run Toss focused watchlist analysis.")
    parser.add_argument("--symbols", default=",".join(config.FOCUSED_NASDAQ_WATCHLIST))
    parser.add_argument("--minute-count", type=int, default=120)
    parser.add_argument("--daily-count", type=int, default=60)
    parser.add_argument("--account-seq", default=None)
    parser.add_argument("--report-dir", default=os.path.join("toss_trading_runtime", "reports"))
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--max-tokens", type=int, default=1800)
    parser.add_argument("--skip-env-file", action="store_true")
    args = parser.parse_args(argv)

    if not args.skip_env_file:
        load_local_env()

    missing = []
    for name in (config.CLIENT_ID_ENV, config.CLIENT_SECRET_ENV, OPENAI_API_KEY_ENV):
        if not os.environ.get(name):
            missing.append(name)
    if missing:
        print("TOSS_FOCUSED_ANALYSIS_STATUS=blocked")
        print("TOSS_FOCUSED_ANALYSIS_REASON=missing_env:{}".format(",".join(missing)))
        return 2

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    client = TossInvestClient(account_seq=args.account_seq or os.environ.get(config.ACCOUNT_SEQ_ENV))
    store = TossRuntimeStore(db_path=args.db_path) if args.db_path else TossRuntimeStore()
    try:
        paper_feedback = store.paper_feedback_summary()
        return_feedback = store.return_feedback_by_symbol()
        previous_context = build_previous_analysis_context(store.latest_structured_by_symbol(symbols))
        relationship_evidence = build_relationship_evidence(store, us_symbols=symbols)
    finally:
        store.close()

    evidence = collect_focused_evidence(
        client,
        symbols,
        account_seq=args.account_seq or os.environ.get(config.ACCOUNT_SEQ_ENV),
        minute_count=args.minute_count,
        daily_count=args.daily_count,
    )
    evidence["paper_feedback_summary"] = paper_feedback
    evidence["return_feedback_by_symbol"] = return_feedback
    evidence["market_relationship"] = relationship_evidence
    evidence = attach_feedback_adjustments(evidence, paper_feedback)
    evidence = attach_previous_analysis_context(evidence, previous_context)
    if not evidence.get("safe_for_analysis"):
        events = detect_events(evidence)
        store = TossRuntimeStore(db_path=args.db_path) if args.db_path else TossRuntimeStore()
        try:
            store.save_evidence(evidence, events=events)
        finally:
            store.close()
        report_path = _write_report(args.report_dir, {
            "status": "failed",
            "stage": "focused_evidence",
            "symbols": symbols,
            "evidence": evidence,
            "events": events,
        })
        print("TOSS_FOCUSED_ANALYSIS_STATUS=failed")
        print("TOSS_FOCUSED_ANALYSIS_STAGE=focused_evidence")
        print("TOSS_FOCUSED_ANALYSIS_REPORT={}".format(report_path))
        return 1

    analyzer = TossGptAnalyzer(max_tokens=args.max_tokens)
    events = detect_events(evidence)
    gpt = analyzer.analyze_focused_evidence(evidence, symbols=symbols)
    structured = extract_structured_analysis(gpt.get("analysis"), symbols)
    structured_comparison = compare_structured_to_previous(structured, previous_context)
    store = TossRuntimeStore(db_path=args.db_path) if args.db_path else TossRuntimeStore()
    try:
        store.save_evidence(evidence, events=events)
        analysis_id = store.save_analysis_result(evidence, gpt, events=events)
        store.save_structured_analysis(analysis_id, structured)
        paper_candidates = store.create_paper_candidates(analysis_id, evidence)
        paper_evaluated = store.evaluate_due_paper_candidates()
        db_path = os.path.abspath(store.db_path)
    finally:
        store.close()
    report = {
        "status": "ok",
        "stage": "focused_gpt_analysis",
        "generated_at": _now(),
        "symbols": symbols,
        "evidence": evidence,
        "events": events,
        "gpt": gpt,
        "structured_analysis": structured,
        "structured_comparison": structured_comparison,
        "db": {
            "path": db_path,
            "paper_candidates_created": paper_candidates,
            "paper_candidates_evaluated": paper_evaluated,
        },
        "safety": {
            "orders_called": False,
            "screening_disabled": not config.ENABLE_TEMP_SCREENING,
            "order_mode": config.ORDER_MODE,
            "allow_real_order": config.ALLOW_REAL_ORDER,
        },
    }
    report_path = _write_report(args.report_dir, report)
    print("TOSS_FOCUSED_ANALYSIS_STATUS=ok")
    print("TOSS_FOCUSED_ANALYSIS_SYMBOLS={}".format(",".join(symbols)))
    print("TOSS_FOCUSED_ANALYSIS_MODEL={}".format(gpt.get("model")))
    print("TOSS_FOCUSED_ANALYSIS_REPORT={}".format(report_path))
    return 0


def _write_report(report_dir, report):
    os.makedirs(report_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.abspath(os.path.join(report_dir, "toss_focused_analysis_{}.json".format(stamp)))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(sanitize_payload(report), handle, ensure_ascii=False, indent=2, default=str)
    return path


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


if __name__ == "__main__":
    sys.exit(main())
