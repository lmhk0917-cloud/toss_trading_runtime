"""SQLite persistence for Toss focused-analysis runtime."""

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta

try:
    from .security import sanitize_payload
    from .runtime_health import build_runtime_health
    from .quant_feedback import build_quant_feedback_snapshot
    from .paper_execution import (
        ASSUMPTION_VERSION, TERMINAL_STATES, build_readiness_report, simulate_order,
    )
except ImportError:  # pragma: no cover
    from security import sanitize_payload
    from runtime_health import build_runtime_health
    from quant_feedback import build_quant_feedback_snapshot
    from paper_execution import (
        ASSUMPTION_VERSION, TERMINAL_STATES, build_readiness_report, simulate_order,
    )


DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "toss_runtime.db")
OPS_EVENT_SAMPLE_LIMIT = 50000
DEFAULT_PAPER_CANDIDATE_COOLDOWN_MINUTES = 30


class TossRuntimeStore(object):
    def __init__(self, db_path=DEFAULT_DB_PATH, read_only=False):
        self.db_path = db_path
        self.read_only = bool(read_only)
        if self.read_only:
            uri = "file:{}?mode=ro".format(os.path.abspath(db_path).replace("\\", "/"))
            self.conn = sqlite3.connect(uri, uri=True, timeout=120)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA busy_timeout = 120000")
            self.conn.execute("PRAGMA query_only = ON")
            return
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=120)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 120000")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.create_tables()

    def create_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS price_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collected_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                price REAL,
                currency TEXT,
                source_timestamp TEXT,
                payload_json TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS candle_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collected_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                latest_timestamp TEXT,
                latest_close REAL,
                change_pct REAL,
                volume_ratio REAL,
                payload_json TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS market_context_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collected_at TEXT NOT NULL,
                fx_rate REAL,
                us_session TEXT,
                kr_session TEXT,
                payload_json TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS event_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                detected_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                value REAL,
                payload_json TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS analysis_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analyzed_at TEXT NOT NULL,
                mode TEXT NOT NULL,
                symbols TEXT NOT NULL,
                model TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                gpt_analysis TEXT,
                evidence_json TEXT,
                events_json TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_trade_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                analysis_id INTEGER,
                symbol TEXT NOT NULL,
                anchor_price REAL NOT NULL,
                horizon_min INTEGER NOT NULL,
                due_at TEXT NOT NULL,
                status TEXT NOT NULL,
                result_return_pct REAL,
                max_return_pct REAL,
                min_return_pct REAL,
                outcome TEXT,
                evaluated_at TEXT,
                payload_json TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS structured_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                final_decision TEXT,
                interest_score INTEGER,
                risk_level TEXT,
                confidence TEXT,
                summary TEXT,
                payload_json TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_execution_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                analysis_id INTEGER,
                symbol TEXT NOT NULL,
                plan_status TEXT,
                status TEXT NOT NULL,
                risk_multiplier REAL,
                adaptive_state TEXT,
                entry_filled_at TEXT,
                entry_price REAL,
                exit_at TEXT,
                exit_price REAL,
                remaining_fraction REAL,
                gross_return_pct REAL,
                net_return_pct REAL,
                mfe_pct REAL,
                mae_pct REAL,
                exit_reason TEXT,
                assumption_version TEXT NOT NULL,
                orders_called INTEGER NOT NULL DEFAULT 0,
                plan_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                UNIQUE(analysis_id, symbol)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_execution_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_order_id INTEGER NOT NULL,
                event_key TEXT NOT NULL UNIQUE,
                event_at TEXT,
                event_type TEXT NOT NULL,
                state TEXT,
                price REAL,
                fraction REAL,
                note TEXT,
                payload_json TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS domestic_feedback_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT,
                horizon_min INTEGER NOT NULL,
                sample_count INTEGER NOT NULL,
                win_rate REAL,
                avg_return_pct REAL,
                avg_win_return_pct REAL,
                avg_loss_return_pct REAL,
                best_return_pct REAL,
                worst_return_pct REAL,
                best_path_return_pct REAL,
                worst_path_return_pct REAL,
                imported_at TEXT NOT NULL,
                payload_json TEXT,
                UNIQUE(source, code, horizon_min)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS domestic_signal_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT,
                latest_detected_at TEXT,
                latest_action_hint TEXT,
                latest_confidence_score REAL,
                latest_risk_level TEXT,
                signal_count INTEGER NOT NULL,
                imported_at TEXT NOT NULL,
                payload_json TEXT,
                UNIQUE(source, code)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS market_relationship_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL,
                source_market TEXT NOT NULL,
                source_symbol TEXT NOT NULL,
                target_market TEXT NOT NULL,
                target_symbol TEXT NOT NULL,
                source_return_pct REAL,
                target_return_pct REAL,
                lag_label TEXT NOT NULL,
                payload_json TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collected_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                trade_timestamp TEXT,
                price REAL,
                volume REAL,
                side TEXT,
                currency TEXT,
                source TEXT NOT NULL,
                payload_json TEXT,
                UNIQUE(symbol, trade_timestamp, price, volume, source)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS orderbook_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collected_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                source_timestamp TEXT,
                currency TEXT,
                best_bid REAL,
                best_ask REAL,
                bid_volume REAL,
                ask_volume REAL,
                spread REAL,
                spread_pct REAL,
                imbalance REAL,
                payload_json TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tick_analysis_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analyzed_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                trade_count INTEGER NOT NULL,
                latest_price REAL,
                oldest_price REAL,
                price_change_pct REAL,
                volume_sum REAL,
                best_bid REAL,
                best_ask REAL,
                spread_pct REAL,
                orderbook_imbalance REAL,
                signal TEXT,
                severity TEXT,
                payload_json TEXT
            )
        """)
        self._ensure_column("paper_trade_candidates", "max_return_pct", "REAL")
        self._ensure_column("paper_trade_candidates", "min_return_pct", "REAL")
        self._ensure_column("paper_trade_candidates", "outcome", "TEXT")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_price_symbol_time ON price_snapshots(symbol, collected_at)")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_candle_symbol_interval_time ON candle_snapshots(symbol, interval, collected_at)")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_events_symbol_time ON event_logs(symbol, detected_at)")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_paper_status_due ON paper_trade_candidates(status, due_at)")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_paper_status_eval_symbol ON paper_trade_candidates(status, evaluated_at, symbol)")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_paper_symbol_horizon_created ON paper_trade_candidates(symbol, horizon_min, created_at)")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_structured_analysis_symbol ON structured_analysis(symbol, analysis_id)")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_structured_analysis_analysis_symbol ON structured_analysis(analysis_id, symbol)")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_paper_execution_status_time ON paper_execution_orders(status, updated_at)")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_paper_execution_symbol_time ON paper_execution_orders(symbol, created_at)")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_paper_execution_event_order_time ON paper_execution_events(paper_order_id, event_at)")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_domestic_feedback_code ON domestic_feedback_summary(code, horizon_min)")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_domestic_signal_code ON domestic_signal_summary(code)")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_relationship_pair_time ON market_relationship_observations(source_symbol, target_symbol, lag_label, observed_at)")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_trade_ticks_symbol_time ON trade_ticks(symbol, trade_timestamp, collected_at)")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_orderbook_symbol_time ON orderbook_snapshots(symbol, collected_at)")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_tick_analysis_symbol_time ON tick_analysis_snapshots(symbol, analyzed_at)")
        self._commit()

    def _ensure_column(self, table, column, column_type):
        existing = [row["name"] for row in self.conn.execute("PRAGMA table_info({})".format(table)).fetchall()]
        if column not in existing:
            self._execute_write("ALTER TABLE {} ADD COLUMN {} {}".format(table, column, column_type))

    def _execute_write(self, query, params=()):
        self._assert_writable()
        return self._with_write_retry(lambda: self.conn.execute(query, params))

    def _commit(self):
        self._assert_writable()
        return self._with_write_retry(self.conn.commit)

    def _assert_writable(self):
        if self.read_only:
            raise RuntimeError("TossRuntimeStore is read-only")

    def _with_write_retry(self, func, max_attempts=8):
        delay = 0.25
        for attempt in range(max_attempts):
            try:
                return func()
            except sqlite3.OperationalError as exc:
                if not _is_locked_error(exc) or attempt >= max_attempts - 1:
                    raise
                time.sleep(delay)
                delay = min(delay * 1.7, 5.0)

    def save_evidence(self, evidence, events=None):
        evidence = evidence or {}
        collected_at = evidence.get("collected_at") or _now()
        prices = ((evidence.get("prices") or {}).get("result") or [])
        for item in prices:
            self._execute_write("""
                INSERT INTO price_snapshots (
                    collected_at, symbol, price, currency, source_timestamp, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                collected_at,
                str(item.get("symbol") or "").upper(),
                _to_float(item.get("lastPrice")),
                item.get("currency"),
                item.get("timestamp"),
                _json(item),
            ))

        for symbol, item in (evidence.get("symbol_evidence") or {}).items():
            for key, interval in (("minute_candles_summary", "1m"), ("daily_candles_summary", "1d")):
                summary = item.get(key)
                if not summary:
                    continue
                self._execute_write("""
                    INSERT INTO candle_snapshots (
                        collected_at, symbol, interval, sample_count, latest_timestamp,
                        latest_close, change_pct, volume_ratio, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    collected_at,
                    symbol,
                    interval,
                    _to_int(summary.get("sample")),
                    summary.get("latest_timestamp"),
                    _to_float(summary.get("latest_close")),
                    _to_float(summary.get("change_pct")),
                    _to_float(summary.get("volume_ratio")),
                    _json(summary),
                ))

        fx = ((evidence.get("exchange_rate") or {}).get("result") or {})
        sessions = evidence.get("sessions") or {}
        self._execute_write("""
            INSERT INTO market_context_snapshots (
                collected_at, fx_rate, us_session, kr_session, payload_json
            ) VALUES (?, ?, ?, ?, ?)
        """, (
            collected_at,
            _to_float(fx.get("rate")),
            ((sessions.get("US") or {}).get("session")),
            ((sessions.get("KRX_NXT") or {}).get("session")),
            _json({
                "exchange_rate": evidence.get("exchange_rate"),
                "sessions": sessions,
                "errors": evidence.get("errors"),
            }),
        ))

        for event in events or []:
            self.save_event(event, commit=False)
        self._commit()

    def save_event(self, event, commit=True):
        self._execute_write("""
            INSERT INTO event_logs (
                detected_at, symbol, event_type, severity, message, value, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            event.get("detected_at") or _now(),
            event.get("symbol"),
            event.get("event_type"),
            event.get("severity") or "info",
            event.get("message") or "",
            _to_float(event.get("value")),
            _json(event),
        ))
        if commit:
            self._commit()

    def save_analysis_result(self, evidence, gpt, events=None, mode="focused_watchlist"):
        usage = (gpt or {}).get("usage") or {}
        cursor = self._execute_write("""
            INSERT INTO analysis_results (
                analyzed_at, mode, symbols, model, prompt_tokens, completion_tokens,
                total_tokens, gpt_analysis, evidence_json, events_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            _now(),
            mode,
            ",".join(evidence.get("symbols") or []),
            (gpt or {}).get("model"),
            _to_int(usage.get("prompt_tokens")),
            _to_int(usage.get("completion_tokens")),
            _to_int(usage.get("total_tokens")),
            (gpt or {}).get("analysis"),
            _json(evidence),
            _json(events or []),
        ))
        self._commit()
        return cursor.lastrowid

    def create_paper_candidates(
        self,
        analysis_id,
        evidence,
        horizons=(5, 10, 30, 60),
        cooldown_minutes=DEFAULT_PAPER_CANDIDATE_COOLDOWN_MINUTES,
    ):
        created = 0
        collected_at = evidence.get("collected_at") or _now()
        created_at = _now()
        cooldown_start = _sqlite_datetime_plus_minutes(created_at, -int(cooldown_minutes or 0))
        decisions = {
            str(row["symbol"] or "").upper(): str(row["final_decision"] or "UNKNOWN").upper()
            for row in self.conn.execute(
                "SELECT symbol, final_decision FROM structured_analysis WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchall()
        }
        for item in ((evidence.get("prices") or {}).get("result") or []):
            symbol = str(item.get("symbol") or "").upper()
            price = _to_float(item.get("lastPrice"))
            if not symbol or price <= 0:
                continue
            decision = decisions.get(symbol, "UNKNOWN")
            for horizon in horizons:
                recent = self.conn.execute("""
                    SELECT p.id
                    FROM paper_trade_candidates p
                    LEFT JOIN structured_analysis s
                        ON s.analysis_id = p.analysis_id AND s.symbol = p.symbol
                    WHERE p.symbol = ? AND p.horizon_min = ?
                      AND p.created_at >= ?
                      AND UPPER(COALESCE(s.final_decision, 'UNKNOWN')) = ?
                    ORDER BY p.created_at DESC, p.id DESC
                    LIMIT 1
                """, (symbol, int(horizon), cooldown_start, decision)).fetchone()
                if recent:
                    continue
                due_at = _sqlite_datetime_plus_minutes(collected_at, horizon)
                self._execute_write("""
                    INSERT INTO paper_trade_candidates (
                        created_at, analysis_id, symbol, anchor_price, horizon_min,
                        due_at, status, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    created_at,
                    analysis_id,
                    symbol,
                    price,
                    int(horizon),
                    due_at,
                    "pending",
                    _json({
                        "source_timestamp": item.get("timestamp"),
                        "currency": item.get("currency"),
                        "final_decision": decision,
                        "candidate_cooldown_minutes": int(cooldown_minutes or 0),
                    }),
                ))
                created += 1
        self._commit()
        return created

    def evaluate_due_paper_candidates(self, close_price_fallback=False, include_future_due=False):
        now = _now()
        if include_future_due:
            rows = self.conn.execute("""
                SELECT * FROM paper_trade_candidates
                WHERE status = 'pending'
                ORDER BY due_at ASC
            """).fetchall()
        else:
            rows = self.conn.execute("""
                SELECT * FROM paper_trade_candidates
                WHERE status = 'pending' AND due_at <= ?
                ORDER BY due_at ASC
            """, (now,)).fetchall()
        evaluated = 0
        for row in rows:
            latest = self.conn.execute("""
                SELECT price FROM price_snapshots
                WHERE symbol = ? AND collected_at >= ?
                ORDER BY collected_at DESC
                LIMIT 1
            """, (row["symbol"], row["due_at"])).fetchone()
            if not latest and close_price_fallback:
                latest = self.conn.execute("""
                    SELECT price FROM price_snapshots
                    WHERE symbol = ? AND collected_at <= ?
                    ORDER BY collected_at DESC
                    LIMIT 1
                """, (row["symbol"], now)).fetchone()
            if not latest:
                continue
            latest_price = _to_float(latest["price"])
            anchor = _to_float(row["anchor_price"])
            if latest_price <= 0 or anchor <= 0:
                continue
            return_pct = ((latest_price - anchor) / anchor) * 100.0
            interval_rows = self.conn.execute("""
                SELECT price FROM price_snapshots
                WHERE symbol = ? AND collected_at >= ? AND collected_at <= ?
                ORDER BY collected_at ASC
            """, (row["symbol"], row["created_at"], now)).fetchall()
            returns = [((_to_float(item["price"]) - anchor) / anchor) * 100.0 for item in interval_rows if _to_float(item["price"]) > 0]
            max_return = max(returns) if returns else return_pct
            min_return = min(returns) if returns else return_pct
            outcome = "win" if return_pct > 0 else "loss" if return_pct < 0 else "flat"
            self._execute_write("""
                UPDATE paper_trade_candidates
                SET status = 'evaluated', result_return_pct = ?, max_return_pct = ?,
                    min_return_pct = ?, outcome = ?, evaluated_at = ?
                WHERE id = ?
            """, (round(return_pct, 4), round(max_return, 4), round(min_return, 4), outcome, now, row["id"]))
            evaluated += 1
        self._commit()
        return evaluated

    def create_paper_execution_orders(self, analysis_id, structured, created_at=None):
        """Persist paper/shadow plans and guard rejections without broker calls."""
        created_at = created_at or _now()
        analysis = self.conn.execute(
            "SELECT analyzed_at FROM analysis_results WHERE id = ?", (analysis_id,)
        ).fetchone()
        if analysis and analysis["analyzed_at"]:
            # A GPT plan cannot be filled before its decision was available.
            created_at = max(created_at, analysis["analyzed_at"])
        created = 0
        for item in structured or []:
            symbol = str(item.get("symbol") or "").upper()
            plan = item.get("trade_plan") or {}
            if not symbol or not plan:
                continue
            initial = simulate_order(plan, [], created_at, asof=created_at)
            cursor = self._execute_write("""
                INSERT OR IGNORE INTO paper_execution_orders (
                    created_at, updated_at, analysis_id, symbol, plan_status,
                    status, risk_multiplier, adaptive_state, remaining_fraction,
                    assumption_version, orders_called, plan_json, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """, (
                created_at,
                _now(),
                analysis_id,
                symbol,
                (plan.get("entry") or {}).get("status"),
                initial.get("status"),
                initial.get("risk_multiplier"),
                initial.get("adaptive_state"),
                initial.get("remaining_fraction"),
                ASSUMPTION_VERSION,
                _json(plan),
                _json(initial),
            ))
            if cursor.rowcount:
                order_id = cursor.lastrowid
                self._save_paper_execution_events(order_id, initial.get("events") or [])
                created += 1
        self._commit()
        return created

    def advance_paper_execution_orders(self, now=None, limit=1000):
        """Replay stored prices and advance every active paper position."""
        now = now or _now()
        terminal = sorted(TERMINAL_STATES)
        placeholders = ",".join("?" for _ in terminal)
        rows = self.conn.execute("""
            SELECT *
            FROM paper_execution_orders
            WHERE status NOT IN ({})
            ORDER BY created_at ASC, id ASC
            LIMIT ?
        """.format(placeholders), terminal + [int(limit)]).fetchall()
        advanced = 0
        closed = 0
        for row in rows:
            plan = _load_json(row["plan_json"])
            points = self._paper_execution_price_path(
                row["symbol"], row["created_at"], now,
            )
            result = simulate_order(plan, points, row["created_at"], asof=now)
            if (result.get("status") == row["status"] and not result.get("terminal")
                    and row["result_json"] == _json(result)):
                continue
            self._execute_write("""
                UPDATE paper_execution_orders
                SET updated_at = ?, status = ?, risk_multiplier = ?,
                    adaptive_state = ?, entry_filled_at = ?, entry_price = ?,
                    exit_at = ?, exit_price = ?, remaining_fraction = ?,
                    gross_return_pct = ?, net_return_pct = ?, mfe_pct = ?,
                    mae_pct = ?, exit_reason = ?, orders_called = 0,
                    result_json = ?
                WHERE id = ?
            """, (
                _now(), result.get("status"), result.get("risk_multiplier"),
                result.get("adaptive_state"), result.get("entry_filled_at"),
                result.get("entry_price"), result.get("exit_at"),
                result.get("average_exit_price"), result.get("remaining_fraction"),
                result.get("gross_return_pct"), result.get("net_return_pct"),
                result.get("mfe_pct"), result.get("mae_pct"),
                result.get("exit_reason"), _json(result), row["id"],
            ))
            self._save_paper_execution_events(row["id"], result.get("events") or [])
            advanced += 1
            if result.get("terminal"):
                closed += 1
        self._commit()
        return {"advanced": advanced, "closed": closed}

    def paper_execution_readiness(self, symbol=None):
        params = []
        where = ""
        if symbol:
            where = "WHERE symbol = ?"
            params.append(str(symbol).upper())
        rows = self.conn.execute("""
            SELECT status, entry_filled_at, exit_at, updated_at, net_return_pct
            FROM paper_execution_orders
            {}
            ORDER BY COALESCE(exit_at, updated_at) ASC, id ASC
        """.format(where), params).fetchall()
        return build_readiness_report(rows)

    def _paper_execution_price_path(self, symbol, start_at, end_at):
        rows = self.conn.execute("""
            SELECT collected_at AS at, price
            FROM price_snapshots
            WHERE symbol = ? AND collected_at >= ? AND collected_at <= ?
            UNION ALL
            SELECT collected_at AS at, price
            FROM trade_ticks
            WHERE symbol = ? AND collected_at >= ? AND collected_at <= ?
            ORDER BY at ASC
        """, (symbol, start_at, end_at, symbol, start_at, end_at)).fetchall()
        return [dict(row) for row in rows]

    def _save_paper_execution_events(self, order_id, events):
        for event in events or []:
            event_key = "{}|{}|{}|{}|{}|{}".format(
                order_id, event.get("event_type"), event.get("event_at"),
                event.get("price"), event.get("state"), event.get("fraction"),
            )
            self._execute_write("""
                INSERT OR IGNORE INTO paper_execution_events (
                    paper_order_id, event_key, event_at, event_type, state,
                    price, fraction, note, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order_id, event_key, event.get("event_at"),
                event.get("event_type"), event.get("state"), event.get("price"),
                event.get("fraction"), event.get("note"), _json(event),
            ))
    def paper_feedback_summary(self, limit=200):
        rows = self.conn.execute("""
            SELECT symbol, horizon_min, result_return_pct, max_return_pct, min_return_pct, outcome
            FROM paper_trade_candidates
            WHERE status = 'evaluated'
            ORDER BY evaluated_at DESC
            LIMIT ?
        """, (int(limit),)).fetchall()
        grouped = {}
        for row in rows:
            key = "{}:{}m".format(row["symbol"], row["horizon_min"])
            bucket = grouped.setdefault(key, {
                "symbol": row["symbol"],
                "horizon_min": row["horizon_min"],
                "count": 0,
                "wins": 0,
                "losses": 0,
                "sum_return_pct": 0.0,
                "sum_win_return_pct": 0.0,
                "sum_loss_return_pct": 0.0,
                "best_return_pct": None,
                "worst_return_pct": None,
                "best_path_return_pct": None,
                "worst_path_return_pct": None,
            })
            value = _to_float(row["result_return_pct"])
            max_value = _to_float(row["max_return_pct"])
            min_value = _to_float(row["min_return_pct"])
            bucket["count"] += 1
            if value > 0:
                bucket["wins"] += 1
                bucket["sum_win_return_pct"] += value
            elif value < 0:
                bucket["losses"] += 1
                bucket["sum_loss_return_pct"] += value
            bucket["sum_return_pct"] += value
            bucket["best_return_pct"] = value if bucket["best_return_pct"] is None else max(bucket["best_return_pct"], value)
            bucket["worst_return_pct"] = value if bucket["worst_return_pct"] is None else min(bucket["worst_return_pct"], value)
            bucket["best_path_return_pct"] = max_value if bucket["best_path_return_pct"] is None else max(bucket["best_path_return_pct"], max_value)
            bucket["worst_path_return_pct"] = min_value if bucket["worst_path_return_pct"] is None else min(bucket["worst_path_return_pct"], min_value)
        summary = []
        for item in grouped.values():
            count = item["count"]
            wins = item["wins"]
            losses = item["losses"]
            avg_win = item["sum_win_return_pct"] / wins if wins else 0.0
            avg_loss = item["sum_loss_return_pct"] / losses if losses else 0.0
            loss_abs = abs(avg_loss)
            summary.append({
                "symbol": item["symbol"],
                "horizon_min": item["horizon_min"],
                "count": count,
                "win_rate": round(item["wins"] / count, 4) if count else 0.0,
                "avg_return_pct": round(item["sum_return_pct"] / count, 4) if count else 0.0,
                "avg_win_return_pct": round(avg_win, 4),
                "avg_loss_return_pct": round(avg_loss, 4),
                "payoff_ratio": round(avg_win / loss_abs, 4) if loss_abs > 0 else 0.0,
                "best_return_pct": round(item["best_return_pct"] or 0.0, 4),
                "worst_return_pct": round(item["worst_return_pct"] or 0.0, 4),
                "best_path_return_pct": round(item["best_path_return_pct"] or 0.0, 4),
                "worst_path_return_pct": round(item["worst_path_return_pct"] or 0.0, 4),
            })
        summary.sort(key=lambda item: (item["symbol"], item["horizon_min"]))
        return summary

    def return_feedback_by_symbol(self, limit=300):
        rows = self.paper_feedback_summary(limit=limit)
        grouped = {}
        for row in rows:
            symbol = row.get("symbol")
            if not symbol:
                continue
            bucket = grouped.setdefault(symbol, {
                "symbol": symbol,
                "samples": 0,
                "weighted_return_pct": 0.0,
                "weighted_win_rate": 0.0,
                "best_return_pct": None,
                "worst_return_pct": None,
                "best_path_return_pct": None,
                "worst_path_return_pct": None,
                "horizons": [],
            })
            count = _to_int(row.get("count"))
            bucket["samples"] += count
            bucket["weighted_return_pct"] += _to_float(row.get("avg_return_pct")) * count
            bucket["weighted_win_rate"] += _to_float(row.get("win_rate")) * count
            bucket["best_return_pct"] = _merge_max(bucket["best_return_pct"], _to_float(row.get("best_return_pct")))
            bucket["worst_return_pct"] = _merge_min(bucket["worst_return_pct"], _to_float(row.get("worst_return_pct")))
            bucket["best_path_return_pct"] = _merge_max(bucket["best_path_return_pct"], _to_float(row.get("best_path_return_pct")))
            bucket["worst_path_return_pct"] = _merge_min(bucket["worst_path_return_pct"], _to_float(row.get("worst_path_return_pct")))
            bucket["horizons"].append(row)
        result = {}
        for symbol, item in grouped.items():
            samples = item["samples"]
            result[symbol] = {
                "symbol": symbol,
                "samples": samples,
                "avg_return_pct": round(item["weighted_return_pct"] / samples, 4) if samples else 0.0,
                "win_rate": round(item["weighted_win_rate"] / samples, 4) if samples else 0.0,
                "best_return_pct": round(item["best_return_pct"] or 0.0, 4),
                "worst_return_pct": round(item["worst_return_pct"] or 0.0, 4),
                "best_path_return_pct": round(item["best_path_return_pct"] or 0.0, 4),
                "worst_path_return_pct": round(item["worst_path_return_pct"] or 0.0, 4),
                "horizons": item["horizons"],
            }
        return result

    def save_structured_analysis(self, analysis_id, structured):
        for item in structured or []:
            self._execute_write("""
                INSERT INTO structured_analysis (
                    analysis_id, symbol, final_decision, interest_score, risk_level,
                    confidence, summary, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                analysis_id,
                item.get("symbol"),
                item.get("final_decision"),
                _to_int(item.get("interest_score")),
                item.get("risk_level"),
                item.get("confidence"),
                item.get("summary"),
                _json(item),
            ))
        self._commit()

    def latest_structured_by_symbol(self, symbols):
        result = {}
        for symbol in symbols or []:
            row = self.conn.execute("""
                SELECT structured_analysis.*, analysis_results.analyzed_at
                FROM structured_analysis
                LEFT JOIN analysis_results ON analysis_results.id = structured_analysis.analysis_id
                WHERE structured_analysis.symbol = ?
                ORDER BY structured_analysis.analysis_id DESC, structured_analysis.id DESC
                LIMIT 1
            """, (str(symbol).upper(),)).fetchone()
            result[str(symbol).upper()] = dict(row) if row else None
        return result

    def operational_summary(self):
        tables = {}
        table_count_quality = {}
        for table in [
            "price_snapshots",
            "candle_snapshots",
            "market_context_snapshots",
            "event_logs",
            "analysis_results",
            "paper_trade_candidates",
            "structured_analysis",
            "paper_execution_orders",
            "paper_execution_events",
            "domestic_feedback_summary",
            "domestic_signal_summary",
            "market_relationship_observations",
            "trade_ticks",
            "orderbook_snapshots",
            "tick_analysis_snapshots",
        ]:
            count, exact, high_watermark = self._summary_row_count(table)
            tables[table] = count
            table_count_quality[table] = {
                "exact": exact,
                "high_watermark_id": high_watermark,
            }
        latest_analysis = self.conn.execute("""
            SELECT analyzed_at, symbols, model, total_tokens
            FROM analysis_results
            ORDER BY id DESC LIMIT 1
        """).fetchone()
        paper_status = [
            {"status": row["status"], "count": row["count"]}
            for row in self.conn.execute("SELECT status, COUNT(1) AS count FROM paper_trade_candidates GROUP BY status").fetchall()
        ]
        top_events = self._recent_top_events()
        return {
            "db_path": os.path.abspath(self.db_path),
            "tables": tables,
            "table_count_quality": table_count_quality,
            "latest_analysis": dict(latest_analysis) if latest_analysis else None,
            "paper_status": paper_status,
            "top_events": top_events,
            "top_events_scope": {
                "basis": "recent_rows",
                "row_limit": OPS_EVENT_SAMPLE_LIMIT,
            },
            "paper_feedback": self.paper_feedback_summary(),
            "quant_feedback": build_quant_feedback_snapshot(self.conn),
            "deployment_readiness": self.paper_execution_readiness(),
            "health": build_runtime_health(self),
        }

    def _recent_top_events(self, row_limit=OPS_EVENT_SAMPLE_LIMIT, result_limit=10):
        """Summarize a bounded recent event window for responsive ops refreshes."""
        rows = self.conn.execute("""
            SELECT event_type, COUNT(1) AS count
            FROM (
                SELECT event_type
                FROM event_logs
                ORDER BY id DESC
                LIMIT ?
            ) AS recent_events
            GROUP BY event_type
            ORDER BY count DESC, event_type ASC
            LIMIT ?
        """, (int(row_limit), int(result_limit))).fetchall()
        return [
            {"event_type": row["event_type"], "count": row["count"]}
            for row in rows
        ]

    def upsert_domestic_feedback_summary(self, rows):
        imported_at = _now()
        count = 0
        for row in rows or []:
            self._execute_write("""
                INSERT INTO domestic_feedback_summary (
                    source, code, name, horizon_min, sample_count, win_rate,
                    avg_return_pct, avg_win_return_pct, avg_loss_return_pct,
                    best_return_pct, worst_return_pct, best_path_return_pct,
                    worst_path_return_pct, imported_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, code, horizon_min) DO UPDATE SET
                    name = excluded.name,
                    sample_count = excluded.sample_count,
                    win_rate = excluded.win_rate,
                    avg_return_pct = excluded.avg_return_pct,
                    avg_win_return_pct = excluded.avg_win_return_pct,
                    avg_loss_return_pct = excluded.avg_loss_return_pct,
                    best_return_pct = excluded.best_return_pct,
                    worst_return_pct = excluded.worst_return_pct,
                    best_path_return_pct = excluded.best_path_return_pct,
                    worst_path_return_pct = excluded.worst_path_return_pct,
                    imported_at = excluded.imported_at,
                    payload_json = excluded.payload_json
            """, (
                row.get("source") or "unknown",
                str(row.get("code") or "").strip(),
                row.get("name"),
                _to_int(row.get("horizon_min")),
                _to_int(row.get("sample_count")),
                _to_float(row.get("win_rate")),
                _to_float(row.get("avg_return_pct")),
                _to_float(row.get("avg_win_return_pct")),
                _to_float(row.get("avg_loss_return_pct")),
                _to_float(row.get("best_return_pct")),
                _to_float(row.get("worst_return_pct")),
                _to_float(row.get("best_path_return_pct")),
                _to_float(row.get("worst_path_return_pct")),
                imported_at,
                _json(row),
            ))
            count += 1
        self._commit()
        return count

    def upsert_domestic_signal_summary(self, rows):
        imported_at = _now()
        count = 0
        for row in rows or []:
            self._execute_write("""
                INSERT INTO domestic_signal_summary (
                    source, code, name, latest_detected_at, latest_action_hint,
                    latest_confidence_score, latest_risk_level, signal_count,
                    imported_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, code) DO UPDATE SET
                    name = excluded.name,
                    latest_detected_at = excluded.latest_detected_at,
                    latest_action_hint = excluded.latest_action_hint,
                    latest_confidence_score = excluded.latest_confidence_score,
                    latest_risk_level = excluded.latest_risk_level,
                    signal_count = excluded.signal_count,
                    imported_at = excluded.imported_at,
                    payload_json = excluded.payload_json
            """, (
                row.get("source") or "unknown",
                str(row.get("code") or "").strip(),
                row.get("name"),
                row.get("latest_detected_at"),
                row.get("latest_action_hint"),
                _to_float(row.get("latest_confidence_score")),
                row.get("latest_risk_level"),
                _to_int(row.get("signal_count")),
                imported_at,
                _json(row),
            ))
            count += 1
        self._commit()
        return count

    def domestic_snapshot(self, codes=None):
        codes = [str(item).strip() for item in codes or [] if str(item).strip()]
        if not codes:
            rows = self.conn.execute("""
                SELECT code FROM domestic_feedback_summary
                UNION
                SELECT code FROM domestic_signal_summary
                ORDER BY code
            """).fetchall()
            codes = [row["code"] for row in rows]
        result = []
        for code in codes:
            feedback_rows = self.conn.execute("""
                SELECT * FROM domestic_feedback_summary
                WHERE code = ?
                ORDER BY horizon_min
            """, (code,)).fetchall()
            signal = self.conn.execute("""
                SELECT * FROM domestic_signal_summary
                WHERE code = ?
                ORDER BY imported_at DESC, id DESC
                LIMIT 1
            """, (code,)).fetchone()
            name = None
            if signal and signal["name"]:
                name = signal["name"]
            elif feedback_rows and feedback_rows[0]["name"]:
                name = feedback_rows[0]["name"]
            result.append({
                "code": code,
                "name": name or "",
                "source": (feedback_rows[0]["source"] if feedback_rows else (signal["source"] if signal else "")),
                "feedback": [dict(row) for row in feedback_rows],
                "signal": dict(signal) if signal else None,
            })
        return result

    def save_relationship_observations(self, rows):
        count = 0
        for row in rows or []:
            self._execute_write("""
                INSERT INTO market_relationship_observations (
                    observed_at, source_market, source_symbol, target_market,
                    target_symbol, source_return_pct, target_return_pct,
                    lag_label, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row.get("observed_at") or _now(),
                row.get("source_market") or "KR",
                str(row.get("source_symbol") or "").strip(),
                row.get("target_market") or "US",
                str(row.get("target_symbol") or "").strip().upper(),
                _to_float(row.get("source_return_pct")),
                _to_float(row.get("target_return_pct")),
                row.get("lag_label") or "same_session",
                _json(row),
            ))
            count += 1
        self._commit()
        return count

    def delete_relationship_observations(self, domestic_codes=None, us_symbols=None, lag_labels=None):
        domestic_codes = [str(item).strip() for item in domestic_codes or [] if str(item).strip()]
        us_symbols = [str(item).strip().upper() for item in us_symbols or [] if str(item).strip()]
        lag_labels = [str(item).strip() for item in lag_labels or [] if str(item).strip()]
        clauses = []
        params = []
        if domestic_codes:
            clauses.append("source_symbol IN ({})".format(",".join("?" for _ in domestic_codes)))
            params.extend(domestic_codes)
        if us_symbols:
            clauses.append("target_symbol IN ({})".format(",".join("?" for _ in us_symbols)))
            params.extend(us_symbols)
        if lag_labels:
            clauses.append("lag_label IN ({})".format(",".join("?" for _ in lag_labels)))
            params.extend(lag_labels)
        if not clauses:
            return 0
        cursor = self._execute_write(
            "DELETE FROM market_relationship_observations WHERE {}".format(" AND ".join(clauses)),
            params,
        )
        self._commit()
        return cursor.rowcount

    def relationship_observations(self, domestic_codes=None, us_symbols=None, limit=100000, compact=False):
        domestic_codes = [str(item).strip() for item in domestic_codes or [] if str(item).strip()]
        us_symbols = [str(item).strip().upper() for item in us_symbols or [] if str(item).strip()]
        clauses = []
        params = []
        if domestic_codes:
            clauses.append("source_symbol IN ({})".format(",".join("?" for _ in domestic_codes)))
            params.extend(domestic_codes)
        if us_symbols:
            clauses.append("target_symbol IN ({})".format(",".join("?" for _ in us_symbols)))
            params.extend(us_symbols)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        columns = "*"
        if compact:
            columns = """
                id, observed_at, source_market, source_symbol, target_market,
                target_symbol, source_return_pct, target_return_pct, lag_label,
                CASE WHEN json_valid(payload_json) THEN json_extract(payload_json, '$.driver_return_pct') END AS driver_return_pct,
                CASE WHEN json_valid(payload_json) THEN json_extract(payload_json, '$.response_return_pct') END AS response_return_pct,
                CASE WHEN json_valid(payload_json) THEN json_extract(payload_json, '$.driver_symbol') END AS driver_symbol,
                CASE WHEN json_valid(payload_json) THEN json_extract(payload_json, '$.response_symbol') END AS response_symbol,
                CASE WHEN json_valid(payload_json) THEN json_extract(payload_json, '$.observation_timeframe') END AS observation_timeframe,
                CASE WHEN json_valid(payload_json) THEN json_extract(payload_json, '$.resolution') END AS resolution,
                CASE WHEN json_valid(payload_json) THEN json_extract(payload_json, '$.data_source') END AS data_source,
                CASE WHEN json_valid(payload_json) THEN json_extract(payload_json, '$.observation_granularity') END AS observation_granularity,
                CASE WHEN json_valid(payload_json) THEN json_extract(payload_json, '$.intraday_source') END AS intraday_source,
                CASE WHEN json_valid(payload_json) THEN json_extract(payload_json, '$.tick_source') END AS tick_source,
                CASE WHEN json_valid(payload_json) THEN json_extract(payload_json, '$.resolution_warning') END AS resolution_warning
            """
        params.append(int(limit))
        rows = self.conn.execute("""
            SELECT {columns}
            FROM market_relationship_observations
            {where}
            ORDER BY observed_at DESC, id DESC
            LIMIT ?
        """.format(columns=columns, where=where), params).fetchall()
        return [dict(row) for row in reversed(rows)]

    def relationship_observation_marker(self, domestic_codes=None, us_symbols=None):
        domestic_codes = [str(item).strip() for item in domestic_codes or [] if str(item).strip()]
        us_symbols = [str(item).strip().upper() for item in us_symbols or [] if str(item).strip()]
        clauses = []
        params = []
        if domestic_codes:
            clauses.append("source_symbol IN ({})".format(",".join("?" for _ in domestic_codes)))
            params.extend(domestic_codes)
        if us_symbols:
            clauses.append("target_symbol IN ({})".format(",".join("?" for _ in us_symbols)))
            params.extend(us_symbols)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        row = self.conn.execute("""
            SELECT COUNT(1) AS row_count, MAX(id) AS max_id, MAX(observed_at) AS latest_observed_at
            FROM market_relationship_observations
            {where}
        """.format(where=where), params).fetchone()
        return tuple(row) if row else (0, None, None)
    def save_trade_ticks(self, symbol, trades_payload, collected_at=None, source="toss_trades"):
        collected_at = collected_at or _now()
        symbol = str(symbol or "").upper()
        rows = _payload_rows(trades_payload)
        inserted = 0
        for row in rows:
            cursor = self._execute_write("""
                INSERT OR IGNORE INTO trade_ticks (
                    collected_at, symbol, trade_timestamp, price, volume,
                    side, currency, source, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                collected_at,
                symbol,
                _pick(row, "timestamp", "tradeTimestamp", "executedAt", "time"),
                _to_float(_pick(row, "price", "lastPrice", "tradePrice", "executionPrice")),
                _to_float(_pick(row, "volume", "quantity", "tradeVolume", "executionVolume")),
                _pick(row, "side", "tradeSide", "executionSide"),
                _pick(row, "currency"),
                source,
                _json(row),
            ))
            inserted += cursor.rowcount
        self._commit()
        return inserted

    def save_price_poll_tick(self, symbol, price_row, collected_at=None):
        collected_at = collected_at or _now()
        row = price_row or {}
        payload = {
            "timestamp": row.get("timestamp") or collected_at,
            "price": row.get("lastPrice") or row.get("price"),
            "volume": row.get("volume") or 0,
            "currency": row.get("currency"),
            "source_symbol": row.get("symbol"),
        }
        return self.save_trade_ticks(symbol, {"result": [payload]}, collected_at=collected_at, source="price_poll_fallback")

    def save_orderbook_snapshot(self, symbol, orderbook_payload, collected_at=None):
        collected_at = collected_at or _now()
        symbol = str(symbol or "").upper()
        item = _payload_object(orderbook_payload)
        bids = item.get("bids") or []
        asks = item.get("asks") or []
        best_bid, bid_volume = _best_level(bids, prefer_high=True)
        best_ask, ask_volume = _best_level(asks, prefer_high=False)
        spread = best_ask - best_bid if best_ask > 0 and best_bid > 0 else 0.0
        midpoint = (best_ask + best_bid) / 2.0 if best_ask > 0 and best_bid > 0 else 0.0
        spread_pct = (spread / midpoint) * 100.0 if midpoint > 0 else 0.0
        total_depth = bid_volume + ask_volume
        imbalance = ((bid_volume - ask_volume) / total_depth) if total_depth > 0 else 0.0
        self._execute_write("""
            INSERT INTO orderbook_snapshots (
                collected_at, symbol, source_timestamp, currency, best_bid,
                best_ask, bid_volume, ask_volume, spread, spread_pct,
                imbalance, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            collected_at,
            symbol,
            item.get("timestamp"),
            item.get("currency"),
            best_bid,
            best_ask,
            bid_volume,
            ask_volume,
            spread,
            spread_pct,
            imbalance,
            _json(item),
        ))
        self._commit()
        return 1

    def save_tick_analysis(self, analysis):
        analysis = analysis or {}
        self._execute_write("""
            INSERT INTO tick_analysis_snapshots (
                analyzed_at, symbol, trade_count, latest_price, oldest_price,
                price_change_pct, volume_sum, best_bid, best_ask, spread_pct,
                orderbook_imbalance, signal, severity, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            analysis.get("analyzed_at") or _now(),
            str(analysis.get("symbol") or "").upper(),
            _to_int(analysis.get("trade_count")),
            _to_float(analysis.get("latest_price")),
            _to_float(analysis.get("oldest_price")),
            _to_float(analysis.get("price_change_pct")),
            _to_float(analysis.get("volume_sum")),
            _to_float(analysis.get("best_bid")),
            _to_float(analysis.get("best_ask")),
            _to_float(analysis.get("spread_pct")),
            _to_float(analysis.get("orderbook_imbalance")),
            analysis.get("signal"),
            analysis.get("severity"),
            _json(analysis),
        ))
        self._commit()
        return 1

    def latest_tick_analysis(self, symbols=None):
        symbols = [str(item).upper() for item in symbols or [] if str(item).strip()]
        result = {}
        if not symbols:
            rows = self.conn.execute("""
                SELECT symbol FROM tick_analysis_snapshots
                GROUP BY symbol
                ORDER BY symbol
            """).fetchall()
            symbols = [row["symbol"] for row in rows]
        for symbol in symbols:
            row = self.conn.execute("""
                SELECT *
                FROM tick_analysis_snapshots
                WHERE symbol = ?
                ORDER BY analyzed_at DESC, id DESC
                LIMIT 1
            """, (symbol,)).fetchone()
            result[symbol] = dict(row) if row else None
        return result

    def recent_trade_ticks(self, symbol, limit=50):
        rows = self.conn.execute("""
            SELECT *
            FROM trade_ticks
            WHERE symbol = ?
            ORDER BY trade_timestamp DESC, collected_at DESC, id DESC
            LIMIT ?
        """, (str(symbol).upper(), int(limit))).fetchall()
        return [dict(row) for row in rows]

    def latest_orderbook_snapshot(self, symbol):
        row = self.conn.execute("""
            SELECT *
            FROM orderbook_snapshots
            WHERE symbol = ?
            ORDER BY collected_at DESC, id DESC
            LIMIT 1
        """, (str(symbol).upper(),)).fetchone()
        return dict(row) if row else None

    def count_rows(self, table):
        row = self.conn.execute("SELECT COUNT(1) AS count FROM {}".format(table)).fetchone()
        return int(row["count"])

    def _summary_row_count(self, table, limit=100000):
        if table != "trade_ticks":
            count = self.count_rows(table)
            return count, True, count
        row = self.conn.execute(
            "SELECT COUNT(1) AS count FROM (SELECT id FROM trade_ticks LIMIT ?)",
            (int(limit) + 1,),
        ).fetchone()
        observed = int(row["count"] or 0)
        high = self.conn.execute("SELECT MAX(id) AS value FROM trade_ticks").fetchone()
        high_watermark = int(high["value"] or 0)
        if observed <= int(limit):
            return observed, True, high_watermark
        return int(limit), False, high_watermark

    def close(self):
        self.conn.close()


