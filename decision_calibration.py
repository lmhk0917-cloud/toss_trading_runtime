"""Deterministic score calibration for Toss GPT outputs.

GPT produces the narrative and first-pass labels. This module makes the stored
decision react to numeric evidence so weak returns, adverse paths, stale shared
context, and live candle deterioration cannot be ignored by wording alone.
"""


DECISION_RANK = {
    "AVOID": 0,
    "RISK": 1,
    "OBSERVE": 2,
    "HOLD": 3,
    "WATCH": 4,
}

RISK_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
CONFIDENCE_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def calibrate_structured_analysis(structured, evidence):
    evidence = evidence or {}
    result = []
    for item in structured or []:
        symbol = str(item.get("symbol") or "").upper()
        symbol_evidence = (evidence.get("symbol_evidence") or {}).get(symbol) or {}
        calibrated = dict(item)
        score = _score(item.get("interest_score"))
        original_score = score
        decision = _choice(item.get("final_decision"), DECISION_RANK, "OBSERVE")
        risk = _choice(item.get("risk_level"), RISK_RANK, "MEDIUM")
        confidence = _choice(item.get("confidence"), CONFIDENCE_RANK, "LOW")
        reasons = []

        feedback_delta, feedback_reasons, feedback_caps = _feedback_adjustment(symbol_evidence)
        score += feedback_delta
        reasons.extend(feedback_reasons)
        decision = _min_decision(decision, feedback_caps.get("max_decision", decision))
        risk = _max_risk(risk, feedback_caps.get("min_risk", risk))
        confidence = _min_confidence(confidence, feedback_caps.get("max_confidence", confidence))

        quant_delta, quant_reasons, quant_caps = _quant_feedback_adjustment(symbol_evidence)
        score += quant_delta
        reasons.extend(quant_reasons)
        decision = _min_decision(decision, quant_caps.get("max_decision", decision))
        risk = _max_risk(risk, quant_caps.get("min_risk", risk))
        confidence = _min_confidence(confidence, quant_caps.get("max_confidence", confidence))

        live_delta, live_reasons, live_caps = _live_candle_adjustment(symbol_evidence)
        score += live_delta
        reasons.extend(live_reasons)
        decision = _min_decision(decision, live_caps.get("max_decision", decision))
        risk = _max_risk(risk, live_caps.get("min_risk", risk))
        confidence = _min_confidence(confidence, live_caps.get("max_confidence", confidence))

        context_delta, context_reasons, context_caps = _context_adjustment(evidence)
        score += context_delta
        reasons.extend(context_reasons)
        confidence = _min_confidence(confidence, context_caps.get("max_confidence", confidence))

        score = max(0, min(100, int(round(score))))
        severe_numeric_risk = _has_severe_numeric_risk(symbol_evidence)
        if decision == "AVOID" and not severe_numeric_risk:
            if score >= 50:
                decision = "OBSERVE"
            else:
                decision = "RISK"
                score = max(score, 40)
            risk = _max_risk(risk, "MEDIUM")
            confidence = _min_confidence(confidence, "MEDIUM")
            reasons.append("GPT AVOID softened because numeric evidence is not severe enough")
        if score < 35:
            decision = _min_decision(decision, "RISK")
            risk = _max_risk(risk, "HIGH")
        elif score < 50 and decision == "WATCH":
            decision = "OBSERVE"
        elif score >= 70 and risk != "HIGH" and confidence != "LOW" and decision in ("OBSERVE", "HOLD"):
            decision = "WATCH"

        calibration = {
            "original_interest_score": original_score,
            "calibrated_interest_score": score,
            "score_delta": score - original_score,
            "reasons": reasons[:10],
        }
        calibrated["interest_score"] = score
        calibrated["final_decision"] = decision
        calibrated["risk_level"] = risk
        calibrated["confidence"] = confidence
        calibrated["calibration"] = calibration
        calibrated["summary"] = _calibrated_summary(
            symbol=symbol,
            decision=decision,
            score=score,
            risk=risk,
            confidence=confidence,
            calibration=calibration,
            symbol_evidence=symbol_evidence,
            evidence=evidence,
        )
        result.append(calibrated)
    return result


