"""Generate the post-close review markdown from Toss and Kiwoom evidence."""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

try:
    from . import config
    from .quant_feedback import build_quant_feedback_snapshot
except ImportError:  # pragma: no cover
    import config
    from quant_feedback import build_quant_feedback_snapshot



DEFAULT_REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "toss_runtime.db")
DEFAULT_KIWOOM_ROOTS = [
    r"C:\Users\lmhk2\PycharmProjects\Kiwoom_Core_Quant_Lab",
    r"C:\Users\lmhk2\PycharmProjects\KiwoomAPI_GPT_personal_ver1",
    r"C:\Users\lmhk2\PycharmProjects\Kiwoom_Screening_Assistant",
]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate Toss post-close review markdown.")
    parser.add_argument("--symbols", default="MU,NVDA,QQQ")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--reports-dir", default=DEFAULT_REPORTS_DIR)
    parser.add_argument("--latest-name", default="post_close_review_latest.md")
    args = parser.parse_args(argv)

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    os.makedirs(args.reports_dir, exist_ok=True)
    text = build_review(args.db_path, args.reports_dir, symbols)
    stamped_path = os.path.join(
        args.reports_dir,
        "post_close_review_{}.md".format(datetime.now().strftime("%Y%m%d_%H%M%S")),
    )
    latest_path = os.path.join(args.reports_dir, args.latest_name)
    for path in (stamped_path, latest_path):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    print("POST_CLOSE_REVIEW={}".format(os.path.abspath(stamped_path)))
    print("POST_CLOSE_REVIEW_LATEST={}".format(os.path.abspath(latest_path)))
    return 0


