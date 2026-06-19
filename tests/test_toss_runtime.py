import json
import os
import sys
import tempfile
from datetime import datetime


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(ROOT)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from toss_trading_runtime.client import TossInvestClient, TossInvestClientError
from toss_trading_runtime.analysis_history import attach_previous_analysis_context, build_previous_analysis_context, compare_structured_to_previous
from toss_trading_runtime.dashboard import build_dashboard_snapshot, format_summary_text, render_dashboard_html
from toss_trading_runtime.dashboard_window import load_symbols, save_symbols, save_watchlist_file
from toss_trading_runtime.domestic_analysis import collect_domestic_evidence
from toss_trading_runtime.domestic_import import load_kiwoom_summaries
from toss_trading_runtime.evidence import collect_read_only_evidence
from toss_trading_runtime.focused_analysis import collect_focused_evidence, summarize_candles
from toss_trading_runtime.event_detector import detect_events
from toss_trading_runtime.feedback import attach_feedback_adjustments, build_feedback_adjustments
from toss_trading_runtime.market_calendar import current_kr_session, current_us_session
from toss_trading_runtime.openai_gpt import TossGptAnalyzer, TossGptError
from toss_trading_runtime.order_safety import TossOrderSafetyGate
from toss_trading_runtime.relationship_analysis import build_relationship_evidence
from toss_trading_runtime.runtime_health import build_runtime_health
from toss_trading_runtime.screener import score_symbol
from toss_trading_runtime.store import TossRuntimeStore
from toss_trading_runtime.structured_analysis import extract_structured_analysis
from toss_trading_runtime.supervisor import _summary_payload, _resolve_stop_time
from toss_trading_runtime.market_session_run import _new_session, _run_stage


class FakeResponse(object):
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeOpener(object):
    def __init__(self):
        self.requests = []

    def __call__(self, request, timeout=20):
        self.requests.append(request)
        url = request.full_url
        if url.endswith("/oauth2/token"):
            return FakeResponse({"access_token": "token-123", "token_type": "Bearer", "expires_in": 86400})
        if "/api/v1/accounts" in url:
            return FakeResponse({"result": [{"accountSeq": "123456789"}]})
        if "/api/v1/prices" in url:
            return FakeResponse({"result": [{"symbol": "AAPL", "lastPrice": "185.70", "currency": "USD"}]})
        if "/api/v1/market-calendar/US" in url:
            return FakeResponse({"result": {"marketCountry": "US", "isBusinessDay": True}})
        if "/api/v1/market-calendar/KR" in url:
            return FakeResponse({"result": {"today": {"integrated": {"preMarket": None, "regularMarket": None, "afterMarket": None}}}})
        if "/api/v1/stocks" in url:
            return FakeResponse({"result": [{"symbol": "AAPL", "market": "NASDAQ", "securityType": "FOREIGN_STOCK", "status": "ACTIVE"}]})
        if "/api/v1/candles" in url:
            return FakeResponse({"result": {"candles": [
                {"timestamp": "2026-06-16T22:31:00+09:00", "closePrice": "102", "volume": "200", "currency": "USD"},
                {"timestamp": "2026-06-16T22:30:00+09:00", "closePrice": "100", "volume": "100", "currency": "USD"},
            ]}})
        if "/api/v1/exchange-rate" in url:
            assert "baseCurrency=USD" in url
            assert "quoteCurrency=KRW" in url
            return FakeResponse({"result": {"baseCurrency": "USD", "quoteCurrency": "KRW", "rate": "1380.0"}})
        if "/api/v1/holdings" in url:
            return FakeResponse({"result": {"holdings": []}})
        if "/api/v1/buying-power" in url:
            return FakeResponse({"result": {"currency": "USD", "cashBuyingPower": "1000.00"}})
        return FakeResponse({"result": {}})


def test_client_uses_read_only_endpoints_and_account_header():
    opener = FakeOpener()
    client = TossInvestClient(
        client_id="client",
        client_secret="secret",
        account_seq="123456789",
        base_url="https://example.test",
        opener=opener,
    )
    evidence = collect_read_only_evidence(client, ["AAPL"], account_seq="123456789")
    assert evidence["safe_for_analysis"]
    urls = [request.full_url for request in opener.requests]
    assert any("/oauth2/token" in url for url in urls)
    assert any("/api/v1/prices" in url for url in urls)
    assert not any("/api/v1/orders" in url for url in urls)
    account_requests = [request for request in opener.requests if "/api/v1/holdings" in request.full_url]
    assert account_requests[0].headers.get("X-tossinvest-account") == "123456789"


