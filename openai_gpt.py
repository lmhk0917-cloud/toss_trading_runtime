"""OpenAI GPT analysis wrapper for Toss read-only evidence.

Uses the Chat Completions HTTP endpoint via the standard library so this
runtime does not depend on the OpenAI SDK version installed for Kiwoom.
"""

import json
import os
import time
import urllib.request

try:
    from .security import sanitize_payload
except ImportError:  # pragma: no cover
    from security import sanitize_payload


OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_CHAT_COMPLETIONS_URL = os.environ.get(
    "OPENAI_CHAT_COMPLETIONS_URL",
    "https://api.openai.com/v1/chat/completions",
)


class TossGptError(Exception):
    pass


class TossGptAnalyzer(object):
    def __init__(self, api_key=None, model=None, opener=None, max_tokens=900):
        self.api_key = api_key or os.environ.get(OPENAI_API_KEY_ENV)
        self.model = model or DEFAULT_MODEL
        self.opener = opener or urllib.request.urlopen
        self.max_tokens = int(max_tokens)
        self.last_usage = {}
        self.last_model = self.model

    def validate_config(self):
        if not self.api_key:
            return [OPENAI_API_KEY_ENV]
        return []

    def analyze_evidence(self, evidence, symbols=None):
        missing = self.validate_config()
        if missing:
            raise TossGptError("missing environment variables: {}".format(",".join(missing)))
        payload = self._build_payload(evidence, symbols=symbols)
        return self._analyze_payload(payload)

    def analyze_focused_evidence(self, evidence, symbols=None):
        missing = self.validate_config()
        if missing:
            raise TossGptError("missing environment variables: {}".format(",".join(missing)))
        payload = self._build_focused_payload(evidence, symbols=symbols)
        return self._analyze_payload(payload)

    def analyze_domestic_evidence(self, evidence, symbols=None):
        missing = self.validate_config()
        if missing:
            raise TossGptError("missing environment variables: {}".format(",".join(missing)))
        payload = self._build_domestic_payload(evidence, symbols=symbols)
        return self._analyze_payload(payload)

    def _analyze_payload(self, payload):
        response = self._call_chat_completions(payload)
        content = (((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not content:
            raise TossGptError("OpenAI response did not include message content")
        self.last_usage = response.get("usage") or {}
        self.last_model = response.get("model") or self.model
        return {
            "model": self.last_model,
            "usage": self.last_usage,
            "analysis": content,
        }

    def _build_payload(self, evidence, symbols=None):
        safe_evidence = sanitize_payload(evidence or {})
        data_json = json.dumps(safe_evidence, ensure_ascii=False, default=str, separators=(",", ":"))
        target_symbols = ", ".join(symbols or safe_evidence.get("symbols") or [])
        system_prompt = (
            "You are a conservative US-stock trading analysis assistant. "
            "You never place orders. You separate evidence, missing data, risk, "
            "and watch conditions. Reply in Korean."
        )
        user_prompt = (
            "Analyze this read-only Toss Invest Open API evidence for US stocks.\n"
            "Target symbols: {symbols}\n\n"
            "Reply in Korean, but keep any machine-readable labels in English.\n"
            "Requirements:\n"
            "1. Do not give buy/sell instructions. Use watch, hold, risk, or avoid style language only.\n"
            "2. Separate confirmed evidence from missing or weak data.\n"
            "3. Explain what should be checked during the next market window.\n"
            "4. Never imply order execution, guaranteed profit, or certainty.\n\n"
            "EVIDENCE_JSON:\n{data_json}"
        ).format(symbols=target_symbols, data_json=data_json)
        return self._chat_payload(system_prompt, user_prompt, self.max_tokens)

    def _build_focused_payload(self, evidence, symbols=None):
        safe_evidence = sanitize_payload(evidence or {})
        target_symbols = ", ".join(symbols or safe_evidence.get("symbols") or [])
        prompt_evidence = _focused_prompt_evidence(safe_evidence, symbols=symbols)
        data_json = json.dumps(prompt_evidence, ensure_ascii=False, default=str, separators=(",", ":"))
        system_prompt = (
            "You are a conservative focused-equity analysis engine modeled after "
            "a personal intraday trading analysis workflow. You do not screen a "
            "broad universe and you never place orders. You only analyze the "
            "provided fixed watchlist. Reply in Korean with clear labels."
        )
        user_prompt = (
            "Run focused fixed-watchlist analysis.\n"
            "Target symbols: {symbols}\n\n"
            "Reply in Korean, but each symbol section must begin with these exact labels:\n"
            "SYMBOL: <ticker>\n"
            "DECISION: WATCH | OBSERVE | HOLD | RISK | AVOID\n"
            "INTEREST_SCORE: <0-100>\n"
            "RISK_LEVEL: LOW | MEDIUM | HIGH\n"
            "CONFIDENCE: LOW | MEDIUM | HIGH\n\n"
            "Then write the Korean analysis under the labels.\n\n"
            "Required sections:\n"
            "1. Korean summary conclusion by symbol.\n"
            "2. Evidence by symbol: price, 1-minute candles, daily candles, volume, index/ETF context, FX, market session.\n"
            "3. Return feedback: explicitly discuss paper-trade avg_return_pct, win_rate, worst_path_return_pct, and sample count if available.\n"
            "4. Weighting: momentum, volatility, trend-following risk, volume quality, missing data, and return feedback.\n"
            "5. Data gaps: what is not available in the current evidence and how it affects confidence.\n"
            "6. Change vs previous analysis if previous_analysis_context exists in evidence.\n"
            "7. Next checks by session: premarket, regular market, and after-hours checks.\n\n"
            "After all symbol sections, include GPT_STRUCTURED_JSON followed by one compact valid JSON object with this shape:\n"
            "{{\"symbols\":[{{\"symbol\":\"NVDA\",\"decision\":\"WATCH|OBSERVE|HOLD|RISK|AVOID\",\"interest_score\":0,\"risk_level\":\"LOW|MEDIUM|HIGH\",\"confidence\":\"LOW|MEDIUM|HIGH\",\"relationship_regime\":\"strong|moderate|weak|mixed|insufficient_evidence|unknown\",\"data_freshness\":\"fresh|stale|missing|unknown\",\"summary\":\"Korean short summary\",\"risk_flags\":[\"short risk flag\"],\"next_checks\":[\"short check\"]}}],\"shared_context_freshness\":{{\"latest_kiwoom_context_time\":\"string_or_null\",\"latest_toss_context_time\":\"string_or_null\",\"latest_relationship_context_time\":\"string_or_null\",\"stale_sections\":[\"section\"],\"missing_sections\":[\"section\"]}}}}\n\n"
            "Rules:\n"
            "- EVIDENCE_JSON is a quality-preserving prompt package; the raw source evidence is stored separately. Do not infer fields that are not present in this package.\n"
            "- Prioritize evidence_quality_manifest, data_quality, latest price/candle summaries, tick/orderbook events, return feedback, previous analysis, and relationship quality warnings over repeated raw fields.\n"
            "- If shared_context_status exists, explicitly use latest_kiwoom_context_time, latest_toss_context_time, latest_relationship_context_time, stale_sections, and missing_sections to set data_freshness.\n"
            "- If shared_context_status.status is missing, stale, partial, or has stale_sections/missing_sections, lower CONFIDENCE and do not treat cross-market context as fresh evidence.\n"
            "- If market_relationship.kiwoom_market_context.source_preference is shared_context_db, state whether the shared-hub context is fresh enough before using it.\n"
            "- Do not make entry or exit decisions from GPT alone.\n"
            "- If market_relationship exists, distinguish strong, weak, mixed, and insufficient_evidence regimes from the supplied paired_sample_count and correlation fields.\n"
            "- Never claim KR-US semiconductor correlation is strong when market_relationship.data_quality.warning is present.\n"
            "- Treat market_relationship.proxy_alignment as directional context only, not correlation proof.\n"
            "- If market_relationship.kiwoom_market_context exists, use it as KOSPI/KOSDAQ/index futures/foreign-institution flow background for Korea-to-US risk appetite, but do not treat it as US order evidence.\n"
            "- If market_relationship.kiwoom_market_context.sections.short_term_event_context exists, treat it as user-supplied short-term catalyst context only. Do not treat Micron/SCA/HBM event tags as proven correlation, and separate bullish catalyst interpretation from gap-up chase or profit-taking risk.\n"
            "- Use regression.beta, r_squared, hit_ratio_up, hit_ratio_down, and lead_score as sensitivity and directional reliability checks; do not rely on Pearson correlation alone.\n"
            "- If gap_effect.status is not_available, explicitly avoid claims about Korean open-gap or intraday reversal behavior.\n"
            "- If evidence is weak, lower INTEREST_SCORE and state hold/watch conditions.\n"
            "- If return feedback is negative or worst_path_return_pct is poor, lower INTEREST_SCORE even if momentum looks good.\n"
            "- If return feedback is positive with enough samples, explain why confidence can improve.\n"
            "- If QQQ, SPY, or SMH are present, use them as context for semiconductor names.\n"
            "- Treat premarket evidence as lower confidence than regular-session confirmation.\n"
            "- Never imply order execution, guaranteed profit, or certainty.\n\n"
            "EVIDENCE_JSON:\n{data_json}"
        ).format(symbols=target_symbols, data_json=data_json)
        return self._chat_payload(system_prompt, user_prompt, max(self.max_tokens, 1400))

    def _build_domestic_payload(self, evidence, symbols=None):
        safe_evidence = sanitize_payload(evidence or {})
        target_symbols = ", ".join(symbols or safe_evidence.get("symbols") or [])
        prompt_evidence = _domestic_prompt_evidence(safe_evidence, symbols=symbols)
        data_json = json.dumps(prompt_evidence, ensure_ascii=False, default=str, separators=(",", ":"))
        system_prompt = (
            "You are a conservative Korean domestic-stock analysis engine. "
            "You combine imported Kiwoom feedback with current Toss Invest read-only "
            "market evidence when available. You never place orders. Reply in Korean "
            "with clear labels."
        )
        user_prompt = (
            "Run domestic Korean-market focused analysis.\n"
            "Target symbols: {symbols}\n\n"
            "Reply in Korean, but each symbol section must begin with these exact labels:\n"
            "SYMBOL: <code>\n"
            "DECISION: WATCH | OBSERVE | HOLD | RISK | AVOID\n"
            "INTEREST_SCORE: <0-100>\n"
            "RISK_LEVEL: LOW | MEDIUM | HIGH\n"
            "CONFIDENCE: LOW | MEDIUM | HIGH\n\n"
            "Required sections:\n"
            "1. Korean summary conclusion by domestic code/name.\n"
            "2. Imported Kiwoom feedback: 5m/10m/30m/60m avg_return_pct, win_rate, worst_path_return_pct, and sample_count.\n"
            "3. Latest signal: action_hint, confidence_score, risk_level, signal_count, and detected time.\n"
            "4. Toss live evidence if available: price, 1-minute candles, daily candles, FX, and KRX/NXT session.\n"
            "5. Data gaps: explicitly state whether Toss live data is missing and whether the analysis is feedback-only.\n"
            "6. Next checks for Korean pre-market, regular market, and after-market.\n\n"
            "After all symbol sections, include GPT_STRUCTURED_JSON followed by one compact valid JSON object with this shape:\n"
            "{{\"symbols\":[{{\"symbol\":\"005930\",\"decision\":\"WATCH|OBSERVE|HOLD|RISK|AVOID\",\"interest_score\":0,\"risk_level\":\"LOW|MEDIUM|HIGH\",\"confidence\":\"LOW|MEDIUM|HIGH\",\"relationship_regime\":\"strong|moderate|weak|mixed|insufficient_evidence|unknown\",\"data_freshness\":\"fresh|stale|missing|unknown\",\"summary\":\"Korean short summary\",\"risk_flags\":[\"short risk flag\"],\"next_checks\":[\"short check\"]}}],\"shared_context_freshness\":{{\"latest_kiwoom_context_time\":\"string_or_null\",\"latest_toss_context_time\":\"string_or_null\",\"latest_relationship_context_time\":\"string_or_null\",\"stale_sections\":[\"section\"],\"missing_sections\":[\"section\"]}}}}\n\n"
            "Rules:\n"
            "- EVIDENCE_JSON is a quality-preserving prompt package; the raw source evidence is stored separately. Do not infer fields that are not present in this package.\n"
            "- Prioritize data_quality, imported feedback, latest signal, Toss live summaries, and relationship quality warnings over repeated raw fields.\n"
            "- If shared_context_status exists, explicitly use latest_kiwoom_context_time, latest_toss_context_time, latest_relationship_context_time, stale_sections, and missing_sections to set data_freshness.\n"
            "- If shared_context_status.status is missing, stale, partial, or has stale_sections/missing_sections, lower CONFIDENCE and do not treat cross-market context as fresh evidence.\n"
            "- If market_relationship.kiwoom_market_context.source_preference is shared_context_db, state whether the shared-hub context is fresh enough before using it.\n"
            "- Do not make entry or exit decisions from GPT alone.\n"
            "- Use market_relationship to explain the US-to-next-KR loop only when paired observations support it.\n"
            "- If relationship_regime is insufficient_evidence, explicitly say the US/KR relationship is not proven for this window.\n"
            "- If market_relationship.kiwoom_market_context.sections.short_term_event_context exists, use it as short-term Micron/SCA/HBM catalyst background only. It must not override weak imported feedback, missing live data, or insufficient relationship evidence.\n"
            "- Do not confuse proxy alignment with true rolling or lead-lag correlation.\n"
            "- Use regression.beta, r_squared, hit_ratio_up, hit_ratio_down, and lead_score to separate direction match from actual sensitivity.\n"
            "- If gap_effect.status is not_available, do not claim whether a US move was reflected at KR open or reversed intraday.\n"
            "- If imported feedback is weak or worst_path_return_pct is poor, lower INTEREST_SCORE.\n"
            "- If only imported historical feedback exists, keep CONFIDENCE LOW or MEDIUM at most.\n"
            "- Never imply order execution, guaranteed profit, or certainty.\n\n"
            "EVIDENCE_JSON:\n{data_json}"
        ).format(symbols=target_symbols, data_json=data_json)
        return self._chat_payload(system_prompt, user_prompt, max(self.max_tokens, 1400))

    def _chat_payload(self, system_prompt, user_prompt, max_tokens):
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }

    def _call_chat_completions(self, payload, max_retries=2):
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": "Bearer {}".format(self.api_key),
            "Content-Type": "application/json",
        }
        request = urllib.request.Request(
            OPENAI_CHAT_COMPLETIONS_URL,
            data=body,
            headers=headers,
            method="POST",
        )
        delay = 2
        timeout = int(os.environ.get("OPENAI_REQUEST_TIMEOUT_SEC", "120"))
        for attempt in range(max_retries + 1):
            try:
                with self.opener(request, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                if attempt >= max_retries:
                    raise TossGptError("OpenAI request failed: {}".format(exc))
                time.sleep(delay)
                delay *= 2


def _focused_prompt_evidence(evidence, symbols=None):
    symbols = [str(item).strip().upper() for item in symbols or evidence.get("symbols") or [] if str(item).strip()]
    symbol_evidence = evidence.get("symbol_evidence") or {}
    return {
        "mode": evidence.get("mode"),
        "broker": evidence.get("broker"),
        "collected_at": evidence.get("collected_at"),
        "symbols": symbols,
        "evidence_quality_manifest": {
            "prompt_package": "quality_preserving_compact_v1",
            "raw_evidence_storage": "analysis_results.evidence_json",
            "raw_account_payloads_excluded": True,
            "purpose": "reduce repeated API noise while preserving decision-critical evidence",
            "must_not_infer_missing_fields": True,
        },
        "data_quality": evidence.get("data_quality"),
        "errors": evidence.get("errors") or [],
        "sessions": evidence.get("sessions") or {},
        "exchange_rate": _compact_exchange_rate(evidence.get("exchange_rate")),
        "market_context": _compact_market_context(evidence),
        "shared_context_status": _compact_shared_context_status(evidence.get("shared_context_status")),
        "symbol_evidence": {
            symbol: _compact_focused_symbol(symbol_evidence.get(symbol) or {})
            for symbol in symbols
        },
        "paper_feedback_summary": _top_feedback_rows(evidence.get("paper_feedback_summary"), symbols),
        "return_feedback_by_symbol": _filter_symbol_map(evidence.get("return_feedback_by_symbol"), symbols),
        "feedback_adjustments": _filter_symbol_map(evidence.get("feedback_adjustments"), symbols),
        "previous_analysis_context": _filter_symbol_map(evidence.get("previous_analysis_context"), symbols),
        "market_relationship": _compact_relationship(evidence.get("market_relationship")),
    }


def _domestic_prompt_evidence(evidence, symbols=None):
    symbols = [str(item).strip() for item in symbols or evidence.get("symbols") or [] if str(item).strip()]
    symbol_evidence = evidence.get("symbol_evidence") or {}
    return {
        "mode": evidence.get("mode"),
        "broker": evidence.get("broker"),
        "collected_at": evidence.get("collected_at"),
        "symbols": symbols,
        "evidence_quality_manifest": {
            "prompt_package": "quality_preserving_compact_v1",
            "raw_evidence_storage": "analysis_results.evidence_json",
            "raw_account_payloads_excluded": True,
            "purpose": "preserve imported feedback and live Toss summaries without repeated raw payload noise",
            "must_not_infer_missing_fields": True,
        },
        "data_quality": evidence.get("data_quality"),
        "errors": evidence.get("errors") or [],
        "sessions": evidence.get("sessions") or {},
        "exchange_rate": _compact_exchange_rate(evidence.get("exchange_rate")),
        "shared_context_status": _compact_shared_context_status(evidence.get("shared_context_status")),
        "domestic_feedback": _top_feedback_rows(evidence.get("domestic_feedback"), symbols, key_name="code"),
        "domestic_signals": _top_feedback_rows(evidence.get("domestic_signals"), symbols, key_name="code"),
        "symbol_evidence": {
            symbol: _compact_domestic_symbol(symbol_evidence.get(symbol) or {})
            for symbol in symbols
        },
        "market_relationship": _compact_relationship(evidence.get("market_relationship")),
    }


def _compact_focused_symbol(item):
    return {
        "symbol": item.get("symbol"),
        "price": _selected(item.get("price"), [
            "symbol", "lastPrice", "last", "openPrice", "highPrice", "lowPrice",
            "change", "changeRate", "changePrice", "volume", "currency",
            "marketStatus", "tradeTime", "timestamp",
        ]),
        "stock": _selected(item.get("stock"), [
            "symbol", "name", "market", "exchange", "securityType", "status",
        ]),
        "minute_candles_summary": item.get("minute_candles_summary"),
        "daily_candles_summary": item.get("daily_candles_summary"),
        "feedback_adjustment": item.get("feedback_adjustment"),
        "previous_analysis": item.get("previous_analysis"),
        "errors": item.get("errors") or [],
    }


def _compact_domestic_symbol(item):
    return {
        "code": item.get("code") or item.get("symbol"),
        "name": item.get("name"),
        "price": _selected(item.get("price"), [
            "symbol", "code", "name", "lastPrice", "openPrice", "highPrice",
            "lowPrice", "change", "changeRate", "volume", "currency",
            "marketStatus", "tradeTime", "timestamp",
        ]),
        "stock": _selected(item.get("stock"), [
            "symbol", "code", "name", "market", "exchange", "securityType", "status",
        ]),
        "minute_candles_summary": item.get("minute_candles_summary"),
        "daily_candles_summary": item.get("daily_candles_summary"),
        "imported_feedback": item.get("imported_feedback") or item.get("feedback"),
        "latest_signal": item.get("latest_signal") or item.get("signal"),
        "errors": item.get("errors") or [],
    }


def _compact_exchange_rate(exchange_rate):
    result = (exchange_rate or {}).get("result")
    if isinstance(result, dict):
        return {"result": _selected(result, ["baseCurrency", "quoteCurrency", "rate", "timestamp", "time", "date"])}
    return exchange_rate


def _compact_market_context(evidence):
    prices = {}
    for item in ((evidence.get("prices") or {}).get("result") or []):
        symbol = str(item.get("symbol") or "").upper()
        if symbol in ("QQQ", "SPY", "SMH", "SOXX"):
            prices[symbol] = _selected(item, [
                "symbol", "lastPrice", "changeRate", "changePrice", "volume",
                "currency", "marketStatus", "tradeTime", "timestamp",
            ])
    return {"index_etf_prices": prices}


def _compact_shared_context_status(status):
    if not isinstance(status, dict):
        return status
    return _selected(status, [
        "status",
        "db_path",
        "latest_kiwoom_context_time",
        "latest_toss_context_time",
        "latest_relationship_context_time",
        "stale_sections",
        "missing_sections",
        "relationship_regime",
        "paired_sample_count",
        "intraday_timing_allowed",
    ])


def _compact_relationship(relationship):
    if not relationship:
        return None
    pairs = []
    for pair in relationship.get("pairs") or []:
        pairs.append({
            "source_symbol": pair.get("source_symbol"),
            "target_symbol": pair.get("target_symbol"),
            "analysis_direction": pair.get("analysis_direction"),
            "resolution": pair.get("resolution"),
            "lag_label": pair.get("lag_label"),
            "paired_sample_count": pair.get("paired_sample_count"),
            "correlation": pair.get("correlation"),
            "regression": pair.get("regression"),
            "directional_stats": pair.get("directional_stats"),
            "lead_score": pair.get("lead_score"),
            "relationship_regime": pair.get("relationship_regime"),
            "latest_observed_at": pair.get("latest_observed_at"),
            "rolling_correlation": pair.get("rolling_correlation"),
            "gap_effect": pair.get("gap_effect"),
        })
    return {
        "mode": relationship.get("mode"),
        "generated_at": relationship.get("generated_at"),
        "domestic_codes": relationship.get("domestic_codes"),
        "us_symbols": relationship.get("us_symbols"),
        "data_quality": relationship.get("data_quality"),
        "relationship_regime": relationship.get("relationship_regime"),
        "pairs": pairs,
        "proxy_alignment": relationship.get("proxy_alignment"),
        "kiwoom_market_context": relationship.get("kiwoom_market_context"),
        "interpretation_rules": relationship.get("interpretation_rules"),
    }


def _filter_symbol_map(value, symbols):
    value = value or {}
    if not isinstance(value, dict):
        return {}
    return {symbol: value.get(symbol) for symbol in symbols if symbol in value}


def _top_feedback_rows(rows, symbols, key_name="symbol", max_rows_per_symbol=4):
    buckets = {symbol: [] for symbol in symbols}
    for row in rows or []:
        key = str(row.get(key_name) or "").upper() if key_name == "symbol" else str(row.get(key_name) or "")
        if key in buckets and len(buckets[key]) < max_rows_per_symbol:
            buckets[key].append(row)
    compact = []
    for symbol in symbols:
        compact.extend(buckets.get(symbol) or [])
    return compact


def _selected(value, keys):
    if not isinstance(value, dict):
        return value
    return {key: value.get(key) for key in keys if key in value}
