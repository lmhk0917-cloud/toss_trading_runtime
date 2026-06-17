"""Toss Invest US-stock order safety gate.

This gate is intentionally independent from the Kiwoom safety gate because
US stocks use decimal prices, USD buying power, and REST account evidence.
"""

from datetime import datetime

try:
    from . import config
except ImportError:  # pragma: no cover
    import config


class TossOrderSafetyGate(object):
    def __init__(self, settings=None):
        self.settings = settings or config

    def validate(self, order, runtime_evidence=None, gpt_review=None, deterministic_result=None):
        order = order or {}
        runtime_evidence = runtime_evidence or {}
        gpt_review = gpt_review or {}
        messages = []
        warnings = []

        order_mode = str(order.get("order_mode") or self.settings.ORDER_MODE).strip().lower()
        symbol = str(order.get("symbol") or order.get("code") or "").strip().upper()
        side = str(order.get("side") or "").strip().upper()
        qty = _to_float(order.get("quantity") or order.get("qty"))
        order_amount = _to_float(order.get("orderAmount") or order.get("order_amount"))
        price = _to_float(order.get("price"))
        estimated_amount = order_amount if order_amount > 0 else qty * price

        self._validate_shape(order_mode, symbol, side, qty, order_amount, price, estimated_amount, messages)
        self._validate_runtime(runtime_evidence, messages, warnings)
        self._validate_deterministic(deterministic_result, messages, warnings)
        self._validate_gpt(gpt_review, messages, warnings)
        self._validate_real_mode(order_mode, messages)

        return {
            "passed": len(messages) == 0,
            "order_mode": order_mode,
            "symbol": symbol,
            "estimated_amount_usd": round(estimated_amount, 4),
            "messages": messages,
            "warnings": warnings,
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
        }

    def _validate_shape(self, order_mode, symbol, side, qty, order_amount, price, estimated_amount, messages):
        if order_mode not in ("disabled", "dry_run", "mock_real", "real"):
            messages.append("unsupported order_mode: {}".format(order_mode))
        if not symbol:
            messages.append("symbol is missing")
        if side not in ("BUY", "SELL"):
            messages.append("side must be BUY or SELL")
        if qty <= 0 and order_amount <= 0:
            messages.append("quantity or orderAmount must be positive")
        if qty > self.settings.MAX_ORDER_QTY:
            messages.append("quantity exceeds MAX_ORDER_QTY")
        if estimated_amount > self.settings.MAX_ORDER_AMOUNT_USD:
            messages.append("estimated amount exceeds MAX_ORDER_AMOUNT_USD")
        allowed = list(getattr(self.settings, "ALLOWED_ORDER_SYMBOLS", []))
        if allowed and symbol not in allowed:
            messages.append("symbol is not in ALLOWED_ORDER_SYMBOLS")
        if qty > 0 and price <= 0 and order_amount <= 0:
            messages.append("price is required for quantity-based local risk estimate")

    def _validate_runtime(self, evidence, messages, warnings):
        if self.settings.REQUIRE_ACCOUNT_HEADER and not evidence.get("account_seq_present"):
            messages.append("Toss account sequence is missing")
        if self.settings.REQUIRE_US_MARKET_CALENDAR and not evidence.get("us_market_calendar_ok"):
            messages.append("US market calendar evidence is missing")
        if self.settings.REQUIRE_RECENT_MARKET_DATA and not evidence.get("recent_market_data_ok"):
            messages.append("recent Toss market data evidence is missing")
        if self.settings.REQUIRE_BUYING_POWER:
            buying_power = _to_float(evidence.get("buying_power_usd"))
            estimated = _to_float(evidence.get("estimated_amount_usd"))
            if buying_power <= 0:
                messages.append("USD buying power evidence is missing")
            if estimated > 0 and buying_power > 0 and estimated > buying_power:
                messages.append("estimated amount exceeds USD buying power")
        open_positions = int(_to_float(evidence.get("open_positions")))
        if open_positions >= self.settings.MAX_OPEN_POSITIONS:
            messages.append("open position limit reached")
        daily_pnl = _to_float(evidence.get("daily_pnl_usd"))
        if self.settings.MAX_DAILY_LOSS_USD > 0 and daily_pnl < -abs(self.settings.MAX_DAILY_LOSS_USD):
            messages.append("daily USD loss limit reached")
        if evidence.get("kiwoom_runtime_active"):
            warnings.append("Kiwoom runtime is active; keep Toss and Kiwoom DB/log/process boundaries separated")

    def _validate_deterministic(self, deterministic_result, messages, warnings):
        if not self.settings.REQUIRE_DETERMINISTIC_ENTRY:
            return
        deterministic_result = deterministic_result or {}
        if not deterministic_result.get("passed"):
            messages.append("deterministic entry rejected")
        score = int(_to_float(deterministic_result.get("confidence_score")))
        if score < self.settings.MIN_CONFIDENCE_SCORE_FOR_REAL_ORDER:
            messages.append("confidence_score below threshold")
        warnings.extend(deterministic_result.get("warnings", []))

    def _validate_gpt(self, gpt_review, messages, warnings):
        if not self.settings.REQUIRE_GPT_JSON_REVIEW:
            return
        if str(gpt_review.get("decision") or "").strip().lower() != "approve":
            messages.append("GPT review decision is not approving order")
        confidence = int(_to_float(gpt_review.get("confidence")))
        if confidence < self.settings.MIN_GPT_CONFIDENCE_FOR_REAL_ORDER:
            messages.append("GPT confidence below threshold")
        if gpt_review.get("risk_flags"):
            warnings.append("GPT review has risk_flags")

    def _validate_real_mode(self, order_mode, messages):
        if order_mode == "disabled":
            messages.append("ORDER_MODE is disabled")
        if order_mode == "real" and not self.settings.ALLOW_REAL_ORDER:
            messages.append("ALLOW_REAL_ORDER is false")
        if order_mode == "real" and not list(getattr(self.settings, "ALLOWED_ORDER_SYMBOLS", [])):
            messages.append("ALLOWED_ORDER_SYMBOLS must be configured for real mode")
        if order_mode == "real" and self.settings.MAX_DAILY_LOSS_USD <= 0:
            messages.append("MAX_DAILY_LOSS_USD must be positive for real mode")


def _to_float(value):
    try:
        if value in (None, ""):
            return 0.0
        return abs(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0.0