def _load_json(value):
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}

def _json(value):
    return json.dumps(sanitize_payload(value), ensure_ascii=False, default=str)


def _is_locked_error(exc):
    text = str(exc).lower()
    return "database is locked" in text or "database table is locked" in text


def _payload_rows(payload):
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if isinstance(result, list):
        return [row for row in result if isinstance(row, dict)]
    if isinstance(result, dict):
        for key in ("trades", "items", "data"):
            rows = result.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        return [result]
    return []


def _payload_object(payload):
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict):
            return result
        return payload
    return {}


def _pick(row, *keys):
    for key in keys:
        value = (row or {}).get(key)
        if value not in (None, ""):
            return value
    return None


def _best_level(levels, prefer_high):
    parsed = []
    for row in levels or []:
        if not isinstance(row, dict):
            continue
        price = _to_float(row.get("price"))
        volume = _to_float(row.get("volume"))
        if price > 0:
            parsed.append((price, volume))
    if not parsed:
        return 0.0, 0.0
    parsed.sort(key=lambda item: item[0], reverse=prefer_high)
    return parsed[0]


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


def _sqlite_datetime_plus_minutes(value, minutes):
    try:
        text = str(value).split(".")[0]
        dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        dt = datetime.now()
    return (dt.replace(microsecond=0) + timedelta(minutes=int(minutes))).strftime("%Y-%m-%d %H:%M:%S.%f")


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


def _merge_max(left, right):
    if left is None:
        return right
    return max(left, right)


def _merge_min(left, right):
    if left is None:
        return right
    return min(left, right)