def test_client_rejects_order_creation_in_scaffold():
    client = TossInvestClient(client_id="client", client_secret="secret")
    try:
        client.create_order({"symbol": "AAPL"})
    except TossInvestClientError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("create_order should be disabled")


def test_order_safety_blocks_default_disabled_mode():
    gate = TossOrderSafetyGate()
    result = gate.validate(
        {"symbol": "AAPL", "side": "BUY", "quantity": "1", "price": "185.70"},
        runtime_evidence={
            "account_seq_present": True,
            "us_market_calendar_ok": True,
            "recent_market_data_ok": True,
            "buying_power_usd": 1000,
            "estimated_amount_usd": 185.70,
        },
        deterministic_result={"passed": True, "confidence_score": 85},
        gpt_review={"decision": "approve", "confidence": 85, "risk_flags": []},
    )
    assert not result["passed"]
    assert "ORDER_MODE is disabled" in result["messages"]


def test_order_safety_warns_when_kiwoom_runtime_active():
    class DryRunSettings(object):
        pass

    import toss_trading_runtime.config as config
    for name in dir(config):
        if name.isupper():
            setattr(DryRunSettings, name, getattr(config, name))
    DryRunSettings.ORDER_MODE = "dry_run"
    DryRunSettings.MAX_ORDER_AMOUNT_USD = 1000

    gate = TossOrderSafetyGate(settings=DryRunSettings)
    result = gate.validate(
        {"symbol": "AAPL", "side": "BUY", "quantity": "1", "price": "185.70", "order_mode": "dry_run"},
        runtime_evidence={
            "account_seq_present": True,
            "us_market_calendar_ok": True,
            "recent_market_data_ok": True,
            "buying_power_usd": 1000,
            "estimated_amount_usd": 185.70,
            "kiwoom_runtime_active": True,
        },
        deterministic_result={"passed": True, "confidence_score": 85},
        gpt_review={"decision": "approve", "confidence": 85, "risk_flags": []},
    )
    assert result["passed"]
    assert any("Kiwoom runtime is active" in item for item in result["warnings"])


def test_gpt_analyzer_blocks_without_key():
    analyzer = TossGptAnalyzer(api_key="", opener=FakeOpener())
    try:
        analyzer.analyze_evidence({"symbols": ["AAPL"]})
    except TossGptError as exc:
        assert "missing environment variables" in str(exc)
    else:
        raise AssertionError("GPT analyzer should block without OPENAI_API_KEY")


def test_gpt_analyzer_sanitizes_prompt_and_returns_analysis():
    class FakeOpenAiOpener(object):
        def __init__(self):
            self.body = None

        def __call__(self, request, timeout=45):
            self.body = request.data.decode("utf-8")
            return FakeResponse({
                "model": "gpt-4o-mini-test",
                "choices": [{"message": {"content": "분석 결과"}}],
                "usage": {"total_tokens": 10},
            })

    opener = FakeOpenAiOpener()
    analyzer = TossGptAnalyzer(api_key="sk-test", opener=opener)
    result = analyzer.analyze_evidence({
        "symbols": ["AAPL"],
        "accountSeq": "123456789",
        "access_token": "secret-token",
    })
    assert result["analysis"] == "분석 결과"
    assert "123456789" not in opener.body
    assert "secret-token" not in opener.body


def test_market_session_helpers_detect_us_premarket_and_kr_nxt():
    us = {"result": {"today": {
        "preMarket": {"startTime": "2026-06-16T17:00:00+09:00", "endTime": "2026-06-16T22:30:00+09:00"},
        "regularMarket": {"startTime": "2026-06-16T22:30:00+09:00", "endTime": "2026-06-17T05:00:00+09:00"},
    }}}
    kr = {"result": {"today": {"integrated": {
        "preMarket": {"startTime": "2026-06-16T08:00:00+09:00", "endTime": "2026-06-16T08:50:00+09:00"},
        "regularMarket": {"startTime": "2026-06-16T09:00:00+09:00", "endTime": "2026-06-16T15:30:00+09:00"},
    }}}}
    from datetime import datetime, timezone
    now = datetime.fromisoformat("2026-06-16T18:00:00+09:00")
    assert current_us_session(us, now=now)["session"] == "preMarket"
    now_kr = datetime.fromisoformat("2026-06-16T08:30:00+09:00")
    assert current_kr_session(kr, now=now_kr)["session"] == "preMarket"


