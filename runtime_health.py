"""Runtime health checks for the Toss focused-analysis store."""

from datetime import datetime


def build_runtime_health(store, max_age_minutes=10):
    now = datetime.now()
    latest_price = _latest_row(store, "price_snapshots", "collected_at")
    latest_candle = _latest_row(store, "candle_snapshots", "collected_at")
    latest_context = _latest_row(store, "market_context_snapshots", "collected_at")
    pending_paper = store.conn.execute("""
        SELECT COUNT(1) AS count
        FROM paper_trade_candidates
        WHERE status = 'pending'
    """).fetchone()["count"]
    due_paper = store.conn.execute("""
        SELECT COUNT(1) AS count
        FROM paper_trade_candidates
        WHERE status = 'pending' AND due_at <= ?
    """, (_format_dt(now),)).fetchone()["count"]

    checks = {
        "latest_price_age_min": _age_minutes(now, latest_price.get("collected_at")),
        "latest_candle_age_min": _age_minutes(now, latest_candle.get("collected_at")),
        "latest_context_age_min": _age_minutes(now, latest_context.get("collected_at")),
        "pending_paper_candidates": int(pending_paper),
        "due_paper_candidates": int(due_paper),
    }
    warnings = []
    for key in ("latest_price_age_min", "latest_candle_age_min", "latest_context_age_min"):
        value = checks[key]
        if value is None:
            warnings.append("{}=missing".format(key))
        elif value > max_age_minutes:
            warnings.append("{}={}".format(key, round(value, 2)))
    if due_paper:
        warnings.append("due_paper_candidates={}".format(int(due_paper)))
    return {
        "generated_at": _format_dt(now),
        "status": "ok" if not warnings else "warning",
        "warnings": warnings,
        "checks": checks,
        "latest": {
            "price": latest_price,
            "candle": latest_candle,
            "context": latest_context,
        },
    }


def _latest_row(store, table, time_column):
    row = store.conn.execute("""
        SELECT *
        FROM {}
        ORDER BY {} DESC, id DESC
        LIMIT 1
    """.format(table, time_column)).fetchone()
    return dict(row) if row else {}


def _age_minutes(now, value):
    dt = _parse_dt(value)
    if not dt:
        return None
    return round(max(0.0, (now - dt).total_seconds() / 60.0), 4)


def _parse_dt(value):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def _format_dt(value):
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")
