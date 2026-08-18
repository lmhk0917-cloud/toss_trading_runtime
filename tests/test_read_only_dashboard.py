import os
import sqlite3
import tempfile

import pytest

from toss_trading_runtime.dashboard import _recent_paper_executions
from toss_trading_runtime.relationship_analysis import load_kiwoom_daily_returns
from toss_trading_runtime.store import TossRuntimeStore


def test_read_only_store_rejects_writes():
    handle = tempfile.NamedTemporaryFile(delete=False)
    path = handle.name
    handle.close()
    writable = TossRuntimeStore(path)
    writable.close()
    try:
        store = TossRuntimeStore(path, read_only=True)
        try:
            assert store.conn.execute("PRAGMA query_only").fetchone()[0] == 1
            with pytest.raises(RuntimeError, match="read-only"):
                store._execute_write("INSERT INTO event_logs (detected_at, symbol, event_type, severity, message) VALUES ('x', 'NVDA', 'x', 'x', 'x')")
            with pytest.raises(sqlite3.OperationalError):
                store.conn.execute("DELETE FROM event_logs")
        finally:
            store.close()
    finally:
        os.unlink(path)


def test_trade_tick_summary_marks_capped_count_as_lower_bound():
    handle = tempfile.NamedTemporaryFile(delete=False)
    path = handle.name
    handle.close()
    store = TossRuntimeStore(path)
    try:
        for index in range(3):
            store.conn.execute(
                "INSERT INTO trade_ticks (collected_at, symbol, trade_timestamp, price, volume, source, payload_json) VALUES (?, 'NVDA', ?, 100, 1, 'test', '{}')",
                ("2026-08-01 00:00:0{}".format(index), "t{}".format(index)),
            )
        store.conn.commit()
        count, exact, high = store._summary_row_count("trade_ticks", limit=2)
        assert count == 2
        assert exact is False
        assert high == 3
    finally:
        store.close()
        os.unlink(path)


def test_paper_execution_rows_parse_json_blockers():
    handle = tempfile.NamedTemporaryFile(delete=False)
    path = handle.name
    handle.close()
    store = TossRuntimeStore(path)
    try:
        store.conn.execute(
            """
            INSERT INTO paper_execution_orders (
                created_at, updated_at, analysis_id, symbol, plan_status, status,
                assumption_version, orders_called, plan_json, result_json
            ) VALUES (?, ?, 1, 'NVDA', 'blocked', 'rejected_by_guard', 'v1', 0, ?, '{}')
            """,
            (
                "2026-08-01 00:00:00",
                "2026-08-01 00:00:00",
                '{"entry":{"blockers":["adaptive_guard"]}}',
            ),
        )
        store.conn.commit()
        rows = _recent_paper_executions(store, symbols=["NVDA"])
        assert rows[0]["exit_reason"] == "adaptive_guard"
    finally:
        store.close()
        os.unlink(path)


def test_kiwoom_daily_returns_prefers_historical_ohlc():
    handle = tempfile.NamedTemporaryFile(delete=False)
    path = handle.name
    handle.close()
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE historical_bars (id INTEGER PRIMARY KEY, code TEXT, timeframe TEXT, bar_time TEXT, open REAL, close REAL)"
        )
        conn.execute("CREATE TABLE ticks (id INTEGER PRIMARY KEY, code TEXT, price REAL, open_price REAL, received_at TEXT)")
        conn.execute("INSERT INTO historical_bars VALUES (1, '005930', 'day', '20260730', 100, 105)")
        conn.execute("INSERT INTO historical_bars VALUES (2, '005930', 'day', '20260731', 110, 108)")
        conn.commit()
    finally:
        conn.close()
    try:
        result = load_kiwoom_daily_returns(path, ["005930"])["005930"]
        assert result["2026-07-31"]["open_return_pct"] == 4.7619
        assert result["2026-07-31"]["close_return_pct"] == 2.8571
    finally:
        os.unlink(path)