def test_us_session_infers_kst_overnight_regular_market():
    us = {"result": {"today": {
        "dayMarket": {"startTime": "2026-06-17T09:00:00+09:00", "endTime": "2026-06-17T17:00:00+09:00"},
        "preMarket": {"startTime": "2026-06-17T17:00:00+09:00", "endTime": "2026-06-17T22:30:00+09:00"},
        "regularMarket": {"startTime": "2026-06-17T22:30:00+09:00", "endTime": "2026-06-18T05:00:00+09:00"},
        "afterMarket": {"startTime": "2026-06-18T05:00:00+09:00", "endTime": "2026-06-18T08:50:00+09:00"},
    }}}
    now = datetime.fromisoformat("2026-06-17T01:53:00+09:00")
    session = current_us_session(us, now=now)
    assert session["session"] == "regularMarket"
    assert session["inferred"]


def test_score_symbol_ranks_momentum_and_volume():
    row = score_symbol(
        "AAPL",
        {"lastPrice": "102", "currency": "USD", "timestamp": "2026-06-16T22:31:00+09:00"},
        {"symbol": "AAPL", "market": "NASDAQ", "securityType": "FOREIGN_STOCK", "status": "ACTIVE"},
        [
            {"closePrice": "102", "volume": "300"},
            {"closePrice": "100", "volume": "100"},
        ],
    )
    assert row["score"] > 0
    assert row["candle_change_pct"] > 0
    assert row["volume_ratio"] > 1


def test_focused_evidence_collects_minute_and_daily_summaries():
    opener = FakeOpener()
    client = TossInvestClient(
        client_id="client",
        client_secret="secret",
        account_seq="123456789",
        base_url="https://example.test",
        opener=opener,
    )
    evidence = collect_focused_evidence(client, ["AAPL"], account_seq="123456789", minute_count=2, daily_count=2)
    assert evidence["mode"] == "focused_watchlist"
    assert evidence["safe_for_analysis"]
    assert evidence["symbol_evidence"]["AAPL"]["minute_candles_summary"]["sample"] == 2
    assert evidence["symbol_evidence"]["AAPL"]["daily_candles_summary"]["sample"] == 2
    urls = [request.full_url for request in opener.requests]
    assert any("interval=1m" in url for url in urls)
    assert any("interval=1d" in url for url in urls)


def test_focused_evidence_allows_noncritical_calendar_gap():
    class CalendarGapOpener(FakeOpener):
        def __call__(self, request, timeout=20):
            if "/api/v1/market-calendar/US" in request.full_url:
                raise RuntimeError("calendar unavailable")
            return super(CalendarGapOpener, self).__call__(request, timeout=timeout)

    client = TossInvestClient(
        client_id="client",
        client_secret="secret",
        account_seq="123456789",
        base_url="https://example.test",
        opener=CalendarGapOpener(),
    )
    evidence = collect_focused_evidence(client, ["AAPL"], account_seq="123456789", minute_count=2, daily_count=2)
    assert evidence["safe_for_analysis"]
    assert evidence["data_quality"]["missing_us_calendar"]
    events = detect_events(evidence)
    assert any(event["symbol"] == "GLOBAL" and event["event_type"] == "DATA_GAP" for event in events)


def test_summarize_candles_returns_personal_style_inputs():
    summary = summarize_candles([
        {"timestamp": "2026-06-16T22:31:00+09:00", "highPrice": "110", "lowPrice": "100", "closePrice": "108", "volume": "300"},
        {"timestamp": "2026-06-16T22:30:00+09:00", "highPrice": "105", "lowPrice": "99", "closePrice": "100", "volume": "100"},
    ], label="1m")
    assert summary["change_pct"] > 0
    assert summary["volume_ratio"] > 1
    assert summary["range_position_pct"] is not None
    assert summary["ma5"] is not None
    assert summary["rsi14"] is not None
    assert summary["vwap"] is not None


