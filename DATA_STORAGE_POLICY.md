# Toss Runtime Data Storage Policy

- Canonical source is the local C: project folder.
- P51/D: is backup only.
- Runtime SQLite files and generated reports should not be committed.
- Secrets must stay in environment variables or local `.env` files excluded by
  `.gitignore`.
- Cross-project context should be exported to or read from
  `shared_market_context`; direct reads from Kiwoom runtime DBs are fallback-only.
- Use post-close export and backup scripts rather than copying locked live DBs
  during an active session.
