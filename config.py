"""Conservative Toss Invest runtime defaults.

Secrets are read from environment variables only. Do not commit API keys,
secret keys, access tokens, or account identifiers.
"""

import json
import os


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_ROOT = os.path.join(PROJECT_ROOT, "watchlists")


def _load_watchlist(env_name, filename, fallback):
    configured = os.environ.get(env_name)
    if configured is not None:
        return [item.strip().upper() for item in configured.split(",") if item.strip()]
    path = os.path.join(WATCHLIST_ROOT, filename)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        values = payload.get("symbols") if isinstance(payload, dict) else payload
        symbols = [str(item).strip().upper() for item in values or [] if str(item).strip()]
        if symbols:
            return list(dict.fromkeys(symbols))
    except (IOError, OSError, TypeError, ValueError):
        pass
    return list(fallback)


def merge_watchlists(*groups):
    merged = []
    for group in groups:
        for item in group or []:
            symbol = str(item).strip().upper()
            if symbol and symbol not in merged:
                merged.append(symbol)
    return merged


BASE_URL = os.environ.get("TOSSINVEST_BASE_URL", "https://openapi.tossinvest.com").rstrip("/")

CLIENT_ID_ENV = "TOSSINVEST_CLIENT_ID"
CLIENT_SECRET_ENV = "TOSSINVEST_CLIENT_SECRET"
ACCOUNT_SEQ_ENV = "TOSSINVEST_ACCOUNT_SEQ"

ORDER_MODE = os.environ.get("TOSSINVEST_ORDER_MODE", "disabled").strip().lower()
ALLOW_REAL_ORDER = os.environ.get("TOSSINVEST_ALLOW_REAL_ORDER", "0").strip() in ("1", "true", "yes")

MAX_ORDER_AMOUNT_USD = float(os.environ.get("TOSSINVEST_MAX_ORDER_AMOUNT_USD", "100"))
MAX_ORDER_QTY = float(os.environ.get("TOSSINVEST_MAX_ORDER_QTY", "1"))
MAX_OPEN_POSITIONS = int(os.environ.get("TOSSINVEST_MAX_OPEN_POSITIONS", "3"))
MAX_DAILY_LOSS_USD = float(os.environ.get("TOSSINVEST_MAX_DAILY_LOSS_USD", "0"))

REQUIRE_ACCOUNT_HEADER = True
REQUIRE_RECENT_MARKET_DATA = True
REQUIRE_US_MARKET_CALENDAR = True
REQUIRE_BUYING_POWER = True
REQUIRE_GPT_JSON_REVIEW = True
REQUIRE_DETERMINISTIC_ENTRY = True

RECENT_PRICE_MAX_AGE_SEC = int(os.environ.get("TOSSINVEST_RECENT_PRICE_MAX_AGE_SEC", "90"))
MIN_CONFIDENCE_SCORE_FOR_REAL_ORDER = int(os.environ.get("TOSSINVEST_MIN_CONFIDENCE_SCORE", "80"))
MIN_GPT_CONFIDENCE_FOR_REAL_ORDER = int(os.environ.get("TOSSINVEST_MIN_GPT_CONFIDENCE", "75"))

ALLOWED_ORDER_SYMBOLS = [
    item.strip().upper()
    for item in os.environ.get("TOSSINVEST_ALLOWED_ORDER_SYMBOLS", "").split(",")
    if item.strip()
]

DEFAULT_WATCHLIST = [
    item.strip().upper()
    for item in os.environ.get("TOSSINVEST_DEFAULT_WATCHLIST", "AAPL,MSFT,NVDA,QQQ,SPY").split(",")
    if item.strip()
]

FOCUSED_NASDAQ_WATCHLIST = _load_watchlist(
    "TOSSINVEST_FOCUSED_NASDAQ_WATCHLIST",
    "us_focus.json",
    ["MU", "NVDA", "AMD", "AVGO", "MRVL", "ARM", "TSM", "ORCL", "CRWV", "PLTR", "QQQ", "SMH"],
)

US_MARKET_CONTEXT_WATCHLIST = _load_watchlist(
    "TOSSINVEST_US_MARKET_CONTEXT_WATCHLIST",
    "us_context.json",
    ["SPY"],
)

US_COLLECTION_WATCHLIST = merge_watchlists(
    FOCUSED_NASDAQ_WATCHLIST,
    US_MARKET_CONTEXT_WATCHLIST,
)

ENABLE_TEMP_SCREENING = os.environ.get("TOSSINVEST_ENABLE_TEMP_SCREENING", "0").strip() in ("1", "true", "yes")