def test_events_and_store_persist_focused_evidence():
    evidence = {
        "collected_at": "2026-06-16 20:10:00.000000",
        "symbols": ["AAPL"],
        "prices": {"result": [{"symbol": "AAPL", "lastPrice": "102", "currency": "USD", "timestamp": "2026-06-16T20:10:00+09:00"}]},
        "exchange_rate": {"result": {"rate": "1380.0"}},
        "sessions": {"US": {"session": "preMarket"}, "KRX_NXT": {"session": "closed"}},
        "symbol_evidence": {
            "AAPL": {
                "stock": {"market": "NASDAQ"},
                "minute_candles_summary": {
                    "label": "1m",
                    "sample": 2,
                    "latest_timestamp": "2026-06-16T20:10:00+09:00",
                    "latest_close": 102,
                    "change_pct": 1.5,
                    "volume_ratio": 3.0,
                },
                "daily_candles_summary": {
                    "label": "1d",
                    "sample": 2,
                    "latest_timestamp": "2026-06-16",
                    "latest_close": 102,
                    "change_pct": 2.0,
                    "volume_ratio": 1.0,
                },
                "errors": [],
            }
        },
    }
    events = detect_events(evidence)
    assert any(event["event_type"] == "1m_MOMENTUM_UP" for event in events)
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    store = TossRuntimeStore(tmp.name)
    try:
        store.save_evidence(evidence, events=events)
        analysis_id = store.save_analysis_result(
            evidence,
            {"model": "test", "usage": {"total_tokens": 10}, "analysis": "ok"},
            events=events,
        )
        structured = extract_structured_analysis(
            "SYMBOL: AAPL\nDECISION: WATCH\nINTEREST_SCORE: 80\nRISK_LEVEL: MEDIUM\nCONFIDENCE: MEDIUM\n",
            ["AAPL"],
        )
        store.save_structured_analysis(analysis_id, structured)
        created = store.create_paper_candidates(analysis_id, evidence, horizons=(5,))
        assert created == 1
        assert store.paper_feedback_summary() == []
        assert store.count_rows("price_snapshots") == 1
        assert store.count_rows("candle_snapshots") == 2
        assert store.count_rows("event_logs") >= 1
        assert store.count_rows("analysis_results") == 1
        assert store.count_rows("paper_trade_candidates") == 1
        assert store.count_rows("structured_analysis") == 1
        assert structured[0]["final_decision"] == "WATCH"
        assert structured[0]["interest_score"] == 80
        assert structured[0]["risk_level"] == "MEDIUM"
        latest = store.latest_structured_by_symbol(["AAPL"])
        assert latest["AAPL"]["final_decision"] == "WATCH"
        summary = store.operational_summary()
        assert summary["tables"]["price_snapshots"] == 1
    finally:
        store.close()
        os.unlink(tmp.name)


def test_feedback_adjustments_attach_to_evidence():
    feedback = [
        {"symbol": "AAPL", "horizon_min": 5, "count": 3, "win_rate": 0.67, "avg_return_pct": 0.2, "best_path_return_pct": 0.4, "worst_path_return_pct": -0.1},
        {"symbol": "MSFT", "horizon_min": 5, "count": 3, "win_rate": 0.33, "avg_return_pct": -0.2, "best_path_return_pct": 0.1, "worst_path_return_pct": -0.6},
    ]
    adjustments = build_feedback_adjustments(feedback)
    assert adjustments["AAPL"]["score_adjustment"] > 0
    assert adjustments["MSFT"]["score_adjustment"] < 0
    assert adjustments["MSFT"]["worst_path_return_pct"] == -0.6
    evidence = {"symbol_evidence": {"AAPL": {}, "NVDA": {}}}
    attach_feedback_adjustments(evidence, feedback)
    assert evidence["symbol_evidence"]["AAPL"]["feedback_adjustment"]["samples"] == 3
    assert evidence["symbol_evidence"]["NVDA"]["feedback_adjustment"]["samples"] == 0


def test_previous_analysis_context_and_comparison():
    latest = {
        "AAPL": {
            "analysis_id": 7,
            "final_decision": "OBSERVE",
            "interest_score": 50,
            "risk_level": "MEDIUM",
            "confidence": "LOW",
            "summary": "old",
        }
    }
    context = build_previous_analysis_context(latest)
    evidence = {"symbol_evidence": {"AAPL": {}, "QQQ": {}}}
    attach_previous_analysis_context(evidence, context)
    assert evidence["symbol_evidence"]["AAPL"]["previous_analysis"]["previous_decision"] == "OBSERVE"
    assert evidence["symbol_evidence"]["QQQ"]["previous_analysis"]["previous_decision"] is None
    comparison = compare_structured_to_previous([
        {"symbol": "AAPL", "final_decision": "WATCH", "interest_score": 65, "risk_level": "HIGH", "confidence": "MEDIUM"}
    ], context)
    assert comparison[0]["interest_score_delta"] == 15
    assert comparison[0]["previous_decision"] == "OBSERVE"


