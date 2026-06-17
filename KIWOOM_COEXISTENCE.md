# Toss/Kiwoom Coexistence Guardrails

Toss Invest Open API is HTTPS/REST based. It should not directly interfere with
Kiwoom OpenAPI COM/QAxWidget login sessions.

Keep these boundaries:

- Do not run Toss probes through the Kiwoom `py37_32` COM runtime.
- Do not start Toss tests from inside a Kiwoom supervisor launch.
- Keep Toss DB/log files outside `KiwoomAPI_GPT_personal_ver1`.
- Keep Kiwoom preflight strict; if Kiwoom is scheduled, do not create extra
  project Python processes before the supervisor.
- Run Conda-based checks sequentially on this machine.
- Use one top-level risk ledger before any future real order mode spans both
  Korean and US brokers.

Default Toss build stage:

1. OAuth token issue.
2. Read-only accounts/prices/calendar/exchange-rate/holdings/buying-power.
3. Evidence pack generation.
4. Dry-run order safety validation.
5. Mock-real rehearsal.
6. Real mode only after explicit unlock, allowlist, daily loss limit, and
   broker-side read-only evidence all pass.

