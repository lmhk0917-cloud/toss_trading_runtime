"""Read-only Toss Invest smoke test.

This script never creates, modifies, or cancels orders.
"""

import argparse
import json
import os
import sys

try:
    from . import config
    from .client import TossInvestClient
    from .env_loader import load_local_env
    from .evidence import collect_read_only_evidence
except ImportError:  # pragma: no cover
    import config
    from client import TossInvestClient
    from env_loader import load_local_env
    from evidence import collect_read_only_evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a read-only Toss Invest API smoke test.")
    parser.add_argument("--symbols", default=",".join(config.DEFAULT_WATCHLIST))
    parser.add_argument("--account-seq", default=os.environ.get(config.ACCOUNT_SEQ_ENV))
    parser.add_argument("--skip-env-file", action="store_true")
    args = parser.parse_args(argv)

    if not args.skip_env_file:
        load_local_env()

    client = TossInvestClient(account_seq=args.account_seq)
    missing = client.validate_config()
    if missing:
        print("TOSS_READ_ONLY_SMOKE_STATUS=blocked")
        print("TOSS_READ_ONLY_SMOKE_REASON=missing_env:{}".format(",".join(missing)))
        return 2

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    evidence = collect_read_only_evidence(client, symbols, account_seq=args.account_seq)
    print("TOSS_READ_ONLY_SMOKE_STATUS={}".format("ok" if evidence.get("safe_for_analysis") else "failed"))
    print("TOSS_READ_ONLY_SMOKE_SYMBOLS={}".format(",".join(symbols)))
    print("TOSS_READ_ONLY_SMOKE_HAS_ACCOUNT_SEQ={}".format("yes" if args.account_seq else "no"))
    print("TOSS_READ_ONLY_SMOKE_ERRORS={}".format(len(evidence.get("errors") or [])))
    if evidence.get("errors"):
        print(json.dumps({"errors": evidence["errors"]}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