def test_structured_analysis_splits_markdown_symbol_sections():
    text = (
        "**SYMBOL: MU**\n"
        "**DECISION:** HOLD\n"
        "**INTEREST_SCORE:** 45\n"
        "**RISK_LEVEL:** HIGH\n"
        "**CONFIDENCE:** LOW\n"
        "MU only.\n"
        "---\n"
        "**SYMBOL: NVDA**\n"
        "**DECISION:** OBSERVE\n"
        "**INTEREST_SCORE:** 65\n"
        "**RISK_LEVEL:** MEDIUM\n"
        "**CONFIDENCE:** MEDIUM\n"
        "NVDA only.\n"
    )
    structured = extract_structured_analysis(text, ["MU", "NVDA"])
    assert structured[0]["symbol"] == "MU"
    assert "NVDA only" not in structured[0]["summary"]
    assert structured[0]["confidence"] == "LOW"
    assert structured[1]["symbol"] == "NVDA"
    assert structured[1]["interest_score"] == 65
    assert structured[1]["confidence"] == "MEDIUM"


def test_runtime_health_and_supervisor_summary_payload():
    evidence = {
        "collected_at": "2026-06-16 20:10:00.000000",
        "symbols": ["AAPL"],
        "prices": {"result": [{"symbol": "AAPL", "lastPrice": "102", "currency": "USD"}]},
        "exchange_rate": {"result": {"rate": "1380.0"}},
        "sessions": {"US": {"session": "preMarket"}, "KRX_NXT": {"session": "closed"}},
        "symbol_evidence": {
            "AAPL": {
                "minute_candles_summary": {"sample": 1, "latest_close": 102},
                "daily_candles_summary": {"sample": 1, "latest_close": 102},
            }
        },
    }
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    store = TossRuntimeStore(tmp.name)
    try:
        store.save_evidence(evidence, events=[])
        health = build_runtime_health(store, max_age_minutes=1)
        assert health["status"] in ("ok", "warning")
        assert "latest_price_age_min" in health["checks"]

        class Args(object):
            symbols = "AAPL"

        payload = _summary_payload(
            args=Args(),
            store=store,
            start_counts={"prices": 0, "candles": 0, "contexts": 0, "events": 0, "paper": 0},
            iterations=1,
            failures=0,
            total_events=0,
            stop_at=_resolve_stop_time(None, 1),
            status="running",
            last_session="preMarket",
            last_error=None,
        )
        assert payload["status"] == "running"
        assert payload["row_deltas"]["prices"] == 1
        assert payload["health"]["checks"]["latest_candle_age_min"] is not None
    finally:
        store.close()
        os.unlink(tmp.name)


