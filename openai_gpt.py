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
        data_json = json.dumps(safe_evidence, ensure_ascii=False, default=str, separators=(",", ":"))
        target_symbols = ", ".join(symbols or safe_evidence.get("symbols") or [])
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
            "Rules:\n"
            "- Do not make entry or exit decisions from GPT alone.\n"
            "- If evidence is weak, lower INTEREST_SCORE and state hold/watch conditions.\n"
            "- If return feedback is negative or worst_path_return_pct is poor, lower INTEREST_SCORE even if momentum looks good.\n"
            "- If return feedback is positive with enough samples, explain why confidence can improve.\n"
            "- If QQQ, SPY, or SMH are present, use them as context for semiconductor names.\n"
            "- Treat premarket evidence as lower confidence than regular-session confirmation.\n"
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
        for attempt in range(max_retries + 1):
            try:
                with self.opener(request, timeout=45) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                if attempt >= max_retries:
                    raise TossGptError("OpenAI request failed: {}".format(exc))
                time.sleep(delay)
                delay *= 2
