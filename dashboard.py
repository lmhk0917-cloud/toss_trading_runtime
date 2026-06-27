"""Static HTML dashboard for the Toss focused runtime."""

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime

try:
    from .relationship_analysis import build_relationship_evidence
    from . import config
    from .store import TossRuntimeStore
except ImportError:  # pragma: no cover
    from relationship_analysis import build_relationship_evidence
    import config
    from store import TossRuntimeStore


DEFAULT_DASHBOARD_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "reports",
    "dashboard_latest.html",
)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build Toss focused runtime dashboard HTML.")
    parser.add_argument("--symbols", default=",".join(config.FOCUSED_NASDAQ_WATCHLIST))
    parser.add_argument("--domestic-symbols", default="005930,000660")
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--html", default=DEFAULT_DASHBOARD_PATH)
    args = parser.parse_args(argv)

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    domestic_symbols = [item.strip() for item in args.domestic_symbols.split(",") if item.strip()]
    store = TossRuntimeStore(db_path=args.db_path) if args.db_path else TossRuntimeStore()
    try:
        snapshot = build_dashboard_snapshot(store, symbols=symbols, domestic_symbols=domestic_symbols)
    finally:
        store.close()

    path = os.path.abspath(args.html)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render_dashboard_html(snapshot))
    print("TOSS_DASHBOARD_STATUS=ok")
    print("TOSS_DASHBOARD_HTML={}".format(path))
    return 0


def build_dashboard_snapshot(store, symbols=None, domestic_symbols=None):
    symbols = [str(item).upper() for item in symbols or []]
    if not symbols:
        symbols = _symbols_from_db(store)
    summary = store.operational_summary()
    return_feedback = store.return_feedback_by_symbol()
    structured = store.latest_structured_by_symbol(symbols)
    tick_analysis = store.latest_tick_analysis(symbols)
    rows = []
    for symbol in symbols:
        latest_price = _latest_price(store, symbol)
        minute = _latest_candle(store, symbol, "1m")
        daily = _latest_candle(store, symbol, "1d")
        item = structured.get(symbol) or {}
        previous = _previous_structured(store, symbol, item.get("analysis_id"))
        current_score = _to_int(item.get("interest_score"))
        previous_score = _to_int(previous.get("interest_score"))
        rows.append({
            "symbol": symbol,
            "decision": item.get("final_decision") or "unknown",
            "interest_score": current_score,
            "score_delta": _delta(current_score, previous_score),
            "risk_level": item.get("risk_level") or "unknown",
            "confidence": item.get("confidence") or "unknown",
            "price": _to_float(latest_price.get("price")),
            "price_time": latest_price.get("collected_at"),
            "minute_change_pct": _to_float(minute.get("change_pct")),
            "minute_volume_ratio": _to_float(minute.get("volume_ratio")),
            "daily_change_pct": _to_float(daily.get("change_pct")),
            "paper": _paper_summary_for_symbol(store, symbol),
            "return_feedback": return_feedback.get(symbol, {
                "samples": 0,
                "avg_return_pct": 0.0,
                "win_rate": 0.0,
                "best_return_pct": 0.0,
                "worst_return_pct": 0.0,
                "best_path_return_pct": 0.0,
                "worst_path_return_pct": 0.0,
                "horizons": [],
            }),
            "summary": _display_summary(item.get("summary") or "", symbol),
            "detail": {
                "minute": _parse_json_payload(minute.get("payload_json")),
                "daily": _parse_json_payload(daily.get("payload_json")),
                "latest_price": latest_price,
                "previous_analysis": previous,
                "tick_analysis": tick_analysis.get(symbol) or {},
                "recent_trades": store.recent_trade_ticks(symbol, limit=20),
                "orderbook": store.latest_orderbook_snapshot(symbol) or {},
            },
            "minute_series": _minute_close_series(store, symbol),
            "tick_analysis": tick_analysis.get(symbol) or {},
        })
    latest_gpt = _latest_gpt_analysis(store, mode="focused_watchlist")
    domestic_gpt = _latest_gpt_analysis(store, mode="domestic_kr")
    domestic_rows = _domestic_with_analysis(store, store.domestic_snapshot(codes=domestic_symbols))
    relationship = build_relationship_evidence(store, domestic_codes=domestic_symbols, us_symbols=symbols)
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "db_path": os.path.abspath(store.db_path),
        "summary": summary,
        "symbols": rows,
        "recent_events": _recent_events(store),
        "symbol_events": _recent_events_by_symbol(store, symbols=symbols),
        "paper_candidates": _recent_paper_candidates(store, symbols=symbols),
        "paper_feedback": summary.get("paper_feedback") or [],
        "return_feedback": return_feedback,
        "score_history": _score_history(store, symbols=symbols),
        "latest_gpt": latest_gpt,
        "gpt_sections": _gpt_sections_by_symbol((latest_gpt or {}).get("gpt_analysis"), symbols),
        "domestic": domestic_rows,
        "relationship": relationship,
        "shared_context_status": _shared_context_status(),
        "domestic_latest_gpt": domestic_gpt,
        "domestic_gpt_sections": _gpt_sections_by_symbol((domestic_gpt or {}).get("gpt_analysis"), domestic_symbols or []),
        "latest_context": _latest_context(store),
        "latest_analysis": summary.get("latest_analysis"),
    }