def _feedback_adjustment(symbol_evidence):
    feedback = symbol_evidence.get("feedback_adjustment") or {}
    samples = int(feedback.get("samples") or 0)
    avg_return = _float(feedback.get("avg_return_pct"))
    win_rate = _float(feedback.get("win_rate"))
    worst_path = _float(feedback.get("worst_path_return_pct"))
    score_delta = int(feedback.get("score_adjustment") or 0)
    reasons = []
    caps = {}
    if samples <= 0:
        return 0, ["no evaluated paper feedback"], {"max_confidence": "MEDIUM"}
    if samples < 3:
        return -2, ["paper feedback sample too small"], {"max_confidence": "MEDIUM"}
    reasons.append("paper feedback score_adjustment={}".format(score_delta))
    if avg_return <= -0.15 or win_rate <= 0.40:
        caps["max_decision"] = "RISK"
        caps["min_risk"] = "HIGH"
        caps["max_confidence"] = "MEDIUM"
        reasons.append("weak feedback avg_return={:.4f} win_rate={:.4f}".format(avg_return, win_rate))
    elif avg_return < 0 or win_rate < 0.50:
        caps["max_decision"] = "OBSERVE"
        caps["max_confidence"] = "MEDIUM"
        reasons.append("mixed weak feedback avg_return={:.4f} win_rate={:.4f}".format(avg_return, win_rate))
    elif avg_return >= 0.15 and win_rate >= 0.55:
        reasons.append("positive feedback avg_return={:.4f} win_rate={:.4f}".format(avg_return, win_rate))
    if worst_path <= -3.0:
        score_delta -= 6
        caps["max_decision"] = "RISK"
        caps["min_risk"] = "HIGH"
        reasons.append("poor worst_path={:.4f}".format(worst_path))
    elif worst_path <= -1.5:
        score_delta -= 3
        caps["max_decision"] = _min_decision(caps.get("max_decision", "WATCH"), "OBSERVE")
        reasons.append("adverse worst_path={:.4f}".format(worst_path))
    elif worst_path <= -0.7:
        score_delta -= 1
        reasons.append("normal volatility worst_path={:.4f}".format(worst_path))
    return score_delta, reasons, caps


def _quant_feedback_adjustment(symbol_evidence):
    guidance = symbol_evidence.get("quant_feedback_guidance") or {}
    metrics = symbol_evidence.get("quant_feedback") or {}
    label = guidance.get("label")
    count = int(guidance.get("sample_count") or metrics.get("evaluated_count") or 0)
    expectancy = _float(metrics.get("expectancy_pct"))
    adverse = _float(metrics.get("adverse_path_rate_pct"))
    delta = 0
    reasons = []
    caps = {}
    if label == "positive_expectancy":
        delta += 6
        reasons.append("quant feedback positive expectancy samples={}".format(count))
    elif label == "negative_expectancy":
        delta -= 6
        caps["max_decision"] = "OBSERVE"
        caps["max_confidence"] = "MEDIUM"
        reasons.append("quant feedback negative expectancy samples={}".format(count))
    elif label == "mixed_expectancy":
        delta -= 3
        caps["max_decision"] = "OBSERVE"
        caps["max_confidence"] = "MEDIUM"
        reasons.append("quant feedback mixed expectancy samples={}".format(count))
    elif label == "sample_too_small":
        delta -= 2
        caps["max_confidence"] = "MEDIUM"
        reasons.append("quant feedback sample too small samples={}".format(count))
    if expectancy < -0.25:
        delta -= 5
        caps["max_decision"] = "RISK"
        caps["min_risk"] = "HIGH"
        reasons.append("strongly negative expectancy={:.4f}".format(expectancy))
    elif expectancy < -0.15:
        delta -= 3
        caps["max_decision"] = _min_decision(caps.get("max_decision", "WATCH"), "OBSERVE")
        reasons.append("negative expectancy={:.4f}".format(expectancy))
    if adverse >= 65:
        delta -= 4
        caps["min_risk"] = "HIGH"
        reasons.append("high adverse_path_rate={:.2f}".format(adverse))
    return delta, reasons, caps


