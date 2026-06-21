# Historical Market Data Integration

Generated and applied on 2026-06-21.

## Source Files

Collected market data lives outside the package under:

- `C:\Users\lmhk2\Documents\New project\market_data_exports\yahoo_history_10y_20260621_161508.xlsx`
- `C:\Users\lmhk2\Documents\New project\market_data_exports\csv`
- `C:\Users\lmhk2\Documents\New project\market_data_exports\naver_kr\naver_kr_history_10y_20260621_161812.xlsx`
- `C:\Users\lmhk2\Documents\New project\market_data_exports\naver_kr\csv`

Yahoo Finance adjusted close returns are the primary source for relationship rows.
Naver Finance KR data is retained as a secondary Korean-market reference.

## Applied Runtime Data

The Yahoo CSV files were imported into `market_relationship_observations` with:

```powershell
C:\Users\lmhk2\anaconda3\python.exe -m toss_trading_runtime.import_historical_relationships --csv-dir "C:\Users\lmhk2\Documents\New project\market_data_exports\csv"
```

Result:

- Inserted relationship rows: `86616`
- Domestic symbols: `005930`, `000660`
- US symbols: `NVDA`, `MU`, `QQQ`, `SOXX`, `AMD`, `AVGO`, `TSM`, `SMH`, `SPY`
- Lag labels: `same_date_us_kr`, `us_t_minus_1_to_kr_t`

## Direction Rule

Rows keep the UI pair shape as `KR symbol -> US symbol`, but historical analysis
uses payload fields:

- `driver_symbol`: US symbol
- `driver_return_pct`: US return
- `response_symbol`: KR symbol
- `response_return_pct`: KR return

Therefore regression beta means:

```text
KR response return = alpha + beta * US driver return
```

This avoids the old ambiguity where `source_symbol` was KR for display but the
actual lead-lag question was US-to-KR.

## Current Example

After import, the relationship analysis uses full historical samples instead of
only recent live observations. Example selected outputs:

- `000660` vs `MU`, `us_t_minus_1_to_kr_t`: sample about `2445`, correlation about `0.4054`, beta about `0.3488`
- `005930` vs `MU`, `us_t_minus_1_to_kr_t`: sample about `2445`, correlation about `0.3470`, beta about `0.2157`

Use the dashboard `KR-US Relationship` section for the current computed values.

## Shared Daily Package

The collected data can be exported for the other projects with:

```powershell
C:\Users\lmhk2\anaconda3\python.exe -m toss_trading_runtime.export_historical_market_data
```

Output:

- `C:\Users\lmhk2\Documents\New project\market_data_exports\shared\historical_market_data_v1.json`

Important resolution rule:

- The shared package is `timeframe=1d`.
- It sets `intraday_source=false` and `tick_source=false`.
- Importers must not create synthetic ticks or synthetic minute candles from it.
- Use it for long-horizon context, daily correlation, and daily lead-lag checks only.