def render_dashboard_html(snapshot):
    summary = snapshot.get("summary") or {}
    health = summary.get("health") or {}
    tables = summary.get("tables") or {}
    latest = snapshot.get("latest_analysis") or {}
    symbol_rows = "\n".join(_symbol_row(row) for row in snapshot.get("symbols") or [])
    tick_rows = "\n".join(_tick_row((row.get("tick_analysis") or {})) for row in snapshot.get("symbols") or [])
    event_rows = "\n".join(_event_row(row) for row in snapshot.get("recent_events") or [])
    feedback_rows = "\n".join(_feedback_row(row) for row in snapshot.get("paper_feedback") or [])
    domestic_rows = "\n".join(_domestic_row(row) for row in snapshot.get("domestic") or [])
    relationship_rows = "\n".join(_relationship_row(row) for row in ((snapshot.get("relationship") or {}).get("pairs") or []))
    context = snapshot.get("latest_context") or {}
    relationship = snapshot.get("relationship") or {}
    shared_context = snapshot.get("shared_context_status") or {}
    table_cells = "\n".join(
        "<tr><td>{}</td><td>{}</td></tr>".format(_e(key), _e(value))
        for key, value in sorted(tables.items())
    )
    warnings = health.get("warnings") or []
    warning_text = ", ".join(warnings) if warnings else "none"
    status_class = "status-ok" if health.get("status") == "ok" else "status-warn"
    return """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Toss Focused Dashboard</title>
<style>
:root {{
  --bg: #f6f7f9;
  --panel: #ffffff;
  --line: #d9dee7;
  --text: #1d2430;
  --muted: #667085;
  --blue: #1b64d8;
  --green: #147a42;
  --red: #b42318;
  --amber: #b54708;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: Segoe UI, Arial, sans-serif;
  letter-spacing: 0;
}}
header {{
  padding: 18px 24px 14px;
  background: var(--panel);
  border-bottom: 1px solid var(--line);
}}
h1 {{ margin: 0 0 6px; font-size: 22px; font-weight: 650; }}
h2 {{ margin: 0 0 10px; font-size: 16px; font-weight: 650; }}
.meta {{ color: var(--muted); font-size: 13px; }}
main {{ padding: 18px 24px 28px; display: grid; gap: 16px; }}
section {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
}}
.metrics {{
  display: grid;
  grid-template-columns: repeat(4, minmax(140px, 1fr));
  gap: 10px;
}}
.metric {{
  border-left: 4px solid var(--blue);
  background: #fbfcfe;
  padding: 10px 12px;
  min-height: 68px;
}}
.metric .label {{ color: var(--muted); font-size: 12px; }}
.metric .value {{ margin-top: 5px; font-size: 20px; font-weight: 650; }}
.status-ok {{ color: var(--green); font-weight: 650; }}
.status-warn {{ color: var(--amber); font-weight: 650; }}
table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
th, td {{
  padding: 9px 8px;
  border-bottom: 1px solid var(--line);
  vertical-align: top;
  text-align: left;
  font-size: 13px;
  overflow-wrap: anywhere;
}}
th {{ color: #344054; font-size: 12px; background: #f9fafb; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.pos {{ color: var(--green); font-weight: 600; }}
.neg {{ color: var(--red); font-weight: 600; }}
.muted {{ color: var(--muted); }}
.pill {{
  display: inline-block;
  min-width: 58px;
  padding: 3px 7px;
  border-radius: 999px;
  background: #eef4ff;
  color: #1849a9;
  text-align: center;
  font-size: 12px;
  font-weight: 650;
}}
.risk-HIGH {{ background: #fef3f2; color: var(--red); }}
.risk-MEDIUM {{ background: #fffaeb; color: var(--amber); }}
.risk-LOW {{ background: #ecfdf3; color: var(--green); }}
.summary {{ color: var(--muted); line-height: 1.4; max-height: 76px; overflow: hidden; }}
@media (max-width: 900px) {{
  header, main {{ padding-left: 12px; padding-right: 12px; }}
  .metrics {{ grid-template-columns: repeat(2, minmax(120px, 1fr)); }}
  table {{ table-layout: auto; }}
  th, td {{ font-size: 12px; }}
}}
</style>
</head>
<body>
<header>
  <h1>Toss Focused Dashboard</h1>
  <div class="meta">Generated {generated_at} | DB {db_path}</div>
</header>
<main>
  <section>
    <h2>Runtime</h2>
    <div class="metrics">
      <div class="metric"><div class="label">Health</div><div class="value {status_class}">{health_status}</div></div>
      <div class="metric"><div class="label">Latest Analysis</div><div class="value">{latest_time}</div></div>
      <div class="metric"><div class="label">Tokens</div><div class="value">{latest_tokens}</div></div>
      <div class="metric"><div class="label">Warnings</div><div class="value">{warning_count}</div></div>
    </div>
    <p class="meta">Warnings: {warning_text}</p>
  </section>
  <section>
    <h2>Focused Symbols</h2>
    <table>
      <thead>
        <tr>
          <th>Symbol</th><th>Decision</th><th class="num">Score</th><th class="num">Delta</th>
          <th>Risk</th><th>Confidence</th><th class="num">Price</th>
          <th class="num">1m %</th><th class="num">1m Vol</th><th class="num">1d %</th>
          <th>Paper</th><th>Summary</th>
        </tr>
      </thead>
      <tbody>{symbol_rows}</tbody>
    </table>
  </section>
  <section>
    <h2>Tick Flow</h2>
    <table>
      <thead><tr><th>Symbol</th><th>Analyzed</th><th class="num">Trades</th><th class="num">Tick %</th><th class="num">Volume</th><th class="num">Bid</th><th class="num">Ask</th><th class="num">Spread %</th><th class="num">Imbalance</th><th>Signal</th><th>Quality</th></tr></thead>
      <tbody>{tick_rows}</tbody>
    </table>
  </section>
  <section>
    <h2>Recent Events</h2>
    <table>
      <thead><tr><th>Time</th><th>Symbol</th><th>Event</th><th>Severity</th><th class="num">Value</th><th>Message</th></tr></thead>
      <tbody>{event_rows}</tbody>
    </table>
  </section>
  <section>
    <h2>Paper Feedback</h2>
    <table>
      <thead><tr><th>Symbol</th><th class="num">Horizon</th><th class="num">Count</th><th class="num">Win</th><th class="num">Avg %</th><th class="num">Best %</th><th class="num">Worst %</th></tr></thead>
      <tbody>{feedback_rows}</tbody>
    </table>
  </section>
  <section>
    <h2>Domestic Market</h2>
    <table>
      <thead><tr><th>Code</th><th>Name</th><th>Source</th><th class="num">Samples</th><th class="num">5m Avg</th><th class="num">10m Avg</th><th class="num">30m Avg</th><th class="num">60m Avg</th><th class="num">Win</th><th>Latest Signal</th></tr></thead>
      <tbody>{domestic_rows}</tbody>
    </table>
  </section>
  <section>
    <h2>KR-US Relationship</h2>
    <div class="metrics">
      <div class="metric"><div class="label">Regime</div><div class="value">{relationship_regime}</div></div>
      <div class="metric"><div class="label">Paired Samples</div><div class="value">{relationship_samples}</div></div>
      <div class="metric"><div class="label">Resolution</div><div class="value">{relationship_resolution}</div></div>
      <div class="metric"><div class="label">Warning</div><div class="value">{relationship_warning}</div></div>
      <div class="metric"><div class="label">Proxy</div><div class="value">{relationship_proxy}</div></div>
    </div>
    <table>
      <thead><tr><th>KR</th><th>US</th><th>Lag</th><th>Res</th><th class="num">Samples</th><th class="num">Corr</th><th class="num">3M</th><th class="num">6M</th><th class="num">1Y</th><th class="num">3Y</th><th class="num">10Y</th><th class="num">Beta</th><th class="num">R2</th><th class="num">Hit Up</th><th class="num">Hit Down</th><th class="num">Lead</th><th>Stability</th><th>Regime</th></tr></thead>
      <tbody>{relationship_rows}</tbody>
    </table>
  </section>
  <section>
    <h2>Shared Context Hub</h2>
    <div class="metrics">
      <div class="metric"><div class="label">DB</div><div class="value">{shared_status}</div></div>
      <div class="metric"><div class="label">Kiwoom</div><div class="value">{shared_kiwoom_time}</div></div>
      <div class="metric"><div class="label">Toss</div><div class="value">{shared_toss_time}</div></div>
      <div class="metric"><div class="label">Relationship</div><div class="value">{shared_relationship_time}</div></div>
    </div>
    <p class="meta">Path: {shared_db_path} | stale: {shared_stale} | missing: {shared_missing}</p>
  </section>
  <section>
    <h2>Latest Context</h2>
    <table><tbody>
      <tr><td>Collected</td><td>{context_time}</td></tr>
      <tr><td>FX Rate</td><td>{context_fx}</td></tr>
      <tr><td>US Session</td><td>{context_us}</td></tr>
      <tr><td>KR Session</td><td>{context_kr}</td></tr>
    </tbody></table>
  </section>
  <section>
    <h2>Tables</h2>
    <table><tbody>{table_cells}</tbody></table>
  </section>
</main>
</body>
</html>""".format(
        generated_at=_e(snapshot.get("generated_at")),
        db_path=_e(snapshot.get("db_path")),
        status_class=status_class,
        health_status=_e(health.get("status") or "unknown"),
        latest_time=_e(latest.get("analyzed_at") or "none"),
        latest_tokens=_e(latest.get("total_tokens") or 0),
        warning_count=_e(len(warnings)),
        warning_text=_e(warning_text),
        symbol_rows=symbol_rows,
        tick_rows=tick_rows,
        event_rows=event_rows,
        feedback_rows=feedback_rows,
        domestic_rows=domestic_rows,
        relationship_regime=_e(relationship.get("relationship_regime") or "insufficient_evidence"),
        relationship_samples=_e(((relationship.get("data_quality") or {}).get("paired_observation_count")) or 0),
        relationship_resolution=_e(_relationship_resolution_label(relationship)),
        relationship_warning=_e(((relationship.get("data_quality") or {}).get("warning")) or ((relationship.get("data_quality") or {}).get("resolution_warning")) or "none"),
        relationship_proxy=_e("yes" if ((relationship.get("data_quality") or {}).get("uses_proxy_alignment")) else "no"),
        relationship_rows=relationship_rows,
        shared_status=_e(shared_context.get("status") or "missing"),
        shared_db_path=_e(shared_context.get("db_path") or ""),
        shared_kiwoom_time=_e(shared_context.get("latest_kiwoom_context_time") or "none"),
        shared_toss_time=_e(shared_context.get("latest_toss_context_time") or "none"),
        shared_relationship_time=_e(shared_context.get("latest_relationship_context_time") or "none"),
        shared_stale=_e(", ".join(shared_context.get("stale_sections") or []) or "none"),
        shared_missing=_e(", ".join(shared_context.get("missing_sections") or []) or "none"),
        context_time=_e(context.get("collected_at") or ""),
        context_fx=_fmt(context.get("fx_rate"), decimals=4),
        context_us=_e(context.get("us_session") or ""),
        context_kr=_e(context.get("kr_session") or ""),
        table_cells=table_cells,
    )