def _live_candle_adjustment(symbol_evidence):
    minute = symbol_evidence.get("minute_candles_summary") or {}
    daily = symbol_evidence.get("daily_candles_summary") or {}
    delta = 0
    reasons = []
    caps = {}
    minute_change = _float(minute.get("change_pct"))
    minute_volume = _float(minute.get("volume_ratio"))
    daily_change = _float(daily.get("change_pct"))
    ma_spread = _float(daily.get("ma5_vs_ma20_pct"))
    rsi = _float(minute.get("rsi14"))
    vwap_distance = _float(minute.get("vwap_distance_pct"))

    if minute_change >= 1.0 and minute_volume >= 1.5:
        delta += 6
        reasons.append("1m momentum confirmed change={:.4f} volume_ratio={:.4f}".format(minute_change, minute_volume))
    elif minute_change <= -1.0:
        delta -= 8
        caps["max_decision"] = "RISK"
        caps["min_risk"] = "HIGH"
        reasons.append("1m momentum deteriorated change={:.4f}".format(minute_change))
    elif minute_change < 0:
        delta -= 3
        reasons.append("1m momentum weak change={:.4f}".format(minute_change))

    if daily_change >= 2.0 and ma_spread >= 0:
        delta += 4
        reasons.append("daily trend supportive change={:.4f}".format(daily_change))
    elif daily_change <= -2.0 or ma_spread <= -1.0:
        delta -= 5
        caps["max_decision"] = _min_decision(caps.get("max_decision", "WATCH"), "OBSERVE")
        reasons.append("daily trend weak change={:.4f} ma5_vs_ma20={:.4f}".format(daily_change, ma_spread))

    if rsi >= 78:
        delta -= 3
        reasons.append("short-term overbought rsi14={:.2f}".format(rsi))
    elif rsi <= 35 and minute_change < 0:
        delta -= 4
        caps["max_decision"] = _min_decision(caps.get("max_decision", "WATCH"), "OBSERVE")
        reasons.append("weak RSI with falling price rsi14={:.2f}".format(rsi))

    if vwap_distance <= -0.4:
        delta -= 3
        reasons.append("below VWAP distance={:.4f}".format(vwap_distance))
    return delta, reasons, caps


def _context_adjustment(evidence):
    status = evidence.get("shared_context_status") or {}
    relationship = evidence.get("market_relationship") or {}
    delta = 0
    reasons = []
    caps = {}
    stale = status.get("stale_sections") or []
    missing = status.get("missing_sections") or []
    if status.get("status") in ("missing", "stale", "partial") or stale or missing:
        delta -= 4
        caps["max_confidence"] = "MEDIUM"
        reasons.append("shared context not fully fresh")
    regime = relationship.get("relationship_regime")
    warning = (relationship.get("data_quality") or {}).get("warning")
    if regime in ("insufficient_evidence", "weak") or warning:
        delta -= 2
        caps["max_confidence"] = "MEDIUM"
        reasons.append("relationship evidence weak or insufficient")
    elif regime == "strong":
        delta += 2
        reasons.append("relationship evidence strong")
    return delta, reasons, caps


def _has_severe_numeric_risk(symbol_evidence):
    feedback = symbol_evidence.get("feedback_adjustment") or {}
    samples = int(feedback.get("samples") or 0)
    if samples >= 3:
        avg_return = _float(feedback.get("avg_return_pct"))
        win_rate = _float(feedback.get("win_rate"))
        worst_path = _float(feedback.get("worst_path_return_pct"))
        if avg_return <= -0.20 and win_rate <= 0.45:
            return True
        if worst_path <= -3.00:
            return True

    guidance = symbol_evidence.get("quant_feedback_guidance") or {}
    metrics = symbol_evidence.get("quant_feedback") or {}
    expectancy = _float(metrics.get("expectancy_pct"))
    adverse = _float(metrics.get("adverse_path_rate_pct"))
    if guidance.get("label") == "negative_expectancy" and (expectancy <= -0.25 or adverse >= 65):
        return True
    if expectancy <= -0.25:
        return True
    if adverse >= 65:
        return True

    minute = symbol_evidence.get("minute_candles_summary") or {}
    daily = symbol_evidence.get("daily_candles_summary") or {}
    minute_change = _float(minute.get("change_pct"))
    daily_change = _float(daily.get("change_pct"))
    if minute_change <= -1.0 and daily_change <= -2.0:
        return True
    if daily_change <= -10.0 and minute_change <= 0.0:
        return True
    return False


