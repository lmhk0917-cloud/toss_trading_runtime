"""Generate an operational report from Toss runtime DB."""

import argparse
import json
import os
import sys
from datetime import datetime

try:
    from .store import TossRuntimeStore
except ImportError:  # pragma: no cover
    from store import TossRuntimeStore


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print or write Toss runtime operational summary.")
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--html", default=None)
    args = parser.parse_args(argv)

    store = TossRuntimeStore(db_path=args.db_path) if args.db_path else TossRuntimeStore()
    try:
        summary = store.operational_summary()
    finally:
        store.close()

    print("TOSS_OPS_DB={}".format(summary["db_path"]))
    for table, count in sorted(summary["tables"].items()):
        print("TOSS_OPS_TABLE_{}={}".format(table, count))
    latest = summary.get("latest_analysis") or {}
    if latest:
        print("TOSS_OPS_LATEST_ANALYSIS={} SYMBOLS={} TOKENS={}".format(
            latest.get("analyzed_at"),
            latest.get("symbols"),
            latest.get("total_tokens"),
        ))
    health = summary.get("health") or {}
    if health:
        print("TOSS_OPS_HEALTH_STATUS={}".format(health.get("status")))
        for warning in health.get("warnings") or []:
            print("TOSS_OPS_HEALTH_WARNING={}".format(warning))
    if args.html:
        path = os.path.abspath(args.html)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_html(summary))
        print("TOSS_OPS_HTML={}".format(path))
    return 0


def _html(summary):
    def rows(items):
        return "\n".join("<tr><td>{}</td><td>{}</td></tr>".format(k, v) for k, v in items)

    table_rows = rows(sorted(summary.get("tables", {}).items()))
    events = "\n".join("<li>{}: {}</li>".format(item["event_type"], item["count"]) for item in summary.get("top_events", []))
    feedback = "\n".join(
        "<li>{} {}m count={} win={} avg={}%</li>".format(
            item["symbol"], item["horizon_min"], item["count"], item["win_rate"], item["avg_return_pct"]
        )
        for item in summary.get("paper_feedback", [])[:20]
    )
    health = summary.get("health") or {}
    checks = "\n".join(
        "<tr><td>{}</td><td>{}</td></tr>".format(k, v)
        for k, v in sorted((health.get("checks") or {}).items())
    )
    warnings = "\n".join("<li>{}</li>".format(item) for item in health.get("warnings", []))
    return """<!doctype html>
<html><head><meta charset="utf-8"><title>Toss Runtime Ops</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;margin:24px}}td{{padding:4px 12px;border-bottom:1px solid #ddd}}</style>
</head><body>
<h1>Toss Runtime Ops</h1>
<p>Generated at {now}</p>
<p>DB: {db}</p>
<h2>Health</h2><p>Status: {health_status}</p><table>{checks}</table><ul>{warnings}</ul>
<h2>Tables</h2><table>{table_rows}</table>
<h2>Top Events</h2><ul>{events}</ul>
<h2>Paper Feedback</h2><ul>{feedback}</ul>
</body></html>""".format(
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        db=summary.get("db_path"),
        health_status=health.get("status"),
        checks=checks,
        warnings=warnings,
        table_rows=table_rows,
        events=events,
        feedback=feedback,
    )


if __name__ == "__main__":
    sys.exit(main())