def _symbol_row(row):
    score_delta = row.get("score_delta")
    delta_class = "pos" if _to_float(score_delta) > 0 else "neg" if _to_float(score_delta) < 0 else "muted"
    paper = row.get("paper") or {}
    paper_text = "{} eval, win {}, avg {}%".format(
        paper.get("evaluated_count", 0),
        paper.get("win_rate", 0),
        paper.get("avg_return_pct", 0),
    )
    risk = str(row.get("risk_level") or "unknown").upper()
    return """<tr>
<td><strong>{symbol}</strong><div class="muted">{price_time}</div></td>
<td><span class="pill">{decision}</span></td>
<td class="num">{score}</td>
<td class="num {delta_class}">{delta}</td>
<td><span class="pill risk-{risk}">{risk}</span></td>
<td>{confidence}</td>
<td class="num">{price}</td>
<td class="num {m1_class}">{m1}</td>
<td class="num">{vol}</td>
<td class="num {d1_class}">{d1}</td>
<td>{paper}</td>
<td><div class="summary">{summary}</div></td>
</tr>""".format(
        symbol=_e(row.get("symbol")),
        price_time=_e(row.get("price_time") or ""),
        decision=_e(row.get("decision")),
        score=_fmt(row.get("interest_score"), decimals=0),
        delta_class=delta_class,
        delta=_fmt(score_delta, decimals=0, signed=True),
        risk=_e(risk),
        confidence=_e(row.get("confidence")),
        price=_fmt(row.get("price"), decimals=2),
        m1_class=_num_class(row.get("minute_change_pct")),
        m1=_fmt(row.get("minute_change_pct"), decimals=2, signed=True),
        vol=_fmt(row.get("minute_volume_ratio"), decimals=2),
        d1_class=_num_class(row.get("daily_change_pct")),
        d1=_fmt(row.get("daily_change_pct"), decimals=2, signed=True),
        paper=_e(paper_text),
        summary=_e(row.get("summary") or ""),
    )


