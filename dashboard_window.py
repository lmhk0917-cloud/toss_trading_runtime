"""Tkinter desktop dashboard for the Toss focused runtime."""

import argparse
import json
import os
import sys
import tkinter as tk
import webbrowser
from tkinter import ttk

try:
    from . import config
    from .dashboard import DEFAULT_DASHBOARD_PATH, build_dashboard_snapshot, format_summary_text, render_dashboard_html
    from .store import TossRuntimeStore
except ImportError:  # pragma: no cover
    import config
    from dashboard import DEFAULT_DASHBOARD_PATH, build_dashboard_snapshot, format_summary_text, render_dashboard_html
    from store import TossRuntimeStore


DEFAULT_SYMBOLS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "reports",
    "dashboard_symbols.json",
)
DEFAULT_DOMESTIC_SYMBOLS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "reports",
    "dashboard_domestic_symbols.json",
)


class TossDashboardWindow(object):
    def __init__(
        self,
        root,
        symbols,
        db_path=None,
        refresh_sec=30,
        symbols_path=DEFAULT_SYMBOLS_PATH,
        domestic_symbols=None,
        domestic_symbols_path=DEFAULT_DOMESTIC_SYMBOLS_PATH,
    ):
        self.root = root
        self.symbols = symbols
        self.domestic_symbols = domestic_symbols or ["005930", "000660"]
        self.db_path = db_path
        self.symbols_path = symbols_path
        self.domestic_symbols_path = domestic_symbols_path
        self.refresh_ms = max(5, int(refresh_sec)) * 1000
        self.snapshot = {}
        self.root.title("Toss Focused Dashboard")
        self.root.geometry("1280x820")
        self.root.minsize(980, 640)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        self.root.configure(bg="#f6f7f9")
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=28, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("TButton", font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI", 16, "bold"), background="#ffffff", foreground="#1d2430")
        style.configure("Meta.TLabel", font=("Segoe UI", 9), background="#ffffff", foreground="#667085")
        style.configure("Metric.TFrame", background="#ffffff", relief="solid", borderwidth=1)
        style.configure("MetricLabel.TLabel", font=("Segoe UI", 9), background="#ffffff", foreground="#667085")
        style.configure("MetricValue.TLabel", font=("Segoe UI", 14, "bold"), background="#ffffff", foreground="#1d2430")

        header = ttk.Frame(self.root, padding=(16, 12))
        header.pack(fill="x")
        ttk.Label(header, text="Toss Focused Dashboard", style="Header.TLabel").pack(anchor="w")
        self.meta_var = tk.StringVar(value="Loading...")
        ttk.Label(header, textvariable=self.meta_var, style="Meta.TLabel").pack(anchor="w", pady=(3, 0))

        toolbar = ttk.Frame(self.root, padding=(16, 8))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Refresh", command=self.refresh).pack(side="left")
        ttk.Button(toolbar, text="Export HTML", command=self.export_html).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Open HTML", command=self.open_html).pack(side="left", padx=(4, 0))
        ttk.Label(toolbar, text="Symbols").pack(side="left", padx=(12, 4))
        self.symbols_var = tk.StringVar(value=",".join(self.symbols))
        self.symbols_entry = ttk.Entry(toolbar, textvariable=self.symbols_var, width=34)
        self.symbols_entry.pack(side="left")
        ttk.Button(toolbar, text="Apply", command=self.apply_symbols).pack(side="left", padx=(4, 0))
        ttk.Label(toolbar, text="KR").pack(side="left", padx=(12, 4))
        self.domestic_symbols_var = tk.StringVar(value=",".join(self.domestic_symbols))
        self.domestic_symbols_entry = ttk.Entry(toolbar, textvariable=self.domestic_symbols_var, width=22)
        self.domestic_symbols_entry.pack(side="left")
        ttk.Button(toolbar, text="Apply KR", command=self.apply_domestic_symbols).pack(side="left", padx=(4, 0))
        ttk.Button(toolbar, text="Quit", command=self.root.destroy).pack(side="right")
        self.status_var = tk.StringVar(value="")
        ttk.Label(toolbar, textvariable=self.status_var).pack(side="left", padx=(12, 0))

        metrics = ttk.Frame(self.root, padding=(16, 4))
        metrics.pack(fill="x")
        self.metric_vars = {}
        for key, label in [
            ("health", "Health"),
            ("latest_analysis", "Latest Analysis"),
            ("tokens", "Tokens"),
            ("warnings", "Warnings"),
        ]:
            frame = ttk.Frame(metrics, style="Metric.TFrame", padding=(12, 10))
            frame.pack(side="left", fill="x", expand=True, padx=(0, 10))
            ttk.Label(frame, text=label, style="MetricLabel.TLabel").pack(anchor="w")
            var = tk.StringVar(value="-")
            self.metric_vars[key] = var
            ttk.Label(frame, textvariable=var, style="MetricValue.TLabel").pack(anchor="w", pady=(4, 0))

        panes = ttk.Panedwindow(self.root, orient="vertical")
        panes.pack(fill="both", expand=True, padx=16, pady=(8, 16))

        top = ttk.Frame(panes)
        bottom = ttk.Notebook(panes)
        panes.add(top, weight=3)
        panes.add(bottom, weight=2)

        columns = ("symbol", "decision", "score", "delta", "avg_ret", "win", "worst_path", "risk", "confidence", "price", "m1", "vol", "d1")
        self.symbol_tree = ttk.Treeview(top, columns=columns, show="headings", selectmode="browse")
        headings = {
            "symbol": "Symbol",
            "decision": "Decision",
            "score": "Score",
            "delta": "Delta",
            "avg_ret": "Avg Ret",
            "win": "Win",
            "worst_path": "Worst Path",
            "risk": "Risk",
            "confidence": "Confidence",
            "price": "Price",
            "m1": "1m %",
            "vol": "1m Vol",
            "d1": "1d %",
        }
        widths = {
            "symbol": 90,
            "decision": 110,
            "score": 70,
            "delta": 70,
            "avg_ret": 80,
            "win": 70,
            "worst_path": 90,
            "risk": 90,
            "confidence": 100,
            "price": 100,
            "m1": 80,
            "vol": 80,
            "d1": 80,
        }
        for col in columns:
            self.symbol_tree.heading(col, text=headings[col])
            self.symbol_tree.column(col, width=widths[col], anchor="e" if col not in ("symbol", "decision", "risk", "confidence") else "w")
        self.symbol_tree.pack(fill="both", expand=True, side="left")
        yscroll = ttk.Scrollbar(top, orient="vertical", command=self.symbol_tree.yview)
        self.symbol_tree.configure(yscrollcommand=yscroll.set)
        yscroll.pack(fill="y", side="right")
        self.symbol_tree.bind("<<TreeviewSelect>>", self._on_symbol_selected)

        summary_frame = ttk.Frame(bottom, padding=8)
        detail_frame = ttk.Frame(bottom, padding=8)
        score_frame = ttk.Frame(bottom, padding=8)
        chart_frame = ttk.Frame(bottom, padding=8)
        paper_frame = ttk.Frame(bottom, padding=8)
        gpt_frame = ttk.Frame(bottom, padding=8)
        events_frame = ttk.Frame(bottom, padding=8)
        domestic_frame = ttk.Frame(bottom, padding=8)
        context_frame = ttk.Frame(bottom, padding=8)
        tables_frame = ttk.Frame(bottom, padding=8)
        bottom.add(summary_frame, text="Summary")
        bottom.add(detail_frame, text="Details")
        bottom.add(score_frame, text="Score Trend")
        bottom.add(chart_frame, text="Minute Chart")
        bottom.add(paper_frame, text="Paper")
        bottom.add(gpt_frame, text="GPT By Symbol")
        bottom.add(events_frame, text="Events By Symbol")
        bottom.add(domestic_frame, text="Domestic KR")
        bottom.add(context_frame, text="Context")
        bottom.add(tables_frame, text="Tables")

        self.summary_text = tk.Text(summary_frame, wrap="word", height=9, font=("Segoe UI", 10), relief="solid", borderwidth=1)
        self.summary_text.pack(fill="both", expand=True)
        self.summary_text.configure(state="disabled")

        self.detail_text = tk.Text(detail_frame, wrap="word", height=9, font=("Consolas", 10), relief="solid", borderwidth=1)
        self.detail_text.pack(fill="both", expand=True)
        self.detail_text.configure(state="disabled")

        self.score_canvas = tk.Canvas(score_frame, height=180, bg="#ffffff", highlightthickness=1, highlightbackground="#d9dee7")
        self.score_canvas.pack(fill="both", expand=True)

        self.chart_canvas = tk.Canvas(chart_frame, height=260, bg="#ffffff", highlightthickness=1, highlightbackground="#d9dee7")
        self.chart_canvas.pack(fill="both", expand=True)

        paper_columns = ("created", "symbol", "horizon", "anchor", "status", "return", "max", "min", "outcome")
        self.paper_tree = ttk.Treeview(paper_frame, columns=paper_columns, show="headings")
        for col, width in [
            ("created", 170), ("symbol", 80), ("horizon", 80), ("anchor", 90),
            ("status", 90), ("return", 90), ("max", 90), ("min", 90), ("outcome", 90),
        ]:
            self.paper_tree.heading(col, text=col.title())
            self.paper_tree.column(col, width=width, anchor="e" if col not in ("created", "symbol", "status", "outcome") else "w")
        self.paper_tree.pack(fill="both", expand=True)

        self.gpt_text = tk.Text(gpt_frame, wrap="word", height=9, font=("Segoe UI", 10), relief="solid", borderwidth=1)
        self.gpt_text.pack(fill="both", expand=True)
        self.gpt_text.configure(state="disabled")

        event_toolbar = ttk.Frame(events_frame)
        event_toolbar.pack(fill="x", pady=(0, 6))
        self.selected_events_only_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            event_toolbar,
            text="Selected symbol only",
            variable=self.selected_events_only_var,
            command=self._render_events,
        ).pack(side="left")

        event_columns = ("time", "symbol", "event", "severity", "value", "message")
        self.event_tree = ttk.Treeview(events_frame, columns=event_columns, show="headings")
        for col, width in [("time", 180), ("symbol", 80), ("event", 180), ("severity", 90), ("value", 90), ("message", 520)]:
            self.event_tree.heading(col, text=col.title())
            self.event_tree.column(col, width=width, anchor="w")
        self.event_tree.pack(fill="both", expand=True)

        domestic_columns = ("code", "name", "samples", "avg5", "avg10", "avg30", "avg60", "win", "worst", "signal")
        self.domestic_tree = ttk.Treeview(domestic_frame, columns=domestic_columns, show="headings")
        domestic_headings = {
            "code": "Code",
            "name": "Name",
            "samples": "Samples",
            "avg5": "5m Avg",
            "avg10": "10m Avg",
            "avg30": "30m Avg",
            "avg60": "60m Avg",
            "win": "Win",
            "worst": "Worst Path",
            "signal": "Latest Signal",
        }
        for col, width in [
            ("code", 90), ("name", 120), ("samples", 80), ("avg5", 80), ("avg10", 80),
            ("avg30", 80), ("avg60", 80), ("win", 70), ("worst", 90), ("signal", 360),
        ]:
            self.domestic_tree.heading(col, text=domestic_headings[col])
            self.domestic_tree.column(col, width=width, anchor="e" if col not in ("code", "name", "signal") else "w")
        self.domestic_tree.pack(fill="both", expand=True)

        self.context_text = tk.Text(context_frame, wrap="word", height=8, font=("Consolas", 10), relief="solid", borderwidth=1)
        self.context_text.pack(fill="both", expand=True)
        self.context_text.configure(state="disabled")

        self.tables_text = tk.Text(tables_frame, wrap="none", height=8, font=("Consolas", 10), relief="solid", borderwidth=1)
        self.tables_text.pack(fill="both", expand=True)
        self.tables_text.configure(state="disabled")

    def refresh(self):
        try:
            store = TossRuntimeStore(db_path=self.db_path) if self.db_path else TossRuntimeStore()
            try:
                self.snapshot = build_dashboard_snapshot(
                    store,
                    symbols=self.symbols,
                    domestic_symbols=self.domestic_symbols,
                )
            finally:
                store.close()
            self._render_snapshot()
            self.status_var.set("Last refresh ok")
        except Exception as exc:
            self.status_var.set("Refresh failed: {}".format(exc))
        self.root.after(self.refresh_ms, self.refresh)

    def _render_snapshot(self):
        snapshot = self.snapshot or {}
        summary = snapshot.get("summary") or {}
        health = summary.get("health") or {}
        latest = snapshot.get("latest_analysis") or {}
        warnings = health.get("warnings") or []
        self.meta_var.set("Generated {} | DB {}".format(snapshot.get("generated_at"), snapshot.get("db_path")))
        self.metric_vars["health"].set(health.get("status") or "unknown")
        self.metric_vars["latest_analysis"].set(latest.get("analyzed_at") or "none")
        self.metric_vars["tokens"].set(str(latest.get("total_tokens") or 0))
        self.metric_vars["warnings"].set(str(len(warnings)))

        self.symbol_tree.delete(*self.symbol_tree.get_children())
        for row in snapshot.get("symbols") or []:
            feedback = row.get("return_feedback") or {}
            self.symbol_tree.insert("", "end", iid=row.get("symbol"), values=(
                row.get("symbol"),
                row.get("decision"),
                _fmt(row.get("interest_score"), 0),
                _fmt(row.get("score_delta"), 0, signed=True),
                _fmt(feedback.get("avg_return_pct"), 4, signed=True),
                _fmt(feedback.get("win_rate"), 2),
                _fmt(feedback.get("worst_path_return_pct"), 4, signed=True),
                row.get("risk_level"),
                row.get("confidence"),
                _fmt(row.get("price"), 2),
                _fmt(row.get("minute_change_pct"), 2, signed=True),
                _fmt(row.get("minute_volume_ratio"), 2),
                _fmt(row.get("daily_change_pct"), 2, signed=True),
            ))
        children = self.symbol_tree.get_children()
        if children and not self.symbol_tree.selection():
            self.symbol_tree.selection_set(children[0])
            self._show_symbol_details(children[0])

        self._render_events()
        self._render_domestic()

        self.paper_tree.delete(*self.paper_tree.get_children())
        for idx, paper in enumerate(snapshot.get("paper_candidates") or []):
            self.paper_tree.insert("", "end", iid=str(idx), values=(
                paper.get("created_at"),
                paper.get("symbol"),
                paper.get("horizon_min"),
                _fmt(paper.get("anchor_price"), 2),
                paper.get("status"),
                _fmt(paper.get("result_return_pct"), 4, signed=True),
                _fmt(paper.get("max_return_pct"), 4, signed=True),
                _fmt(paper.get("min_return_pct"), 4, signed=True),
                paper.get("outcome") or "",
            ))

        selection = self.symbol_tree.selection()
        if selection:
            self._show_gpt_for(selection[0])

        tables = summary.get("tables") or {}
        health_checks = health.get("checks") or {}
        text = ["Tables"]
        for key in sorted(tables):
            text.append("{:<32} {}".format(key, tables[key]))
        text.append("")
        text.append("Health Checks")
        for key in sorted(health_checks):
            text.append("{:<32} {}".format(key, health_checks[key]))
        if warnings:
            text.append("")
            text.append("Warnings")
            text.extend(str(item) for item in warnings)
        self._set_text(self.tables_text, "\n".join(text))
        self._render_context()

    def _on_symbol_selected(self, _event=None):
        selection = self.symbol_tree.selection()
        if selection:
            self._show_symbol_details(selection[0])

    def _show_symbol_details(self, symbol):
        for row in self.snapshot.get("symbols") or []:
            if row.get("symbol") == symbol:
                self._set_text(self.summary_text, format_summary_text(row.get("summary") or ""))
                self._set_text(self.detail_text, self._detail_text_for(row))
                self._draw_score_history(symbol)
                self._draw_minute_chart(row)
                self._show_gpt_for(symbol)
                self._render_events()
                return
        self._set_text(self.summary_text, "")
        self._set_text(self.detail_text, "")
        self._draw_score_history(None)
        self._draw_minute_chart(None)
        self._show_gpt_for(None)
        self._render_events()

    def apply_symbols(self):
        symbols = [item.strip().upper() for item in self.symbols_var.get().split(",") if item.strip()]
        if not symbols:
            self.status_var.set("Symbols cannot be empty")
            return
        self.symbols = symbols
        save_symbols(symbols, self.symbols_path)
        self.status_var.set("Saved symbols: {}".format(",".join(symbols)))
        self.symbol_tree.selection_remove(*self.symbol_tree.selection())
        self.refresh()

    def apply_domestic_symbols(self):
        symbols = [item.strip() for item in self.domestic_symbols_var.get().split(",") if item.strip()]
        if not symbols:
            self.status_var.set("KR symbols cannot be empty")
            return
        self.domestic_symbols = symbols
        save_symbols(symbols, self.domestic_symbols_path)
        self.status_var.set("Saved KR symbols: {}".format(",".join(symbols)))
        self.refresh()

    def _show_gpt_for(self, symbol):
        latest_gpt = (self.snapshot or {}).get("latest_gpt") or {}
        sections = (self.snapshot or {}).get("gpt_sections") or {}
        header = "Analysis #{} | {} | {} | tokens {}\n".format(
            latest_gpt.get("id") or "-",
            latest_gpt.get("analyzed_at") or "-",
            latest_gpt.get("model") or "-",
            latest_gpt.get("total_tokens") or 0,
        )
        if symbol:
            body = sections.get(symbol) or "No GPT section found for {}.".format(symbol)
            text = "{}Symbol: {}\n\n{}".format(header, symbol, body)
        else:
            text = "{}\n{}".format(header, latest_gpt.get("gpt_analysis") or "")
        self._set_text(self.gpt_text, text)

    def export_html(self):
        if not self.snapshot:
            self.status_var.set("No snapshot to export")
            return
        path = os.path.abspath(DEFAULT_DASHBOARD_PATH)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(render_dashboard_html(self.snapshot))
            self.status_var.set("Exported {}".format(path))
        except Exception as exc:
            self.status_var.set("Export failed: {}".format(exc))

    def open_html(self):
        self.export_html()
        path = os.path.abspath(DEFAULT_DASHBOARD_PATH)
        if os.path.exists(path):
            webbrowser.open("file:///" + path.replace("\\", "/"))

    def _render_events(self):
        if not hasattr(self, "event_tree"):
            return
        selected = None
        selection = self.symbol_tree.selection() if hasattr(self, "symbol_tree") else []
        if selection:
            selected = selection[0]
        if self.selected_events_only_var.get() and selected:
            events = ((self.snapshot or {}).get("symbol_events") or {}).get(selected) or []
        else:
            events = (self.snapshot or {}).get("recent_events") or []
        self.event_tree.delete(*self.event_tree.get_children())
        for idx, event in enumerate(events):
            self.event_tree.insert("", "end", iid=str(idx), values=(
                event.get("detected_at"),
                event.get("symbol"),
                event.get("event_type"),
                event.get("severity"),
                _fmt(event.get("value"), 2),
                event.get("message"),
            ))

    def _render_domestic(self):
        if not hasattr(self, "domestic_tree"):
            return
        self.domestic_tree.delete(*self.domestic_tree.get_children())
        for row in (self.snapshot or {}).get("domestic") or []:
            horizons = {int(item.get("horizon_min") or 0): item for item in row.get("feedback") or []}
            sample_count = sum(_to_int(item.get("sample_count")) for item in row.get("feedback") or [])
            total_win_weight = sum(_to_float(item.get("win_rate")) * _to_int(item.get("sample_count")) for item in row.get("feedback") or [])
            win_rate = total_win_weight / sample_count if sample_count else 0.0
            worst_values = [_to_float(item.get("worst_path_return_pct")) for item in row.get("feedback") or []]
            worst_path = min(worst_values) if worst_values else 0.0
            signal = row.get("signal") or {}
            signal_text = "{} | score {} | risk {} | count {} | {}".format(
                signal.get("latest_action_hint") or "-",
                _fmt(signal.get("latest_confidence_score"), 0),
                signal.get("latest_risk_level") or "-",
                signal.get("signal_count") or 0,
                signal.get("latest_detected_at") or "-",
            )
            self.domestic_tree.insert("", "end", iid=row.get("code"), values=(
                row.get("code"),
                row.get("name"),
                sample_count,
                _fmt((horizons.get(5) or {}).get("avg_return_pct"), 4, signed=True),
                _fmt((horizons.get(10) or {}).get("avg_return_pct"), 4, signed=True),
                _fmt((horizons.get(30) or {}).get("avg_return_pct"), 4, signed=True),
                _fmt((horizons.get(60) or {}).get("avg_return_pct"), 4, signed=True),
                _fmt(win_rate, 4),
                _fmt(worst_path, 4, signed=True),
                signal_text,
            ))

    def _render_context(self):
        context = (self.snapshot or {}).get("latest_context") or {}
        payload = _parse_json(context.get("payload_json"))
        lines = [
            "Latest Market Context",
            "  collected_at: {}".format(context.get("collected_at") or "-"),
            "  fx_rate: {}".format(_fmt(context.get("fx_rate"), 4)),
            "  us_session: {}".format(context.get("us_session") or "-"),
            "  kr_session: {}".format(context.get("kr_session") or "-"),
            "",
            "Health Latest Rows",
        ]
        latest = (((self.snapshot or {}).get("summary") or {}).get("health") or {}).get("latest") or {}
        for key in ("price", "candle", "context"):
            row = latest.get(key) or {}
            lines.append("  {}: {}".format(key, row.get("collected_at") or row.get("latest_timestamp") or "-"))
        if payload:
            lines.append("")
            lines.append("Context Payload")
            for key in sorted(payload):
                lines.append("  {}: {}".format(key, payload.get(key)))
        self._set_text(self.context_text, "\n".join(lines))

    def _detail_text_for(self, row):
        detail = row.get("detail") or {}
        minute = detail.get("minute") or {}
        daily = detail.get("daily") or {}
        previous = detail.get("previous_analysis") or {}
        latest_price = detail.get("latest_price") or {}
        lines = [
            "Symbol: {}".format(row.get("symbol")),
            "Decision: {} | Score: {} | Delta: {}".format(row.get("decision"), _fmt(row.get("interest_score"), 0), _fmt(row.get("score_delta"), 0, signed=True)),
            "Risk: {} | Confidence: {}".format(row.get("risk_level"), row.get("confidence")),
            "Price: {} {} at {}".format(_fmt(row.get("price"), 2), latest_price.get("currency") or "", row.get("price_time")),
            "Return Feedback: samples={} avg={} win={} best={} worst={} worst_path={}".format(
                (row.get("return_feedback") or {}).get("samples"),
                _fmt((row.get("return_feedback") or {}).get("avg_return_pct"), 4, signed=True),
                _fmt((row.get("return_feedback") or {}).get("win_rate"), 4),
                _fmt((row.get("return_feedback") or {}).get("best_return_pct"), 4, signed=True),
                _fmt((row.get("return_feedback") or {}).get("worst_return_pct"), 4, signed=True),
                _fmt((row.get("return_feedback") or {}).get("worst_path_return_pct"), 4, signed=True),
            ),
            "",
            "1m Candle",
            "  sample={} latest={} change={} volume_ratio={} rsi={} vwap_dist={}".format(
                minute.get("sample"), _fmt(minute.get("latest_close"), 2), _fmt(minute.get("change_pct"), 4, signed=True),
                _fmt(minute.get("volume_ratio"), 4), _fmt(minute.get("rsi14"), 2), _fmt(minute.get("vwap_distance_pct"), 4, signed=True),
            ),
            "  ma5={} ma20={} ma_spread={} range_pos={}".format(
                _fmt(minute.get("ma5"), 2), _fmt(minute.get("ma20"), 2),
                _fmt(minute.get("ma5_vs_ma20_pct"), 4, signed=True), _fmt(minute.get("range_position_pct"), 2),
            ),
            "",
            "1d Candle",
            "  sample={} latest={} change={} volume_ratio={} rsi={} vwap_dist={}".format(
                daily.get("sample"), _fmt(daily.get("latest_close"), 2), _fmt(daily.get("change_pct"), 4, signed=True),
                _fmt(daily.get("volume_ratio"), 4), _fmt(daily.get("rsi14"), 2), _fmt(daily.get("vwap_distance_pct"), 4, signed=True),
            ),
            "  ma5={} ma20={} ma_spread={} range_pos={}".format(
                _fmt(daily.get("ma5"), 2), _fmt(daily.get("ma20"), 2),
                _fmt(daily.get("ma5_vs_ma20_pct"), 4, signed=True), _fmt(daily.get("range_position_pct"), 2),
            ),
            "",
            "Previous Analysis",
            "  decision={} score={} risk={} confidence={} at {}".format(
                previous.get("final_decision"), previous.get("interest_score"),
                previous.get("risk_level"), previous.get("confidence"), previous.get("analyzed_at"),
            ),
        ]
        return "\n".join(lines)

    def _draw_score_history(self, symbol):
        canvas = self.score_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 400)
        height = max(canvas.winfo_height(), 180)
        pad = 34
        canvas.create_text(12, 12, anchor="nw", text="Interest score history: {}".format(symbol or "-"), fill="#1d2430", font=("Segoe UI", 10, "bold"))
        canvas.create_line(pad, height - pad, width - pad, height - pad, fill="#d9dee7")
        canvas.create_line(pad, pad, pad, height - pad, fill="#d9dee7")
        for score in (0, 50, 100):
            y = height - pad - (score / 100.0) * (height - 2 * pad)
            canvas.create_line(pad, y, width - pad, y, fill="#eef2f6")
            canvas.create_text(8, y, anchor="w", text=str(score), fill="#667085", font=("Segoe UI", 8))
        if not symbol:
            return
        history = ((self.snapshot.get("score_history") or {}).get(symbol) or [])
        points = []
        for idx, item in enumerate(history):
            score = item.get("interest_score")
            if score is None:
                continue
            x = pad if len(history) <= 1 else pad + (idx / float(len(history) - 1)) * (width - 2 * pad)
            y = height - pad - (float(score) / 100.0) * (height - 2 * pad)
            points.append((x, y, item))
        if len(points) >= 2:
            coords = []
            for x, y, _item in points:
                coords.extend([x, y])
            canvas.create_line(*coords, fill="#1b64d8", width=2)
        for x, y, item in points:
            canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#1b64d8", outline="#1b64d8")
            canvas.create_text(x, y - 12, text=str(item.get("interest_score")), fill="#1d2430", font=("Segoe UI", 8))

    def _draw_minute_chart(self, row):
        canvas = self.chart_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 520)
        height = max(canvas.winfo_height(), 260)
        pad_l = 54
        pad_r = 24
        pad_t = 28
        pad_b = 34
        symbol = (row or {}).get("symbol") or "-"
        series = (row or {}).get("minute_series") or []
        canvas.create_text(12, 10, anchor="nw", text="1m close chart: {}".format(symbol), fill="#1d2430", font=("Segoe UI", 10, "bold"))
        if len(series) < 2:
            canvas.create_text(width / 2, height / 2, text="Not enough 1m close data", fill="#667085", font=("Segoe UI", 11))
            return
        values = [_to_float(item.get("latest_close")) for item in series if _to_float(item.get("latest_close")) > 0]
        if len(values) < 2:
            canvas.create_text(width / 2, height / 2, text="No valid close values", fill="#667085", font=("Segoe UI", 11))
            return
        low = min(values)
        high = max(values)
        if high <= low:
            high = low + 1.0
        x0 = pad_l
        x1 = width - pad_r
        y0 = pad_t
        y1 = height - pad_b

        def y_for(price):
            return y1 - ((price - low) / (high - low)) * (y1 - y0)

        def x_for(index):
            return x0 + (index / float(len(values) - 1)) * (x1 - x0)

        canvas.create_rectangle(x0, y0, x1, y1, outline="#d9dee7")
        levels = [
            ("R", high, "#b42318"),
            ("Fib 23.6", high - (high - low) * 0.236, "#b54708"),
            ("Fib 38.2", high - (high - low) * 0.382, "#b54708"),
            ("Fib 50.0", high - (high - low) * 0.500, "#667085"),
            ("Fib 61.8", high - (high - low) * 0.618, "#b54708"),
            ("S", low, "#147a42"),
        ]
        for label, price, color in levels:
            y = y_for(price)
            canvas.create_line(x0, y, x1, y, fill=color, dash=(4, 3))
            canvas.create_text(8, y, anchor="w", text="{} {:.2f}".format(label, price), fill=color, font=("Segoe UI", 8))

        coords = []
        for idx, price in enumerate(values):
            coords.extend([x_for(idx), y_for(price)])
        canvas.create_line(*coords, fill="#1b64d8", width=2)
        last_x = x_for(len(values) - 1)
        last_y = y_for(values[-1])
        canvas.create_oval(last_x - 4, last_y - 4, last_x + 4, last_y + 4, fill="#1b64d8", outline="#1b64d8")
        canvas.create_text(last_x - 4, last_y - 14, anchor="e", text="{:.2f}".format(values[-1]), fill="#1d2430", font=("Segoe UI", 9, "bold"))

    def _set_text(self, widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Open Toss focused dashboard window.")
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--refresh-sec", type=int, default=30)
    parser.add_argument("--symbols-path", default=DEFAULT_SYMBOLS_PATH)
    parser.add_argument("--domestic-symbols", default=None)
    parser.add_argument("--domestic-symbols-path", default=DEFAULT_DOMESTIC_SYMBOLS_PATH)
    args = parser.parse_args(argv)
    if args.symbols:
        symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
        save_symbols(symbols, args.symbols_path)
    else:
        symbols = load_symbols(args.symbols_path) or list(config.FOCUSED_NASDAQ_WATCHLIST)
    if args.domestic_symbols:
        domestic_symbols = [item.strip() for item in args.domestic_symbols.split(",") if item.strip()]
        save_symbols(domestic_symbols, args.domestic_symbols_path)
    else:
        domestic_symbols = load_symbols(args.domestic_symbols_path) or ["005930", "000660"]
    root = tk.Tk()
    TossDashboardWindow(
        root,
        symbols=symbols,
        db_path=args.db_path,
        refresh_sec=args.refresh_sec,
        symbols_path=args.symbols_path,
        domestic_symbols=domestic_symbols,
        domestic_symbols_path=args.domestic_symbols_path,
    )
    root.mainloop()
    return 0


def load_symbols(path=DEFAULT_SYMBOLS_PATH):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        symbols = payload.get("symbols") if isinstance(payload, dict) else payload
        return [str(item).strip().upper() for item in symbols or [] if str(item).strip()]
    except (IOError, OSError, TypeError, ValueError):
        return []


def save_symbols(symbols, path=DEFAULT_SYMBOLS_PATH):
    cleaned = [str(item).strip().upper() for item in symbols or [] if str(item).strip()]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp_path = os.path.abspath(path) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump({"symbols": cleaned}, handle, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, os.path.abspath(path))
    return cleaned


def _fmt(value, decimals=2, signed=False):
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if decimals == 0:
        text = str(int(round(number)))
    else:
        text = ("{0:." + str(decimals) + "f}").format(number)
    if signed and number > 0:
        return "+" + text
    return text


def _to_float(value):
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value):
    try:
        if value in (None, ""):
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _parse_json(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


if __name__ == "__main__":
    sys.exit(main())