def _calibrated_summary(symbol, decision, score, risk, confidence, calibration, symbol_evidence=None, evidence=None):
    symbol_evidence = symbol_evidence or {}
    evidence = evidence or {}
    reasons = calibration.get("reasons") or []
    feedback = symbol_evidence.get("feedback_adjustment") or {}
    quant_guidance = symbol_evidence.get("quant_feedback_guidance") or {}
    minute = symbol_evidence.get("minute_candles_summary") or {}
    daily = symbol_evidence.get("daily_candles_summary") or {}
    relationship = evidence.get("market_relationship") or {}
    context_status = evidence.get("shared_context_status") or {}

    stance = _decision_lead(decision)
    feedback_text = _feedback_text(feedback, quant_guidance)
    chart_text = _chart_text(minute, daily)
    context_text = _context_text(relationship, context_status)
    reason_text = _human_reasons(reasons)
    next_check = _next_check(decision, minute, daily, feedback, context_status)

    return (
        "{symbol}: {stance}\n"
        "- 판단: {decision}({score}점), 위험도 {risk}, 신뢰도 {confidence}\n"
        "- 수치: {feedback_text} {chart_text}\n"
        "- 맥락: {context_text} 핵심 보정 근거는 {reason_text}입니다.\n"
        "- 다음 확인: {next_check}."
    ).format(
        symbol=symbol,
        stance=stance,
        decision=decision,
        score=score,
        risk=risk,
        confidence=confidence,
        feedback_text=feedback_text,
        chart_text=chart_text,
        context_text=context_text,
        reason_text=reason_text,
        next_check=next_check,
    )


def _feedback_text(feedback, quant_guidance):
    samples = int(feedback.get("samples") or 0)
    if samples <= 0:
        base = "아직 평가된 사후 피드백 샘플이 부족합니다."
    else:
        base = "사후 피드백은 샘플 {samples}건, 평균수익 {avg:+.2f}%, 승률 {win:.0f}%, worst path {worst:+.2f}%입니다.".format(
            samples=samples,
            avg=_float(feedback.get("avg_return_pct")),
            win=_float(feedback.get("win_rate")) * 100.0,
            worst=_float(feedback.get("worst_path_return_pct")),
        )
    label = quant_guidance.get("label")
    if label:
        return base + " 퀀트 피드백은 {}입니다.".format(_guidance_label(label))
    return base


def _chart_text(minute, daily):
    minute_change = _float(minute.get("change_pct"))
    minute_volume = _float(minute.get("volume_ratio"))
    daily_change = _float(daily.get("change_pct"))
    ma_spread = _float(daily.get("ma5_vs_ma20_pct"))
    return "차트 요약은 1분 변화 {m:+.2f}%, 거래량비 {v:.2f}, 일봉 변화 {d:+.2f}%, MA5-MA20 {ma:+.2f}%입니다.".format(
        m=minute_change,
        v=minute_volume,
        d=daily_change,
        ma=ma_spread,
    )


def _context_text(relationship, context_status):
    regime = relationship.get("relationship_regime") or "unknown"
    status = context_status.get("status") or "unknown"
    stale = context_status.get("stale_sections") or []
    missing = context_status.get("missing_sections") or []
    if stale or missing or status in ("missing", "stale", "partial"):
        return "공유 컨텍스트는 {} 상태라 KR-US 관계 해석은 보수적으로 봐야 합니다.".format(status)
    return "KR-US 관계 regime은 {}이고 공유 컨텍스트는 {} 상태입니다.".format(regime, status)