def _event_row(row):
    return """<tr><td>{time}</td><td>{symbol}</td><td>{etype}</td><td>{severity}</td><td class="num">{value}</td><td>{message}</td></tr>""".format(
        time=_e(row.get("detected_at")),
        symbol=_e(row.get("symbol")),
        etype=_e(row.get("event_type")),
        severity=_e(row.get("severity")),
        value=_fmt(row.get("value"), decimals=2),
        message=_e(row.get("message")),
    )


def _tick_row(row):
    quality = _parse_json_payload(row.get("payload_json")).get("data_quality") if row.get("payload_json") else {}
    if not row:
        return """<tr><td colspan="11" class="muted">No tick flow analysis yet.</td></tr>"""
    return """<tr><td><strong>{symbol}</strong></td><td>{time}</td><td class="num">{count}</td><td class="num {chg_class}">{change}</td><td class="num">{volume}</td><td class="num">{bid}</td><td class="num">{ask}</td><td class="num">{spread}</td><td class="num {imb_class}">{imbalance}</td><td>{signal}</td><td>{quality}</td></tr>""".format(
        symbol=_e(row.get("symbol")),
        time=_e(row.get("analyzed_at") or ""),
        count=_e(row.get("trade_count") or 0),
        chg_class=_num_class(row.get("price_change_pct")),
        change=_fmt(row.get("price_change_pct"), decimals=4, signed=True),
        volume=_fmt(row.get("volume_sum"), decimals=2),
        bid=_fmt(row.get("best_bid"), decimals=2),
        ask=_fmt(row.get("best_ask"), decimals=2),
        spread=_fmt(row.get("spread_pct"), decimals=4),
        imb_class=_num_class(row.get("orderbook_imbalance")),
        imbalance=_fmt(row.get("orderbook_imbalance"), decimals=4, signed=True),
        signal=_e(row.get("signal") or "-"),
        quality=_e((quality or {}).get("status") or "-"),
    )


