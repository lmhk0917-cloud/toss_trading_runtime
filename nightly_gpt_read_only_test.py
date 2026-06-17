"""Tonight-ready Toss read-only + GPT analysis test.

This script:
- loads local env files without printing secrets
- calls only Toss read-only APIs
- calls OpenAI for analysis
- writes a sanitized report
- never creates, modifies, or cancels orders
"""

import argparse
import json
import os
import sys
from datetime import datetime

try:
    from . import config
    from .client import TossInvestClient
    from .env_loader import load_local_env
    from .evidence import collect_read_only_evidence
    from .openai_gpt import OPENAI_API_KEY_ENV, TossGptAnalyzer
    from .security import sanitize_payload
except ImportError:  # pragma: no cover
    import config
    from client import TossInvestClient
    from env_loader import load_local_env
    from evidence import collect_read_only_evidence
    from openai_gpt import OPENAI_API_KEY_ENV, TossGptAnalyzer
    from security import sanitize_payload


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run Toss read-only evidence collection and GPT analysis.")
    parser.add_argument("--symbols", default=",".join(config.DEFAULT_WATCHLIST))
    parser.add_argument("--account-seq", default=None)
    parser.add_argument("--report-dir", default=os.path.join("toss_trading_runtime", "reports"))
    parser.add_argument("--max-tokens", type=int, default=900)
    parser.add_argument("--skip-env-file", action="store_true")
    args = parser.parse_args(argv)

    loaded_env_files = [] if args.skip_env_file else load_local_env()
    account_seq = args.account_seq or os.environ.get(config.ACCOUNT_SEQ_ENV)
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]

    missing = []
    for name in (config.CLIENT_ID_ENV, config.CLIENT_SECRET_ENV, OPENAI_API_KEY_ENV):
        if not os.environ.get(name):
            missing.append(name)
    if missing:
        print("TOSS_NIGHTLY_GPT_STATUS=blocked")
        print("TOSS_NIGHTLY_GPT_REASON=missing_env:{}".format(",".join(missing)))
        return 2

    client = TossInvestClient(account_seq=account_seq)
    evidence = collect_read_only_evidence(client, symbols, account_seq=account_seq)
    if not evidence.get("safe_for_analysis"):
        report_path = _write_report(args.report_dir, {
            "status": "failed",
            "stage": "toss_read_only_evidence",
            "loaded_env_files": loaded_env_files,
            "symbols": symbols,
            "evidence": evidence,
        })
        print("TOSS_NIGHTLY_GPT_STATUS=failed")
        print("TOSS_NIGHTLY_GPT_STAGE=toss_read_only_evidence")
        print("TOSS_NIGHTLY_GPT_REPORT={}".format(report_path))
        return 1

    analyzer = TossGptAnalyzer(max_tokens=args.max_tokens)
    gpt = analyzer.analyze_evidence(evidence, symbols=symbols)
    report = {
        "status": "ok",
        "stage": "gpt_analysis",
        "generated_at": _now(),
        "loaded_env_files": loaded_env_files,
        "symbols": symbols,
        "account_seq_present": bool(account_seq),
        "evidence": evidence,
        "gpt": gpt,
        "safety": {
            "orders_called": False,
            "order_mode": config.ORDER_MODE,
            "allow_real_order": config.ALLOW_REAL_ORDER,
        },
    }
    report_path = _write_report(args.report_dir, report)
    print("TOSS_NIGHTLY_GPT_STATUS=ok")
    print("TOSS_NIGHTLY_GPT_SYMBOLS={}".format(",".join(symbols)))
    print("TOSS_NIGHTLY_GPT_MODEL={}".format(gpt.get("model")))
    print("TOSS_NIGHTLY_GPT_REPORT={}".format(report_path))
    return 0


def _write_report(report_dir, report):
    os.makedirs(report_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.abspath(os.path.join(report_dir, "toss_gpt_read_only_{}.json".format(stamp)))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(sanitize_payload(report), handle, ensure_ascii=False, indent=2, default=str)
    return path


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


if __name__ == "__main__":
    sys.exit(main())