# US stock paper-cost assumptions. Toss currently publishes a 0.1% US stock
# commission per side. Slippage and regulatory values are conservative research
# buffers, not broker quotes. FX is excluded by default because returns are
# measured in USD; set a buffer when a KRW conversion is part of the strategy.
US_STOCK_COMMISSION_PCT = float(os.environ.get("TOSSINVEST_US_STOCK_COMMISSION_PCT", "0.10"))
US_STOCK_SLIPPAGE_PCT = float(os.environ.get("TOSSINVEST_US_STOCK_SLIPPAGE_PCT", "0.05"))
US_STOCK_REGULATORY_BUFFER_PCT = float(os.environ.get("TOSSINVEST_US_STOCK_REGULATORY_BUFFER_PCT", "0.01"))
US_STOCK_FX_ROUND_TRIP_BUFFER_PCT = float(os.environ.get("TOSSINVEST_US_STOCK_FX_ROUND_TRIP_BUFFER_PCT", "0.0"))
_PAPER_ROUND_TRIP_COST_OVERRIDE = os.environ.get("TOSSINVEST_PAPER_ROUND_TRIP_COST_PCT")
PAPER_ROUND_TRIP_COST_PCT = (
    float(_PAPER_ROUND_TRIP_COST_OVERRIDE)
    if _PAPER_ROUND_TRIP_COST_OVERRIDE not in (None, "")
    else (
        US_STOCK_COMMISSION_PCT * 2
        + US_STOCK_SLIPPAGE_PCT * 2
        + US_STOCK_REGULATORY_BUFFER_PCT
        + US_STOCK_FX_ROUND_TRIP_BUFFER_PCT
    )
)
PAPER_COST_SOURCE = (
    "TOSSINVEST_PAPER_ROUND_TRIP_COST_PCT override"
    if _PAPER_ROUND_TRIP_COST_OVERRIDE not in (None, "")
    else "Toss official 0.1% commission per side plus configurable simulation buffers"
)

# Trade-plan gates prepare paper/shadow validation only. They cannot enable
# orders and deliberately stop short of producing a live-trading candidate.
TRADE_PLAN_MIN_ENTRY_SCORE = int(os.environ.get("TOSSINVEST_TRADE_PLAN_MIN_ENTRY_SCORE", "75"))
TRADE_PLAN_MIN_FEEDBACK_SAMPLES = int(os.environ.get("TOSSINVEST_TRADE_PLAN_MIN_FEEDBACK_SAMPLES", "20"))
TRADE_PLAN_MIN_NET_RR = float(os.environ.get("TOSSINVEST_TRADE_PLAN_MIN_NET_RR", "1.20"))
TRADE_PLAN_MAX_HOLD_MINUTES = int(os.environ.get("TOSSINVEST_TRADE_PLAN_MAX_HOLD_MINUTES", "60"))

KIWOOM_DB_CANDIDATES = [
    r"C:\Users\lmhk2\PycharmProjects\Kiwoom_Core_Quant_Lab\data\ticks.db",
    r"C:\Users\lmhk2\PycharmProjects\KiwoomAPI_GPT_personal_ver1\data\ticks.db",
    r"C:\Users\lmhk2\PycharmProjects\Kiwoom_Screening_Assistant\data\ticks.db",
]


def resolve_kiwoom_personal_db_path():
    configured = os.environ.get("KIWOOM_PERSONAL_DB_PATH")
    if configured:
        return configured
    existing = [path for path in KIWOOM_DB_CANDIDATES if os.path.exists(path)]
    if not existing:
        return KIWOOM_DB_CANDIDATES[0]
    existing.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    return existing[0]


KIWOOM_PERSONAL_DB_PATH = resolve_kiwoom_personal_db_path()

SHARED_CONTEXT_DB_PATH = os.environ.get(
    "SHARED_CONTEXT_DB_PATH",
    r"C:\Users\lmhk2\Documents\New project\shared_market_context\shared_context.db",
)

READ_ONLY_ENDPOINTS = set([
    "/api/v1/accounts",
    "/api/v1/prices",
    "/api/v1/candles",
    "/api/v1/exchange-rate",
    "/api/v1/holdings",
    "/api/v1/market-calendar/US",
    "/api/v1/buying-power",
])

MUTATING_ORDER_ENDPOINTS = set([
    "/api/v1/orders",
    "/api/v1/orders/{orderId}/cancel",
    "/api/v1/orders/{orderId}/modify",
])


def missing_secret_names():
    missing = []
    for name in (CLIENT_ID_ENV, CLIENT_SECRET_ENV):
        if not os.environ.get(name):
            missing.append(name)
    return missing