def _feedback_row(row):
    return """<tr><td>{symbol}</td><td class="num">{horizon}</td><td class="num">{count}</td><td class="num">{win}</td><td class="num {avg_class}">{avg}</td><td class="num {best_class}">{best}</td><td class="num {worst_class}">{worst}</td></tr>""".format(
        symbol=_e(row.get("symbol")),
        horizon=_e(row.get("horizon_min")),
        count=_e(row.get("count")),
        win=_fmt(row.get("win_rate"), decimals=4),
        avg_class=_num_class(row.get("avg_return_pct")),
        avg=_fmt(row.get("avg_return_pct"), decimals=4, signed=True),
        best_class=_num_class(row.get("best_return_pct")),
        best=_fmt(row.get("best_return_pct"), decimals=4, signed=True),
        worst_class=_num_class(row.get("worst_return_pct")),
        worst=_fmt(row.get("worst_return_pct"), decimals=4, signed=True),
    )


def _domestic_row(row):
    horizons = {int(item.get("horizon_min") or 0): item for item in row.get("feedback") or []}
    sample_count = sum(_to_int(item.get("sample_count")) or 0 for item in row.get("feedback") or [])
    win_values = [(_to_float(item.get("win_rate")), _to_int(item.get("sample_count")) or 0) for item in row.get("feedback") or []]
    total = sum(count for _value, count in win_values)
    win_rate = sum(value * count for value, count in win_values) / total if total else 0.0
    signal = row.get("signal") or {}
    signal_text = "{} {} {} at {}".format(
        signal.get("latest_action_hint") or "-",
        _fmt(signal.get("latest_confidence_score"), decimals=0),
        signal.get("latest_risk_level") or "",
        signal.get("latest_detected_at") or "-",
    )
    return """<tr><td><strong>{code}</strong></td><td>{name}</td><td>{source}</td><td class="num">{samples}</td><td class="num {c5}">{r5}</td><td class="num {c10}">{r10}</td><td class="num {c30}">{r30}</td><td class="num {c60}">{r60}</td><td class="num">{win}</td><td>{signal}</td></tr>""".format(
        code=_e(row.get("code")),
        name=_e(row.get("name")),
        source=_e(row.get("source")),
        samples=_e(sample_count),
        c5=_num_class((horizons.get(5) or {}).get("avg_return_pct")),
        r5=_fmt((horizons.get(5) or {}).get("avg_return_pct"), decimals=4, signed=True),
        c10=_num_class((horizons.get(10) or {}).get("avg_return_pct")),
        r10=_fmt((horizons.get(10) or {}).get("avg_return_pct"), decimals=4, signed=True),
        c30=_num_class((horizons.get(30) or {}).get("avg_return_pct")),
        r30=_fmt((horizons.get(30) or {}).get("avg_return_pct"), decimals=4, signed=True),
        c60=_num_class((horizons.get(60) or {}).get("avg_return_pct")),
        r60=_fmt((horizons.get(60) or {}).get("avg_return_pct"), decimals=4, signed=True),
        win=_fmt(win_rate, decimals=4),
        signal=_e(signal_text),
    )


