# Toss Invest Runtime

This is a safety-first Toss Invest Open API scaffold for US-stock analysis.
It is separate from the Kiwoom runtime.

## Secrets

Set these outside the repository:

```powershell
$env:TOSSINVEST_CLIENT_ID="..."
$env:TOSSINVEST_CLIENT_SECRET="..."
$env:TOSSINVEST_ACCOUNT_SEQ="..."
```

Do not commit real keys, secrets, tokens, or account identifiers.

## Read-Only Smoke

```powershell
python -m toss_trading_runtime.read_only_smoke --symbols AAPL,MSFT,NVDA,QQQ,SPY
```

This only calls OAuth, account, market data, market calendar, FX, holdings, and
buying-power APIs. It never creates, modifies, or cancels orders.

## Read-Only GPT Test

After you add secrets to `.env.local` or `.env`, run:

```powershell
C:\Users\lmhk2\anaconda3\python.exe -m toss_trading_runtime.nightly_gpt_read_only_test --symbols AAPL,MSFT,NVDA,QQQ,SPY
```

Required env vars:

```powershell
TOSSINVEST_CLIENT_ID=...
TOSSINVEST_CLIENT_SECRET=...
TOSSINVEST_ACCOUNT_SEQ=...
OPENAI_API_KEY=...
```

The generated report is written under `toss_trading_runtime/reports/` with
secrets and account identifiers sanitized.

## Read-Only Screening

```powershell
C:\Users\lmhk2\anaconda3\python.exe -m toss_trading_runtime.run_screener --symbols AAPL,MSFT,NVDA,QQQ,SPY --candle-count 60
```

This path is temporarily disabled by default while the runtime is focused on a
small fixed watchlist. Set `TOSSINVEST_ENABLE_TEMP_SCREENING=1` only when broad
candidate ranking is explicitly being tested.

## Focused Watchlist Analysis

```powershell
C:\Users\lmhk2\anaconda3\python.exe -m toss_trading_runtime.run_focused_analysis --symbols MU,NVDA,AMD,AVGO,TSM,QQQ,SMH,SPY
```

This is the preferred route. It collects price, stock metadata, US/KR session
calendar, FX, account evidence, 1m candles, and daily candles for a fixed symbol
set, persists the evidence/events/GPT result to SQLite, creates paper-trade
follow-up candidates, then asks GPT for a personal-style focused analysis report.

## Focused Collection Loop

```powershell
C:\Users\lmhk2\anaconda3\python.exe -m toss_trading_runtime.collect_loop --symbols MU,NVDA,QQQ --iterations 10 --interval-sec 60 --minute-count 40 --daily-count 20
```

This path does not call GPT. It repeatedly collects read-only evidence, writes
SQLite rows, detects deterministic events, and evaluates any due paper-trade
candidates when later prices exist.

For a low-rate-limit rehearsal before the US regular open:

```powershell
C:\Users\lmhk2\anaconda3\python.exe -m toss_trading_runtime.collect_loop --symbols MU,NVDA,QQQ --iterations 5 --interval-sec 60 --minute-count 20 --daily-count 5
```

SQLite database:

```text
toss_trading_runtime/toss_runtime.db
```

Current persisted tables:

- `price_snapshots`
- `candle_snapshots`
- `market_context_snapshots`
- `event_logs`
- `analysis_results`
- `paper_trade_candidates`

## Supervisor

```powershell
C:\Users\lmhk2\anaconda3\python.exe -m toss_trading_runtime.supervisor --symbols MU,NVDA,QQQ --until-kst 22:30 --interval-sec 60 --minute-count 20 --daily-count 5
```

The supervisor prints row deltas at shutdown and keeps order APIs disabled.
It also writes a rolling summary JSON so a stopped run can be audited:

```text
toss_trading_runtime/reports/supervisor_latest.json
```

## One-Command Market Session Run

```powershell
C:\Users\lmhk2\anaconda3\python.exe -m toss_trading_runtime.market_session_run --symbols MU,NVDA,QQQ --collect-minutes 3 --interval-sec 30 --minute-count 60 --daily-count 20 --max-tokens 1800
```

This runs focused collection first, then one GPT focused analysis, then refreshes
the ops HTML report. Each stage is recorded in:

```text
toss_trading_runtime/reports/market_session_latest.json
```

## Ops Report

```powershell
C:\Users\lmhk2\anaconda3\python.exe -m toss_trading_runtime.ops_report --html toss_trading_runtime\reports\ops_latest.html
```

The report includes runtime health: latest price/candle/context age, pending
paper candidates, due paper candidates, and top deterministic events.

## Dashboard

```powershell
C:\Users\lmhk2\anaconda3\python.exe -m toss_trading_runtime.dashboard --symbols MU,NVDA,QQQ --html toss_trading_runtime\reports\dashboard_latest.html
```

The dashboard is a static HTML file with runtime health, latest structured
judgment, score delta versus the previous analysis, latest prices, candle
metrics, recent events, and paper feedback.

## Desktop Dashboard Window

```powershell
C:\Users\lmhk2\anaconda3\python.exe -m toss_trading_runtime.dashboard_window --symbols MU,NVDA,QQQ --refresh-sec 30
```

This opens a local Windows UI using Tkinter. It does not start a web server and
does not call order APIs.

Shortcut from the runtime folder:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\launch_dashboard.ps1 -Symbols MU,NVDA,QQQ -RefreshSec 30
```

The desktop UI includes runtime health, symbol score/decision rows, per-symbol
details, score trend, paper feedback, GPT sections, recent events, latest
market context, and an HTML export/open action.

## Order Safety Defaults

- `TOSSINVEST_ORDER_MODE=disabled`
- `TOSSINVEST_ALLOW_REAL_ORDER=0`
- Real order submission is not implemented in the client scaffold.
- `TossOrderSafetyGate` must pass before any later executable broker adapter is
  added.
