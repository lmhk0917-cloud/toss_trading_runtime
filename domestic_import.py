"""Import domestic-market feedback summaries from the Kiwoom personal DB.

The importer is intentionally summary-only: it does not copy raw ticks or
orders, and it writes only into the Toss runtime DB.
"""

import argparse
import os
import sqlite3
import sys

try:
    from .store import TossRuntimeStore
except ImportError:  # pragma: no cover
    from store import TossRuntimeStore


DEFAULT_KIWOOM_DB = r"C:\Users\lmhk2\PycharmProjects\KiwoomAPI_GPT_personal_ver1\data\ticks.db"
DEFAULT_CODES = ["005930", "000660"]
SOURCE = "kiwoom_personal_ver1"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import Kiwoom domestic feedback summaries into Toss runtime.")
    parser.add_argument("--kiwoom-db", default=DEFAULT_KIWOOM_DB)
    parser.add_argument("--codes", default=",".join(DEFAULT_CODES))
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--source", default=SOURCE)
    args = parser.parse_args(argv)

    codes = [item.strip() for item in args.codes.split(",") if item.strip()]
    feedback_rows, signal_rows = load_kiwoom_summaries(args.kiwoom_db, codes=codes, source=args.source)
    store = TossRuntimeStore(db_path=args.db_path) if args.db_path else TossRuntimeStore()
    try:
        feedback_count = store.upsert_domestic_feedback_summary(feedback_rows)
        signal_count = store.upsert_domestic_signal_summary(signal_rows)
    finally:
        store.close()
    print("TOSS_DOMESTIC_IMPORT_STATUS=ok")
    print("TOSS_DOMESTIC_IMPORT_SOURCE={}".format(args.source))
    print("TOSS_DOMESTIC_IMPORT_CODES={}".format(",".join(codes)))
    print("TOSS_DOMESTIC_IMPORT_FEEDBACK_ROWS={}".format(feedback_count))
    print("TOSS_DOMESTIC_IMPORT_SIGNAL_ROWS={}".format(signal_count))
    return 0


def load_kiwoom_summaries(db_path, codes=None, source=SOURCE):
    if not os.path.exists(db_path):
        raise RuntimeError("Kiwoom DB not found: {}".format(db_path))
    codes = [str(item).strip() for item in codes or [] if str(item).strip()]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _require_tables(conn, ["paper_trade_results", "signal_logs"])
        names = _names_by_code(conn, codes)
        feedback_rows = []
        for horizon, return_col, best_col, worst_col in [
            (5, "return_5m_pct", "return_5m_pct", "return_5m_pct"),
            (10, "return_10m_pct", "return_10m_pct", "return_10m_pct"),
            (30, "return_30m_pct", "max_gain_30m_pct", "max_loss_30m_pct"),
            (60, "return_60m_pct", "max_gain_60m_pct", "max_loss_60m_pct"),
        ]:
            feedback_rows.extend(_feedback_for_horizon(
                conn,
                codes=codes,
                source=source,
                names=names,
                horizon=horizon,
                return_col=return_col,
                best_col=best_col,
                worst_col=worst_col,
            ))
        signal_rows = _signal_summaries(conn, codes=codes, source=source, names=names)
        return feedback_rows, signal_rows
    finally:
        conn.close()


def _require_tables(conn, tables):
    existing = set(row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
    missing = [table for table in tables if table not in existing]
    if missing:
        raise RuntimeError("Kiwoom DB missing required tables: {}".format(",".join(missing)))


def _names_by_code(conn, codes):
    names = {}
    rows = _execute_code_filter(conn, "SELECT code, name FROM signal_logs {where} ORDER BY detected_at DESC, id DESC", codes)
    for row in rows:
        code = str(row["code"] or "")
        if code and code not in names and row["name"]:
            names[code] = row["name"]
    return names


def _feedback_for_horizon(conn, codes, source, names, horizon, return_col, best_col, worst_col):
    if not _has_column(conn, "paper_trade_results", return_col):
        return []
    query = """
        SELECT
            code,
            COUNT(1) AS sample_count,
            SUM(CASE WHEN {ret} > 0 THEN 1 ELSE 0 END) AS wins,
            AVG({ret}) AS avg_return_pct,
            AVG(CASE WHEN {ret} > 0 THEN {ret} END) AS avg_win_return_pct,
            AVG(CASE WHEN {ret} < 0 THEN {ret} END) AS avg_loss_return_pct,
            MAX({ret}) AS best_return_pct,
            MIN({ret}) AS worst_return_pct,
            MAX({best}) AS best_path_return_pct,
            MIN({worst}) AS worst_path_return_pct
        FROM paper_trade_results
        {{where}} {and_clause} {ret} IS NOT NULL
        GROUP BY code
        ORDER BY code
    """.format(ret=return_col, best=best_col, worst=worst_col, and_clause="AND" if codes else "WHERE")
    rows = _execute_code_filter(conn, query, codes)
    result = []
    for row in rows:
        count = _to_int(row["sample_count"])
        code = str(row["code"] or "")
        result.append({
            "source": source,
            "code": code,
            "name": names.get(code, ""),
            "horizon_min": horizon,
            "sample_count": count,
            "win_rate": round(_to_float(row["wins"]) / count, 4) if count else 0.0,
            "avg_return_pct": round(_to_float(row["avg_return_pct"]), 4),
            "avg_win_return_pct": round(_to_float(row["avg_win_return_pct"]), 4),
            "avg_loss_return_pct": round(_to_float(row["avg_loss_return_pct"]), 4),
            "best_return_pct": round(_to_float(row["best_return_pct"]), 4),
            "worst_return_pct": round(_to_float(row["worst_return_pct"]), 4),
            "best_path_return_pct": round(_to_float(row["best_path_return_pct"]), 4),
            "worst_path_return_pct": round(_to_float(row["worst_path_return_pct"]), 4),
        })
    return result


def _signal_summaries(conn, codes, source, names):
    query = """
        SELECT s.*, latest.signal_count
        FROM signal_logs s
        JOIN (
            SELECT code, MAX(id) AS latest_id, COUNT(1) AS signal_count
            FROM signal_logs
            {where}
            GROUP BY code
        ) latest ON latest.latest_id = s.id
        ORDER BY s.code
    """
    rows = _execute_code_filter(conn, query, codes)
    result = []
    for row in rows:
        code = str(row["code"] or "")
        result.append({
            "source": source,
            "code": code,
            "name": row["name"] or names.get(code, ""),
            "latest_detected_at": row["detected_at"],
            "latest_action_hint": row["action_hint"],
            "latest_confidence_score": _to_float(row["confidence_score"]),
            "latest_risk_level": row["risk_level"],
            "signal_count": _to_int(row["signal_count"]),
        })
    return result


def _execute_code_filter(conn, query, codes):
    codes = [str(item).strip() for item in codes or [] if str(item).strip()]
    params = []
    where = ""
    if codes:
        where = "WHERE code IN ({})".format(",".join("?" for _ in codes))
        params.extend(codes)
    return conn.execute(query.format(where=where), params).fetchall()


def _has_column(conn, table, column):
    return column in [row["name"] for row in conn.execute("PRAGMA table_info({})".format(table)).fetchall()]


def _to_float(value):
    try:
        if value in (None, ""):
            return 0.0
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _to_int(value):
    try:
        if value in (None, ""):
            return 0
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    sys.exit(main())