def _relationship_row(row):
    regression = row.get("regression") or {}
    directional = row.get("directional_stats") or {}
    resolution = row.get("resolution") or {}
    rolling = row.get("rolling_correlation") or {}
    windows = rolling.get("windows") or {}
    hit_up = (directional.get("hit_ratio_up") or {}).get("hit_ratio")
    hit_down = (directional.get("hit_ratio_down") or {}).get("hit_ratio")
    return """<tr><td>{kr}</td><td>{us}</td><td>{lag}</td><td><span class="pill">{res}</span></td><td class="num">{samples}</td><td class="num">{corr}</td><td class="num">{corr_3m}</td><td class="num">{corr_6m}</td><td class="num">{corr_1y}</td><td class="num">{corr_3y}</td><td class="num">{corr_10y}</td><td class="num">{beta}</td><td class="num">{r2}</td><td class="num">{hit_up}</td><td class="num">{hit_down}</td><td class="num">{lead}</td><td>{stability}</td><td>{regime}</td></tr>""".format(
        kr=_e(row.get("source_symbol")),
        us=_e(row.get("target_symbol")),
        lag=_e(row.get("lag_label")),
        res=_e(resolution.get("timeframe") or "unknown"),
        samples=_e(row.get("paired_sample_count")),
        corr=_fmt(row.get("correlation"), decimals=4),
        corr_3m=_fmt((windows.get("3m") or {}).get("correlation"), decimals=4),
        corr_6m=_fmt((windows.get("6m") or {}).get("correlation"), decimals=4),
        corr_1y=_fmt((windows.get("1y") or {}).get("correlation"), decimals=4),
        corr_3y=_fmt((windows.get("3y") or {}).get("correlation"), decimals=4),
        corr_10y=_fmt((windows.get("10y") or {}).get("correlation"), decimals=4),
        beta=_fmt(regression.get("beta"), decimals=4, signed=True),
        r2=_fmt(regression.get("r_squared"), decimals=4),
        hit_up=_fmt(hit_up, decimals=4),
        hit_down=_fmt(hit_down, decimals=4),
        lead=_fmt(row.get("lead_score"), decimals=2),
        stability=_e(rolling.get("stability") or "-"),
        regime=_e(row.get("relationship_regime")),
    )


def _relationship_resolution_label(relationship):
    quality = relationship.get("data_quality") or {}
    daily = _to_int(quality.get("daily_historical_observation_count"))
    total = _to_int(quality.get("paired_observation_count"))
    if daily and daily == total:
        return "1d historical"
    if daily:
        return "mixed"
    return "live/intraday"


def _shared_context_status():
    db_path = getattr(config, "SHARED_CONTEXT_DB_PATH", "")
    status = {
        "db_path": db_path,
        "status": "missing",
        "latest_kiwoom_context_time": None,
        "latest_toss_context_time": None,
        "latest_relationship_context_time": None,
        "stale_sections": [],
        "missing_sections": [],
    }
    if not db_path or not os.path.exists(db_path):
        status["missing_sections"] = ["shared_context.db"]
        return status
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            quick = conn.execute("PRAGMA quick_check").fetchone()
            if not quick or quick[0] != "ok":
                status["status"] = "failed"
                return status
            if not _shared_has_table(conn, "shared_context_snapshots"):
                status["status"] = "missing_table"
                status["missing_sections"] = ["shared_context_snapshots"]
                return status
            status["latest_kiwoom_context_time"] = _shared_latest(conn, "source = 'kiwoom'")
            status["latest_toss_context_time"] = _shared_latest(conn, "source = 'toss'")
            status["latest_relationship_context_time"] = _shared_latest(conn, "section = 'relationship_metrics'")
            for name, value in [
                ("kiwoom", status["latest_kiwoom_context_time"]),
                ("toss", status["latest_toss_context_time"]),
                ("relationship", status["latest_relationship_context_time"]),
            ]:
                if not value:
                    status["missing_sections"].append(name)
            status["status"] = "ok" if not status["missing_sections"] else "partial"
            return status
        finally:
            conn.close()
    except Exception as exc:
        status["status"] = "failed"
        status["missing_sections"] = [str(exc)]
        return status


def _shared_has_table(conn, table):
    return conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _shared_latest(conn, where):
    row = conn.execute("SELECT MAX(collected_at) AS latest FROM shared_context_snapshots WHERE {}".format(where)).fetchone()
    return row["latest"] if row else None


def _symbols_from_db(store):
    rows = store.conn.execute("""
        SELECT symbol FROM structured_analysis
        UNION
        SELECT symbol FROM price_snapshots
        ORDER BY symbol
    """).fetchall()
    return [row["symbol"] for row in rows]


def _latest_price(store, symbol):
    return _row(store, """
        SELECT * FROM price_snapshots
        WHERE symbol = ?
        ORDER BY collected_at DESC, id DESC
        LIMIT 1
    """, (symbol,))


def _latest_candle(store, symbol, interval):
    return _row(store, """
        SELECT * FROM candle_snapshots
        WHERE symbol = ? AND interval = ?
        ORDER BY collected_at DESC, id DESC
        LIMIT 1
    """, (symbol, interval))