def _next_check(decision, minute, daily, feedback, context_status):
    minute_change = _float(minute.get("change_pct"))
    daily_change = _float(daily.get("change_pct"))
    worst = _float(feedback.get("worst_path_return_pct"))
    if decision == "AVOID":
        return "하락 둔화, 1분봉 반등 지속, worst path 축소가 확인되기 전까지 회피 유지"
    if decision == "RISK":
        if minute_change > 0 and daily_change < 0:
            return "단기 반등이 일봉 약세를 이기는지와 거래량 동반 여부"
        if worst <= -1.5:
            return "반등보다 하방 경로가 먼저 줄어드는지"
        return "다음 캔들에서 가격 유지와 shared context 신선도 회복 여부"
    if decision in ("OBSERVE", "HOLD"):
        return "가격 유지, 거래량비 개선, 피드백 샘플 추가"
    return "추격보다 눌림과 거래량 지속성"


def _guidance_label(label):
    mapping = {
        "positive_expectancy": "양호",
        "negative_expectancy": "약함",
        "mixed_expectancy": "혼재",
        "sample_too_small": "샘플 부족",
        "no_feedback": "없음",
    }
    return mapping.get(label, str(label))


def _decision_lead(decision):
    if decision == "WATCH":
        return "관심 유지 가능"
    if decision == "HOLD":
        return "대기 우선"
    if decision == "OBSERVE":
        return "관찰 대상"
    if decision == "RISK":
        return "주의 대상"
    if decision == "AVOID":
        return "회피 우선"
    return "판단 보류"


def _human_reasons(reasons):
    selected = []
    for reason in reasons:
        text = str(reason)
        if "AVOID softened" in text:
            selected.append("GPT의 과도한 AVOID 완화")
        elif "positive feedback" in text:
            selected.append("사후 피드백 양호")
        elif "weak feedback" in text or "mixed weak feedback" in text:
            selected.append("사후 피드백 약함")
        elif "normal volatility" in text:
            selected.append("adverse path가 일반 변동성 범위")
        elif "adverse worst_path" in text:
            selected.append("하방 경로 주의")
        elif "poor worst_path" in text:
            selected.append("큰 하방 경로")
        elif "positive expectancy" in text:
            selected.append("기대값 양호")
        elif "negative expectancy" in text:
            selected.append("기대값 약함")
        elif "1m momentum confirmed" in text:
            selected.append("1분봉 모멘텀 확인")
        elif "1m momentum deteriorated" in text:
            selected.append("1분봉 악화")
        elif "daily trend weak" in text:
            selected.append("일봉 추세 약함")
        elif "daily trend supportive" in text:
            selected.append("일봉 추세 우호적")
        elif "relationship evidence weak" in text:
            selected.append("KR-US 관계 근거 약함")
        elif "shared context not fully fresh" in text:
            selected.append("공유 컨텍스트 신선도 부족")
        if len(selected) >= 5:
            break
    if not selected:
        return "뚜렷한 수치 보정 없음"
    return ", ".join(_dedupe(selected))


def _dedupe(items):
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def _choice(value, allowed, default):
    value = str(value or "").upper()
    return value if value in allowed else default


def _score(value):
    try:
        return max(0, min(100, int(float(value))))
    except (TypeError, ValueError):
        return 50


def _float(value):
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _min_decision(left, right):
    left = _choice(left, DECISION_RANK, "OBSERVE")
    right = _choice(right, DECISION_RANK, "OBSERVE")
    return left if DECISION_RANK[left] <= DECISION_RANK[right] else right


def _max_risk(left, right):
    left = _choice(left, RISK_RANK, "MEDIUM")
    right = _choice(right, RISK_RANK, "MEDIUM")
    return left if RISK_RANK[left] >= RISK_RANK[right] else right


def _min_confidence(left, right):
    left = _choice(left, CONFIDENCE_RANK, "LOW")
    right = _choice(right, CONFIDENCE_RANK, "LOW")
    return left if CONFIDENCE_RANK[left] <= CONFIDENCE_RANK[right] else right