def test_market_session_stage_records_success_and_failure():
    class Args(object):
        symbols = "AAPL,QQQ"
        collect_minutes = 1
        until_kst = None
        interval_sec = 30
        minute_count = 60
        daily_count = 20
        max_tokens = 1800
        skip_gpt = False

    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    try:
        session = _new_session(Args())
        ok_code = _run_stage(session, tmp.name, "ok_stage", lambda argv: 0, ["--x"], continue_on_codes=(0,))
        fail_code = _run_stage(session, tmp.name, "fail_stage", lambda argv: 3, [], continue_on_codes=(0,))
        assert ok_code == 0
        assert fail_code == 3
        assert session["stages"][0]["status"] == "ok"
        assert session["stages"][1]["status"] == "failed"
        with open(tmp.name, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        assert payload["symbols"] == ["AAPL", "QQQ"]
    finally:
        os.unlink(tmp.name)


def test_dashboard_snapshot_and_html_render():
    evidence = {
        "collected_at": "2026-06-16 20:10:00.000000",
        "symbols": ["AAPL"],
        "prices": {"result": [{"symbol": "AAPL", "lastPrice": "102", "currency": "USD"}]},
        "exchange_rate": {"result": {"rate": "1380.0"}},
        "sessions": {"US": {"session": "preMarket"}, "KRX_NXT": {"session": "closed"}},
        "symbol_evidence": {
            "AAPL": {
                "minute_candles_summary": {"sample": 1, "latest_close": 102, "change_pct": 1.5, "volume_ratio": 2.0},
                "daily_candles_summary": {"sample": 1, "latest_close": 102, "change_pct": 2.5, "volume_ratio": 1.0},
            }
        },
    }
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    store = TossRuntimeStore(tmp.name)
    try:
        store.save_evidence(evidence, events=[])
        first_id = store.save_analysis_result(evidence, {"model": "test", "usage": {"total_tokens": 1}, "analysis": "old"})
        store.save_structured_analysis(first_id, [{
            "symbol": "AAPL",
            "final_decision": "OBSERVE",
            "interest_score": 50,
            "risk_level": "MEDIUM",
            "confidence": "LOW",
            "summary": "old",
        }])
        second_id = store.save_analysis_result(
            evidence,
            {
                "model": "test",
                "usage": {"total_tokens": 2},
                "analysis": "SYMBOL: AAPL\nDECISION: WATCH\nAAPL section only.\n",
            },
        )
        store.save_structured_analysis(second_id, [{
            "symbol": "AAPL",
            "final_decision": "WATCH",
            "interest_score": 60,
            "risk_level": "MEDIUM",
            "confidence": "MEDIUM",
            "summary": "new",
        }])
        store.upsert_domestic_feedback_summary([{
            "source": "kiwoom_personal_ver1",
            "code": "005930",
            "name": "Samsung Electronics",
            "horizon_min": 5,
            "sample_count": 2,
            "win_rate": 0.5,
            "avg_return_pct": 0.15,
            "avg_win_return_pct": 0.3,
            "avg_loss_return_pct": -0.1,
            "best_return_pct": 0.3,
            "worst_return_pct": -0.1,
            "best_path_return_pct": 0.3,
            "worst_path_return_pct": -0.1,
        }])
        store.upsert_domestic_signal_summary([{
            "source": "kiwoom_personal_ver1",
            "code": "005930",
            "name": "Samsung Electronics",
            "latest_detected_at": "2026-06-16 10:00:00",
            "latest_action_hint": "WATCH",
            "latest_confidence_score": 72,
            "latest_risk_level": "MEDIUM",
            "signal_count": 3,
        }])
        store.save_relationship_observations([
            {
                "observed_at": "2026-06-16 15:40:00",
                "source_symbol": "005930",
                "target_symbol": "AAPL",
                "source_return_pct": 0.10,
                "target_return_pct": 0.20,
                "lag_label": "kr_close_to_us_regular",
            },
            {
                "observed_at": "2026-06-17 15:40:00",
                "source_symbol": "005930",
                "target_symbol": "AAPL",
                "source_return_pct": 0.20,
                "target_return_pct": 0.40,
                "lag_label": "kr_close_to_us_regular",
            },
            {
                "observed_at": "2026-06-18 15:40:00",
                "source_symbol": "005930",
                "target_symbol": "AAPL",
                "source_return_pct": 0.30,
                "target_return_pct": 0.60,
                "lag_label": "kr_close_to_us_regular",
            },
            {
                "observed_at": "2026-06-19 15:40:00",
                "source_symbol": "005930",
                "target_symbol": "AAPL",
                "source_return_pct": 0.40,
                "target_return_pct": 0.80,
                "lag_label": "kr_close_to_us_regular",
            },
            {
                "observed_at": "2026-06-20 15:40:00",
                "source_symbol": "005930",
                "target_symbol": "AAPL",
                "source_return_pct": 0.50,
                "target_return_pct": 1.00,
                "lag_label": "kr_close_to_us_regular",
            },
        ])
        snapshot = build_dashboard_snapshot(store, symbols=["AAPL"])
        html = render_dashboard_html(snapshot)
        assert snapshot["symbols"][0]["score_delta"] == 10
        assert snapshot["symbols"][0]["detail"]["minute"]["sample"] == 1
        assert snapshot["symbols"][0]["minute_series"]
        assert snapshot["score_history"]["AAPL"][-1]["interest_score"] == 60
        assert snapshot["latest_gpt"]["total_tokens"] == 2
        assert "AAPL section only" in snapshot["gpt_sections"]["AAPL"]
        assert "paper_candidates" in snapshot
        assert snapshot["symbols"][0]["return_feedback"]["samples"] == 0
        assert snapshot["domestic"][0]["code"] == "005930"
        assert snapshot["relationship"]["relationship_regime"] == "strong"
        assert "Toss Focused Dashboard" in html
        assert "Domestic Market" in html
        assert "KR-US Relationship" in html
        assert "AAPL" in html
        formatted = format_summary_text("AAPL **EVIDENCE:** - one - two **NEXT CHECKS:** done")
        assert "\n\nEVIDENCE:" in formatted
        assert "\n- one" in formatted
    finally:
        store.close()
        os.unlink(tmp.name)


def test_relationship_evidence_blocks_correlation_claim_without_pairs():
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    store = TossRuntimeStore(tmp.name)
    try:
        store.upsert_domestic_feedback_summary([{
            "source": "kiwoom_personal_ver1",
            "code": "005930",
            "name": "Samsung Electronics",
            "horizon_min": 5,
            "sample_count": 10,
            "win_rate": 0.6,
            "avg_return_pct": 0.2,
            "avg_win_return_pct": 0.4,
            "avg_loss_return_pct": -0.2,
            "best_return_pct": 1.0,
            "worst_return_pct": -0.5,
            "best_path_return_pct": 1.2,
            "worst_path_return_pct": -0.7,
        }])
        evidence = build_relationship_evidence(store, domestic_codes=["005930"], us_symbols=["NVDA"], min_samples=3)
        assert evidence["relationship_regime"] == "insufficient_evidence"
        assert evidence["data_quality"]["warning"]
        assert evidence["data_quality"]["uses_proxy_alignment"]
        assert evidence["proxy_alignment"]["domestic"]["005930"]["sample_count"] == 10
    finally:
        store.close()
        os.unlink(tmp.name)


def test_relationship_evidence_detects_strong_paired_relationship():
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    store = TossRuntimeStore(tmp.name)
    try:
        rows = []
        for index, value in enumerate([0.1, 0.2, -0.1, 0.4, -0.3], start=1):
            rows.append({
                "observed_at": "2026-06-{:02d} 15:40:00".format(index),
                "source_symbol": "005930",
                "target_symbol": "NVDA",
                "source_return_pct": value,
                "target_return_pct": value * 2,
                "lag_label": "kr_close_to_us_regular",
            })
        store.save_relationship_observations(rows)
        evidence = build_relationship_evidence(store, domestic_codes=["005930"], us_symbols=["NVDA"], min_samples=5)
        assert evidence["relationship_regime"] == "strong"
        assert evidence["data_quality"]["warning"] is None
        assert evidence["pairs"][0]["paired_sample_count"] == 5
        assert evidence["pairs"][0]["correlation"] >= 0.65
    finally:
        store.close()
        os.unlink(tmp.name)


def test_dashboard_window_symbol_persistence():
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    try:
        saved = save_symbols(["mu", " nvda ", "QQQ"], tmp.name)
        assert saved == ["MU", "NVDA", "QQQ"]
        assert load_symbols(tmp.name) == ["MU", "NVDA", "QQQ"]
    finally:
        os.unlink(tmp.name)


def test_dashboard_window_watchlist_file_persistence():
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    try:
        path = save_watchlist_file(["mu", " nvda ", "QQQ"], tmp.name, market="US", label="US Focus")
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        assert payload["market"] == "US"
        assert payload["label"] == "US Focus"
        assert payload["symbols"] == ["MU", "NVDA", "QQQ"]
        assert payload["symbol_count"] == 3
        assert not payload["orders_enabled"]
    finally:
        os.unlink(tmp.name)


def test_domestic_import_loads_kiwoom_feedback_summary():
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    conn = None
    try:
        conn = __import__("sqlite3").connect(tmp.name)
        conn.execute("""
            CREATE TABLE paper_trade_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                return_5m_pct REAL,
                return_10m_pct REAL,
                return_30m_pct REAL,
                max_gain_30m_pct REAL,
                max_loss_30m_pct REAL,
                return_60m_pct REAL,
                max_gain_60m_pct REAL,
                max_loss_60m_pct REAL
            )
        """)
        conn.execute("""
            CREATE TABLE signal_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                detected_at TEXT,
                code TEXT,
                name TEXT,
                action_hint TEXT,
                confidence_score REAL,
                risk_level TEXT
            )
        """)
        conn.execute("INSERT INTO paper_trade_results (code, return_5m_pct, return_10m_pct, return_30m_pct, max_gain_30m_pct, max_loss_30m_pct, return_60m_pct, max_gain_60m_pct, max_loss_60m_pct) VALUES ('005930', 0.2, 0.1, -0.3, 0.4, -0.5, 0.6, 0.8, -0.2)")
        conn.execute("INSERT INTO paper_trade_results (code, return_5m_pct, return_10m_pct, return_30m_pct, max_gain_30m_pct, max_loss_30m_pct, return_60m_pct, max_gain_60m_pct, max_loss_60m_pct) VALUES ('005930', -0.1, 0.2, 0.4, 0.5, -0.2, -0.4, 0.1, -0.7)")
        conn.execute("INSERT INTO signal_logs (detected_at, code, name, action_hint, confidence_score, risk_level) VALUES ('2026-06-16 09:01:00', '005930', 'Samsung Electronics', 'OBSERVE', 61, 'LOW')")
        conn.execute("INSERT INTO signal_logs (detected_at, code, name, action_hint, confidence_score, risk_level) VALUES ('2026-06-16 10:01:00', '005930', 'Samsung Electronics', 'WATCH', 72, 'MEDIUM')")
        conn.commit()
        conn.close()
        conn = None

        feedback, signals = load_kiwoom_summaries(tmp.name, codes=["005930"], source="test_source")
        assert len(feedback) == 4
        by_horizon = {row["horizon_min"]: row for row in feedback}
        assert by_horizon[5]["sample_count"] == 2
        assert by_horizon[5]["win_rate"] == 0.5
        assert by_horizon[30]["worst_path_return_pct"] == -0.5
        assert signals[0]["latest_action_hint"] == "WATCH"
        assert signals[0]["signal_count"] == 2
    finally:
        if conn is not None:
            conn.close()
        os.unlink(tmp.name)


def test_domestic_evidence_and_gpt_payload_use_imported_feedback():
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    store = TossRuntimeStore(tmp.name)
    try:
        store.upsert_domestic_feedback_summary([{
            "source": "kiwoom_personal_ver1",
            "code": "005930",
            "name": "Samsung Electronics",
            "horizon_min": 5,
            "sample_count": 10,
            "win_rate": 0.6,
            "avg_return_pct": 0.2,
            "avg_win_return_pct": 0.4,
            "avg_loss_return_pct": -0.2,
            "best_return_pct": 1.0,
            "worst_return_pct": -0.5,
            "best_path_return_pct": 1.2,
            "worst_path_return_pct": -0.7,
        }])
        store.upsert_domestic_signal_summary([{
            "source": "kiwoom_personal_ver1",
            "code": "005930",
            "name": "Samsung Electronics",
            "latest_detected_at": "2026-06-16 09:10:00",
            "latest_action_hint": "WATCH",
            "latest_confidence_score": 70,
            "latest_risk_level": "MEDIUM",
            "signal_count": 2,
        }])
        evidence = collect_domestic_evidence(None, store, ["005930"])
        assert evidence["safe_for_analysis"]
        assert evidence["data_quality"]["feedback_only"]

        class FakeDomesticOpenAi(object):
            def __init__(self):
                self.body = None

            def __call__(self, request, timeout=45):
                self.body = request.data.decode("utf-8")
                return FakeResponse({
                    "model": "gpt-4o-mini-test",
                    "choices": [{"message": {"content": "SYMBOL: 005930\nDECISION: WATCH\nINTEREST_SCORE: 55\nRISK_LEVEL: MEDIUM\nCONFIDENCE: LOW\n국내장 분석"}}],
                    "usage": {"total_tokens": 20},
                })

        opener = FakeDomesticOpenAi()
        gpt = TossGptAnalyzer(api_key="sk-test", opener=opener).analyze_domestic_evidence(evidence, ["005930"])
        assert "domestic Korean-market" in opener.body
        assert "005930" in gpt["analysis"]
    finally:
        store.close()
        os.unlink(tmp.name)


if __name__ == "__main__":
    tests = [
        test_client_uses_read_only_endpoints_and_account_header,
        test_client_rejects_order_creation_in_scaffold,
        test_order_safety_blocks_default_disabled_mode,
        test_order_safety_warns_when_kiwoom_runtime_active,
        test_gpt_analyzer_blocks_without_key,
        test_gpt_analyzer_sanitizes_prompt_and_returns_analysis,
        test_market_session_helpers_detect_us_premarket_and_kr_nxt,
        test_us_session_infers_kst_overnight_regular_market,
        test_score_symbol_ranks_momentum_and_volume,
        test_focused_evidence_collects_minute_and_daily_summaries,
        test_focused_evidence_allows_noncritical_calendar_gap,
        test_summarize_candles_returns_personal_style_inputs,
        test_events_and_store_persist_focused_evidence,
        test_feedback_adjustments_attach_to_evidence,
        test_previous_analysis_context_and_comparison,
        test_structured_analysis_splits_markdown_symbol_sections,
        test_runtime_health_and_supervisor_summary_payload,
        test_market_session_stage_records_success_and_failure,
        test_dashboard_snapshot_and_html_render,
        test_relationship_evidence_blocks_correlation_claim_without_pairs,
        test_relationship_evidence_detects_strong_paired_relationship,
        test_dashboard_window_symbol_persistence,
        test_dashboard_window_watchlist_file_persistence,
        test_domestic_import_loads_kiwoom_feedback_summary,
        test_domestic_evidence_and_gpt_payload_use_imported_feedback,
    ]
    for test in tests:
        test()
        print("PASS {}".format(test.__name__))