def _previous_structured(store, symbol, current_analysis_id):
    if not current_analysis_id:
        return {}
    return _row(store, """
        SELECT * FROM structured_analysis
        WHERE symbol = ? AND analysis_id < ?
        ORDER BY analysis_id DESC, id DESC
        LIMIT 1
    """, (symbol, current_analysis_id))


def _paper_summary_for_symbol(store, symbol):
    row = store.conn.execute("""
        SELECT
            COUNT(1) AS evaluated_count,
            SUM(CASE WHEN result_return_pct > 0 THEN 1 ELSE 0 END) AS wins,
            AVG(result_return_pct) AS avg_return_pct
        FROM paper_trade_candidates
        WHERE symbol = ? AND status = 'evaluated'
    """, (symbol,)).fetchone()
    count = int(row["evaluated_count"] or 0)
    wins = int(row["wins"] or 0)
    return {
        "evaluated_count": count,
        "win_rate": round(wins / count, 4) if count else 0.0,
        "avg_return_pct": round(_to_float(row["avg_return_pct"]), 4),
    }


def _recent_events(store, limit=24):
    rows = store.conn.execute("""
        SELECT detected_at, symbol, event_type, severity, message, value
        FROM event_logs
        ORDER BY detected_at DESC, id DESC
        LIMIT ?
    """, (int(limit),)).fetchall()
    return [dict(row) for row in rows]


def _recent_events_by_symbol(store, symbols=None, limit=12):
    result = {}
    for symbol in [str(item).upper() for item in symbols or []]:
        rows = store.conn.execute("""
            SELECT detected_at, symbol, event_type, severity, message, value
            FROM event_logs
            WHERE symbol = ?
            ORDER BY detected_at DESC, id DESC
            LIMIT ?
        """, (symbol, int(limit))).fetchall()
        result[symbol] = [dict(row) for row in rows]
    return result


def _recent_paper_candidates(store, symbols=None, limit=80):
    symbols = [str(item).upper() for item in symbols or []]
    params = []
    where = ""
    if symbols:
        where = "WHERE symbol IN ({})".format(",".join("?" for _ in symbols))
        params.extend(symbols)
    params.append(int(limit))
    rows = store.conn.execute("""
        SELECT created_at, symbol, horizon_min, anchor_price, status,
               result_return_pct, max_return_pct, min_return_pct, outcome, evaluated_at
        FROM paper_trade_candidates
        {where}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
    """.format(where=where), params).fetchall()
    return [dict(row) for row in rows]


def _score_history(store, symbols=None, limit=12):
    symbols = [str(item).upper() for item in symbols or []]
    result = {}
    for symbol in symbols:
        rows = store.conn.execute("""
            SELECT structured_analysis.analysis_id, analysis_results.analyzed_at,
                   structured_analysis.final_decision, structured_analysis.interest_score,
                   structured_analysis.risk_level, structured_analysis.confidence
            FROM structured_analysis
            LEFT JOIN analysis_results ON analysis_results.id = structured_analysis.analysis_id
            WHERE structured_analysis.symbol = ?
            ORDER BY structured_analysis.analysis_id DESC, structured_analysis.id DESC
            LIMIT ?
        """, (symbol, int(limit))).fetchall()
        result[symbol] = [dict(row) for row in reversed(rows)]
    return result


def _minute_close_series(store, symbol, limit=120):
    rows = store.conn.execute("""
        SELECT collected_at, latest_close, change_pct, volume_ratio
        FROM candle_snapshots
        WHERE symbol = ? AND interval = '1m' AND latest_close > 0
        ORDER BY collected_at DESC, id DESC
        LIMIT ?
    """, (str(symbol).upper(), int(limit))).fetchall()
    series = [dict(row) for row in reversed(rows)]
    return series


def _domestic_with_analysis(store, rows):
    codes = [row.get("code") for row in rows or []]
    structured = _latest_structured_by_mode(store, codes, mode="domestic_kr")
    result = []
    for row in rows or []:
        item = dict(row)
        item["analysis"] = structured.get(row.get("code")) or None
        result.append(item)
    return result


def _latest_structured_by_mode(store, symbols, mode):
    result = {}
    for symbol in symbols or []:
        row = store.conn.execute("""
            SELECT structured_analysis.*, analysis_results.analyzed_at
            FROM structured_analysis
            LEFT JOIN analysis_results ON analysis_results.id = structured_analysis.analysis_id
            WHERE structured_analysis.symbol = ? AND analysis_results.mode = ?
            ORDER BY structured_analysis.analysis_id DESC, structured_analysis.id DESC
            LIMIT 1
        """, (str(symbol), mode)).fetchone()
        result[str(symbol)] = dict(row) if row else None
    return result