def build_review(db_path, reports_dir, symbols):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        tables = [
            "price_snapshots",
            "candle_snapshots",
            "market_context_snapshots",
            "event_logs",
            "trade_ticks",
            "orderbook_snapshots",
            "tick_analysis_snapshots",
            "analysis_results",
            "paper_trade_candidates",
        ]
        counts = {table: _scalar(con, "SELECT COUNT(1) FROM " + table) for table in tables}
        latest_prices = {}
        latest_ticks = {}
        for symbol in symbols:
            latest_prices[symbol] = _row_dict(con.execute(
                """
                SELECT collected_at, symbol, price, currency, source_timestamp
                FROM price_snapshots
                WHERE symbol=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (symbol,),
            ).fetchone())
            latest_ticks[symbol] = _row_dict(con.execute(
                """
                SELECT analyzed_at, symbol, trade_count, latest_price,
                       price_change_pct, volume_sum, best_bid, best_ask,
                       spread_pct, orderbook_imbalance, signal, severity
                FROM tick_analysis_snapshots
                WHERE symbol=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (symbol,),
            ).fetchone())
        latest_analysis = _row_dict(con.execute(
            "SELECT id, analyzed_at, mode, symbols, model, total_tokens FROM analysis_results ORDER BY id DESC LIMIT 1"
        ).fetchone())
        paper_feedback = [
            dict(row) for row in con.execute(
                """
                SELECT symbol, horizon_min, COUNT(1) AS count,
                       ROUND(AVG(result_return_pct), 4) AS avg_return_pct,
                       ROUND(SUM(CASE WHEN result_return_pct > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(1), 4) AS win_rate,
                       ROUND(MIN(min_return_pct), 4) AS worst_path_return_pct,
                       ROUND(MAX(max_return_pct), 4) AS best_path_return_pct
                FROM paper_trade_candidates
                WHERE status='evaluated'
                GROUP BY symbol, horizon_min
                ORDER BY symbol, horizon_min
                """
            ).fetchall()
        ]
        top_events = [
            dict(row) for row in con.execute(
                """
                SELECT event_type, severity, COUNT(1) AS count
                FROM event_logs
                GROUP BY event_type, severity
                ORDER BY count DESC
                LIMIT 12
                """
            ).fetchall()
        ]
        pending_paper = _scalar(con, "SELECT COUNT(1) FROM paper_trade_candidates WHERE status='pending'")
        quant_feedback = build_quant_feedback_snapshot(con, symbols=symbols)
    finally:
        con.close()

    market_session = _load_json(reports_dir, "market_session_latest.json")
    supervisor = _load_json(reports_dir, "supervisor_latest.json")
    tick_summary = _load_json(reports_dir, "tick_collector_latest.json")
    kiwoom_audit = _build_kiwoom_audit()

    lines = []
    lines.append("# Toss Post-Close Review")
    lines.append("")
    lines.append("- generated_at: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    lines.append("- symbols: {}".format(",".join(symbols)))
    lines.append("- orders_enabled: false")
    lines.append("- db_path: {}".format(os.path.abspath(db_path)))
    lines.append("")
    lines.append("## Session Status")
    lines.append("")
    lines.append("- market_session_status: {}".format(market_session.get("status", "missing")))
    lines.append("- supervisor_status: {}".format(supervisor.get("status", "missing")))
    lines.append("- supervisor_iterations: {}".format(supervisor.get("iterations", "missing")))
    lines.append("- supervisor_failures: {}".format(supervisor.get("failures", "missing")))
    lines.append("- tick_collector_status: {}".format(tick_summary.get("status", "stale_or_missing")))
    lines.append("- latest_analysis: {}".format(latest_analysis or "none"))
    lines.append("")
    lines.append("## DB Counts")
    lines.append("")
    for key in tables:
        lines.append("- {}: {}".format(key, counts.get(key)))
    lines.append("")
    lines.append("## Latest Prices")
    lines.append("")
    for symbol in symbols:
        lines.append("- {}: {}".format(symbol, latest_prices.get(symbol) or "none"))
    lines.append("")
    lines.append("## Latest Tick Flow")
    lines.append("")
    for symbol in symbols:
        lines.append("- {}: {}".format(symbol, latest_ticks.get(symbol) or "none"))
    lines.append("")
    lines.append("## Paper Feedback")
    lines.append("")
    if paper_feedback:
        for row in paper_feedback:
            lines.append(
                "- {symbol} {horizon_min}m count={count} win={win_rate} avg={avg_return_pct}% "
                "best_path={best_path_return_pct}% worst_path={worst_path_return_pct}%".format(**row)
            )
    else:
        lines.append("- no evaluated paper feedback yet")
    lines.append("")
    lines.append("## Quant Feedback")
    lines.append("")
    q_overview = quant_feedback.get("overview") or {}
    q_guidance = quant_feedback.get("guidance") or {}
    lines.append("- label: {}".format(q_guidance.get("label", "none")))
    lines.append("- summary: {}".format(q_guidance.get("summary", "none")))
    lines.append("- round_trip_cost_pct: {}".format(quant_feedback.get("round_trip_cost_pct")))
    lines.append("- evaluated_count: {}".format(q_overview.get("evaluated_count")))
    lines.append("- expectancy_pct: {}".format(q_overview.get("expectancy_pct")))
    lines.append("- net_win_rate_pct: {}".format(q_overview.get("net_win_rate_pct")))
    lines.append("- net_profit_factor: {}".format(q_overview.get("net_profit_factor")))
    lines.append("")
    for row in quant_feedback.get("by_symbol") or []:
        guidance = (quant_feedback.get("symbol_guidance") or {}).get(row.get("symbol"), {})
        lines.append("- {symbol}: samples={evaluated_count} exp={expectancy_pct}% net_win={net_win_rate_pct}% pf={net_profit_factor} label={label}".format(
            symbol=row.get("symbol"),
            evaluated_count=row.get("evaluated_count"),
            expectancy_pct=row.get("expectancy_pct"),
            net_win_rate_pct=row.get("net_win_rate_pct"),
            net_profit_factor=row.get("net_profit_factor"),
            label=guidance.get("label", "none"),
        ))
    lines.append("")
    lines.append("## Top Events")
    lines.append("")
    for row in top_events:
        lines.append("- {event_type} severity={severity} count={count}".format(**row))
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    if supervisor.get("failures"):
        lines.append("- CHECK: supervisor had failures; inspect reports/supervisor_latest.json and run_logs.")
    else:
        lines.append("- CHECK: supervisor failure count is zero or unavailable.")
    if pending_paper:
        lines.append("- CHECK: pending paper candidates remain: {}".format(pending_paper))
    else:
        lines.append("- CHECK: no pending paper candidates.")
    if not latest_analysis:
        lines.append("- CHECK: no GPT analysis result recorded after session; run focused analysis manually.")
    else:
        lines.append("- CHECK: GPT/latest analysis is present.")
    lines.append("")
    lines.append("## Kiwoom Path Audit")
    lines.append("")
    lines.append("- selected_db: {}".format(kiwoom_audit.get("selected_db") or "none"))
    lines.append("- env_override: KIWOOM_PERSONAL_DB_PATH")
    lines.append("- config_resolved_db: {}".format(config.KIWOOM_PERSONAL_DB_PATH))
    lines.append("- policy: env override first, then newest existing DB among Kiwoom_Core_Quant_Lab, KiwoomAPI_GPT_personal_ver1, Kiwoom_Screening_Assistant")
    lines.append("")
    lines.append("### Kiwoom Roots")
    lines.append("")
    for root in kiwoom_audit.get("roots") or []:
        lines.append("- path={} exists={} modified_at={} db_candidates={}".format(
            root.get("path"), root.get("exists"), root.get("modified_at"), root.get("db_candidates")
        ))
    lines.append("")
    lines.append("### Kiwoom DB Candidates")
    lines.append("")
    for item in kiwoom_audit.get("dbs") or []:
        lines.append("- path={} exists={} modified_at={} size_bytes={} latest_tick={} paper_trade_results={} signal_logs={} error={}".format(
            item.get("path"),
            item.get("exists"),
            item.get("modified_at"),
            item.get("size_bytes"),
            item.get("latest_tick"),
            item.get("paper_trade_results"),
            item.get("signal_logs"),
            item.get("error"),
        ))
    lines.append("")
    lines.append("## Suggested Fixes")
    lines.append("")
    lines.append("- Keep SQLite WAL/busy_timeout enabled; concurrent price and tick collectors need it.")
    lines.append("- Consider merging tick collection into the supervisor loop or using a single writer queue if another lock appears.")
    lines.append("- Add UI process/session status rows so the window shows active collector PIDs and latest DB write time.")
    lines.append("- Add post-close delta comparison against session start counts for a cleaner sleep-mode report.")
    lines.append("- Review tick signals with actual next-bar returns before using ORDERBOOK imbalance as a positive/negative cue.")
    lines.append("- Kiwoom path handling now avoids a stale hardcoded personal_ver1 DB path; set KIWOOM_PERSONAL_DB_PATH explicitly if you want to pin one renamed project.")
    lines.append("- If Kiwoom_Core_Quant_Lab is now canonical, update human docs/runbooks that still mention KiwoomAPI_GPT_personal_ver1.")
    return "\n".join(lines) + "\n"


def _build_kiwoom_audit():
    roots = []
    dbs = []
    for root in DEFAULT_KIWOOM_ROOTS:
        item = {"path": root, "exists": os.path.isdir(root), "modified_at": None, "db_candidates": []}
        if item["exists"]:
            item["modified_at"] = datetime.fromtimestamp(os.path.getmtime(root)).strftime("%Y-%m-%d %H:%M:%S")
            for rel in ("data\\ticks.db", "ticks.db"):
                db_path = os.path.join(root, rel)
                if os.path.exists(db_path):
                    item["db_candidates"].append(db_path)
                    dbs.append(db_path)
        roots.append(item)
    db_items = [_inspect_kiwoom_db(path) for path in dbs]
    usable = [item for item in db_items if item.get("exists") and not item.get("error")]
    usable.sort(key=lambda item: (item.get("modified_at") or "", item.get("size_bytes") or 0), reverse=True)
    return {"roots": roots, "dbs": db_items, "selected_db": usable[0]["path"] if usable else None}


def _inspect_kiwoom_db(path):
    item = {
        "path": path,
        "exists": os.path.exists(path),
        "size_bytes": None,
        "modified_at": None,
        "latest_tick": None,
        "paper_trade_results": None,
        "signal_logs": None,
        "error": None,
    }
    if not item["exists"]:
        return item
    try:
        stat = os.stat(path)
        item["size_bytes"] = stat.st_size
        item["modified_at"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        con = sqlite3.connect("file:{}?mode=ro".format(path.replace("\\", "/")), uri=True)
        con.row_factory = sqlite3.Row
        try:
            tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
            if "ticks" in tables:
                item["latest_tick"] = _row_dict(con.execute(
                    "SELECT code, MAX(received_at) AS latest_received_at, COUNT(1) AS row_count FROM ticks"
                ).fetchone())
            if "paper_trade_results" in tables:
                item["paper_trade_results"] = _scalar(con, "SELECT COUNT(1) FROM paper_trade_results")
            if "signal_logs" in tables:
                item["signal_logs"] = _scalar(con, "SELECT COUNT(1) FROM signal_logs")
        finally:
            con.close()
    except Exception as exc:
        item["error"] = str(exc)
    return item


def _load_json(reports_dir, name):
    path = os.path.join(reports_dir, name)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        return {"load_error": str(exc), "path": path}


def _row_dict(row):
    return dict(row) if row else None


def _scalar(con, query, params=()):
    row = con.execute(query, params).fetchone()
    return row[0] if row else None


if __name__ == "__main__":
    sys.exit(main())


