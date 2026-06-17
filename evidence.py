"""Read-only Toss evidence collection for GPT analysis and safety gates."""

from datetime import datetime

try:
    from .security import sanitize_payload
except ImportError:  # pragma: no cover
    from security import sanitize_payload


def collect_read_only_evidence(client, symbols, account_seq=None):
    symbols = [str(item).upper() for item in symbols if str(item).strip()]
    evidence = {
        "broker": "tossinvest",
        "collected_at": _now(),
        "symbols": symbols,
        "errors": [],
        "accounts": None,
        "prices": None,
        "us_market_calendar": None,
        "exchange_rate": None,
        "holdings": None,
        "buying_power_usd": None,
    }

    for key, loader in [
        ("accounts", client.get_accounts),
        ("prices", lambda: client.get_prices(symbols)),
        ("us_market_calendar", client.get_us_market_calendar),
        ("exchange_rate", lambda: client.get_exchange_rate(base_currency="USD", quote_currency="KRW")),
    ]:
        _capture(evidence, key, loader)

    account_seq = account_seq or _first_account_seq(evidence.get("accounts"))
    if account_seq:
        _capture(evidence, "holdings", lambda: client.get_holdings(account_seq=account_seq))
        _capture(evidence, "buying_power_usd", lambda: client.get_buying_power(currency="USD", account_seq=account_seq))

    evidence["safe_for_analysis"] = len(evidence["errors"]) == 0
    return sanitize_payload(evidence)


def _capture(evidence, key, loader):
    try:
        evidence[key] = loader()
    except Exception as exc:
        evidence["errors"].append("{} failed: {}".format(key, exc))


def _first_account_seq(accounts_response):
    result = (accounts_response or {}).get("result") or []
    if not result:
        return None
    account_seq = result[0].get("accountSeq")
    if account_seq in (None, ""):
        return None
    return str(account_seq)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