def _latest_gpt_analysis(store, mode=None):
    where = ""
    params = []
    if mode:
        where = "WHERE mode = ?"
        params.append(mode)
    row = store.conn.execute("""
        SELECT id, analyzed_at, symbols, model, total_tokens, gpt_analysis
        FROM analysis_results
        {where}
        ORDER BY id DESC
        LIMIT 1
    """.format(where=where), params).fetchone()
    return dict(row) if row else None


def _latest_context(store):
    row = store.conn.execute("""
        SELECT collected_at, fx_rate, us_session, kr_session, payload_json
        FROM market_context_snapshots
        ORDER BY collected_at DESC, id DESC
        LIMIT 1
    """).fetchone()
    return dict(row) if row else {}


def _gpt_sections_by_symbol(text, symbols):
    text = text or ""
    sections = {}
    markers = []
    boundaries = []
    symbol_set = set(str(symbol).upper() for symbol in symbols or [])
    for match in re.finditer(r"(?:^|\n)\s*#{1,6}\s*(\S.*?)\s*(?:\n|$)", text, flags=re.IGNORECASE):
        boundaries.append(match.start())
        symbol = match.group(1).upper()
        if symbol in symbol_set:
            markers.append((match.start(), symbol))
    for match in re.finditer(r"(?:^|\n)\s*[-#*\s_]*(?:\*{0,2}\s*SYMBOL\s*\*{0,2}\s*:\s*\*{0,2}\s*)([A-Z0-9.\-]{1,10})\b", text, flags=re.IGNORECASE):
        boundaries.append(match.start())
        symbol = match.group(1).upper()
        if symbol in symbol_set:
            markers.append((match.start(), symbol))
    deduped = {}
    for pos, symbol in markers:
        deduped[symbol] = min(pos, deduped.get(symbol, pos))
    markers = sorted((pos, symbol) for symbol, pos in deduped.items())
    if not markers:
        upper = text.upper()
        for symbol in symbols or []:
            symbol = str(symbol).upper()
            idx = upper.find(symbol)
            if idx >= 0:
                markers.append((idx, symbol))
        markers.sort()
    for index, (start, symbol) in enumerate(markers):
        later_boundaries = [pos for pos in boundaries if pos > start + 20]
        end = min(later_boundaries) if later_boundaries else len(text)
        section = text[start:end].strip()
        sections[symbol] = section
    for symbol in symbols or []:
        sections.setdefault(str(symbol).upper(), "")
    return sections


def _parse_json_payload(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def _display_summary(text, symbol):
    text = " ".join(str(text or "").split())
    current = str(symbol or "").upper()
    pattern = r"(?:\s---\s+#{1,6}\s*([A-Z0-9.\-]{1,10})\b|\s+\*{0,2}\s*SYMBOL\s*\*{0,2}\s*:\s*\*{0,2}\s*([A-Z0-9.\-]{1,10})\b)"
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        marker = (match.group(1) or match.group(2) or "").upper()
        if marker == current:
            continue
        before = text[:match.start()].strip()
        if before:
            return before
    return text


def format_summary_text(text):
    text = " ".join(str(text or "").split())
    replacements = [
        (" **DECISION", "\nDECISION"),
        (" **INTEREST_SCORE", "\nINTEREST_SCORE"),
        (" **RISK_LEVEL", "\nRISK_LEVEL"),
        (" **CONFIDENCE", "\nCONFIDENCE"),
        (" **EVIDENCE:**", "\n\nEVIDENCE:\n"),
        (" **WEIGHTING:**", "\n\nWEIGHTING:\n"),
        (" **DATA GAPS:**", "\n\nDATA GAPS:\n"),
        (" **CHANGE VS PREVIOUS ANALYSIS:**", "\n\nCHANGE VS PREVIOUS ANALYSIS:\n"),
        (" **NEXT CHECKS:**", "\n\nNEXT CHECKS:\n"),
        (" - ", "\n- "),
        (" --- ", "\n\n---\n"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    text = text.replace("**", "")
    lines = [line.strip() for line in text.splitlines()]
    compact = []
    previous_blank = False
    for line in lines:
        if not line:
            if not previous_blank:
                compact.append("")
            previous_blank = True
            continue
        compact.append(line)
        previous_blank = False
    return "\n".join(compact).strip()


def _row(store, query, params):
    row = store.conn.execute(query, params).fetchone()
    return dict(row) if row else {}


def _delta(current, previous):
    if current is None or previous is None:
        return None
    return current - previous


def _num_class(value):
    value = _to_float(value)
    if value > 0:
        return "pos"
    if value < 0:
        return "neg"
    return "muted"


def _fmt(value, decimals=2, signed=False):
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _e(value)
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
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _e(value):
    return html.escape(str(value if value is not None else ""))


if __name__ == "__main__":
    sys.exit(main())
