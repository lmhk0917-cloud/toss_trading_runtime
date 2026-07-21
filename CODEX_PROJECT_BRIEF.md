# Toss Trading Runtime Brief

Role: US-market read-only data collection and analysis runtime.

Canonical root: `C:\Users\lmhk2\Documents\New project\toss_trading_runtime`

Boundary:

- Keep Toss HTTPS/Open API code separate from Kiwoom OpenAPI+ COM code.
- Keep order APIs disabled unless the user explicitly changes the order mode.
- Read Korean-market context through `shared_market_context` first.
- Do not commit real Toss credentials, account identifiers, OpenAI keys, tokens,
  runtime DBs, or generated reports containing unsanitized account details.

Current maturity target:

- Match Kiwoom Core's data/replay/feedback discipline.
- Match `real_trading_runtime` safety defaults and order-boundary tests.
- Add sector-profile context only through shared/exported data contracts.
