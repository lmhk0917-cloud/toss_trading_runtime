"""Export sanitized Toss runtime summaries into the shared market context hub."""

import json
import os
import sqlite3
import sys
from datetime import datetime


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
NEW_PROJECT_ROOT = os.path.dirname(PROJECT_ROOT)
SHARED_ROOT = os.path.join(NEW_PROJECT_ROOT, "shared_market_context")
if SHARED_ROOT not in sys.path:
    sys.path.insert(0, SHARED_ROOT)

from shared_context_store import SharedContextStore, classify_relationship_payload

try:
    from toss_trading_runtime import config
    from toss_trading_runtime.relationship_analysis import build_relationship_evidence
    from toss_trading_runtime.store import TossRuntimeStore
except ImportError:  # pragma: no cover
    import config
    from relationship_analysis import build_relationship_evidence
    from store import TossRuntimeStore


DEFAULT_SHARED_DB = os.path.join(SHARED_ROOT, "shared_context.db")


def main(argv=None):
    db_path = os.environ.get("TOSS_RUNTIME_DB_PATH") or os.path.join(PROJECT_ROOT, "toss_runtime.db")
    shared_db = os.environ.get("SHARED_CONTEXT_DB_PATH") or DEFAULT_SHARED_DB
    rows = export_to_shared_context(db_path=db_path, shared_db=shared_db)
    print("TOSS_SHARED_CONTEXT_EXPORT_STATUS=ok")
    print("TOSS_SHARED_CONTEXT_EXPORT_ROWS={}".format(rows))
    print("TOSS_SHARED_CONTEXT_DB={}".format(shared_db))
    return 0


def export_to_shared_context(db_path=None, shared_db=DEFAULT_SHARED_DB):
    db_path = db_path or os.path.join(PROJECT_ROOT, "toss_runtime.db")
    store = SharedContextStore(db_path=shared_db)
    row_count = 0
    try:
        if not os.path.exists(db_path):
            store.insert_snapshot(
                "toss", "US", None, "context", "runtime_status",
                {"status": "missing", "db_path": db_path},
                status="missing",
            )
            return 1

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row_count += _export_latest_us_prices(conn, store)
            row_count += _export_tick_analysis(conn, store)
            row_count += _export_orderbook(conn, store)
            row_count += _export_gpt_structured(conn, store)
            row_count += _export_market_context(conn, store)
        finally:
            conn.close()

        toss_store = TossRuntimeStore(db_path=db_path)
        try:
            relationship = build_relationship_evidence(
                toss_store,
                domestic_codes=["005930", "000660"],
                us_symbols=config.FOCUSED_NASDAQ_WATCHLIST,
            )
        finally:
            toss_store.close()
        row_count += _export_relationship(store, relationship)
        return row_count
    finally:
        store.close()


def _export_latest_us_prices(conn, store):
    if not _has_table(conn, "price_snapshots"):
        return 0
    rows = conn.execute("""
        SELECT p.*
        FROM price_snapshots p
        JOIN (
            SELECT symbol, MAX(id) AS latest_id
            FROM price_snapshots
            GROUP BY symbol
        ) latest ON latest.latest_id = p.id
        ORDER BY p.symbol
    """).fetchall()
    if not rows:
        return 0
    latest_time = max(row["collected_at"] for row in rows if row["collected_at"])
    payload = {
        "prices": [dict(row) for row in rows],
        "source_db": os.path.abspath(conn.execute("PRAGMA database_list").fetchone()[2]),
    }
    store.insert_snapshot("toss", "US", None, "minute", "latest_us_prices", payload, asof=latest_time, collected_at=_now(), sample_count=len(rows))
    return 1


def _export_tick_analysis(conn, store):
    if not _has_table(conn, "tick_analysis_snapshots"):
        return 0
    rows = conn.execute("""
        SELECT t.*
        FROM tick_analysis_snapshots t
        JOIN (
            SELECT symbol, MAX(id) AS latest_id
            FROM tick_analysis_snapshots
            GROUP BY symbol
        ) latest ON latest.latest_id = t.id
        ORDER BY t.symbol
    """).fetchall()
    if not rows:
        return 0
    latest_time = max(row["analyzed_at"] for row in rows if row["analyzed_at"])
    store.insert_snapshot("toss", "US", None, "tick", "tick_analysis", {"rows": [dict(row) for row in rows]}, asof=latest_time, sample_count=len(rows))
    return 1


def _export_orderbook(conn, store):
    if not _has_table(conn, "orderbook_snapshots"):
        return 0
    rows = conn.execute("""
        SELECT o.*
        FROM orderbook_snapshots o
        JOIN (
            SELECT symbol, MAX(id) AS latest_id
            FROM orderbook_snapshots
            GROUP BY symbol
        ) latest ON latest.latest_id = o.id
        ORDER BY o.symbol
    """).fetchall()
    if not rows:
        return 0
    latest_time = max(row["collected_at"] for row in rows if row["collected_at"])
    store.insert_snapshot("toss", "US", None, "tick", "orderbook_summary", {"rows": [dict(row) for row in rows]}, asof=latest_time, sample_count=len(rows))
    return 1


def _export_gpt_structured(conn, store):
    if not _has_table(conn, "structured_analysis"):
        return 0
    rows = conn.execute("""
        SELECT s.symbol, s.final_decision, s.interest_score, s.risk_level, s.confidence,
               s.summary, a.analyzed_at, a.mode, a.model
        FROM structured_analysis s
        LEFT JOIN analysis_results a ON a.id = s.analysis_id
        JOIN (
            SELECT symbol, MAX(id) AS latest_id
            FROM structured_analysis
            GROUP BY symbol
        ) latest ON latest.latest_id = s.id
        ORDER BY s.symbol
    """).fetchall()
    if not rows:
        return 0
    latest_time = max((row["analyzed_at"] for row in rows if row["analyzed_at"]), default=None)
    store.insert_snapshot("toss", "US", None, "context", "gpt_structured_analysis", {"rows": [dict(row) for row in rows]}, asof=latest_time, sample_count=len(rows))
    return 1


def _export_market_context(conn, store):
    if not _has_table(conn, "market_context_snapshots"):
        return 0
    row = conn.execute("""
        SELECT collected_at, fx_rate, us_session, kr_session, payload_json
        FROM market_context_snapshots
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    if not row:
        return 0
    payload = dict(row)
    payload["payload_json"] = _parse_json(payload.get("payload_json"))
    store.insert_snapshot("toss", "GLOBAL", None, "context", "market_context", payload, asof=row["collected_at"], sample_count=1)
    return 1


def _export_relationship(store, relationship):
    relationship = relationship or {}
    for pair in relationship.get("pairs") or []:
        pair["evidence_class"] = classify_relationship_payload({
            "paired_sample_count": pair.get("paired_sample_count"),
            "correlation": pair.get("correlation"),
            "timeframe": ((pair.get("resolution") or {}).get("timeframe")),
            "valid_for_intraday_timing": ((pair.get("resolution") or {}).get("intraday_count", 0) > 0),
        }, min_samples=(relationship.get("data_quality") or {}).get("min_samples") or 20)
    store.insert_snapshot(
        "toss",
        "GLOBAL",
        None,
        "relationship",
        "relationship_metrics",
        relationship,
        asof=relationship.get("generated_at"),
        sample_count=(relationship.get("data_quality") or {}).get("paired_observation_count"),
        status="ok" if relationship.get("relationship_regime") != "insufficient_evidence" else "partial",
    )
    return 1


def _has_table(conn, table):
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _parse_json(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
