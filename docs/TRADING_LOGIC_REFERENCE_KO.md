<!-- Public documentation copy; preserve the private local original. -->
> Publication scope: this reference describes the local working snapshot. Links point to the published committed subset and may show earlier logic or line numbers. Local-only research modules and existing uncommitted changes are not uploaded.

# 키움·Toss 공식, 가중치, 판단 로직 및 동작 원리

작성 기준: 2026-10-04의 로컬 소스 코드. 키움 Core Quant Lab과 Toss 런타임을 대상으로 한다.

**이 문서는 현재 구현을 설명하는 기술 문서이지, 검증된 수익 전략이나 매매 추천이 아니다.** API 키, 계좌, 보유자산, 실제 수익 내역은 포함하지 않았다. 코드나 환경 설정을 변경하면 수치도 달라질 수 있다.

## 1. 먼저 구분할 것

| 구분 | 의미 | 주의점 |
|---|---|---|
| 규칙 점수 | 개발자가 지정한 공식과 가중치로 계산한 0~100 점수 | 80점이 승률 80%라는 뜻은 아니다. |
| GPT 판단 | 입력 근거를 해석한 초기 점수, 설명, 위험 평가 | 모델 내부 가중치는 이 프로젝트에서 알 수 없다. |
| 학습 모델 확률 | 과거 표본으로 학습·보정한 비용 차감 수익 양수 확률 | 오프라인 연구용이며 자동 실전 적용하지 않는다. |
| 방향 평가 | 관심/회피 판단이 이후 가격 방향과 맞았는지 평가 | 회피 성공은 실제 공매도 수익이나 계좌 수익이 아니다. |
| 모의체결 수익 | 저장 가격과 체결·비용 가정으로 계산한 수익 | 실제 호가 대기열이나 주문 체결을 보장하지 않는다. |
| 계좌 수익률 | 현금·수량·포지션·비용·평가손익을 반영한 성과 | 신호 수익률 평균과 다르다. |

### 실행 상태에 관한 범위

- 아래의 '실행 경로'는 코드 호출 관계를 의미한다. 현재 PC에서 해당 프로세스가 실행 중이라는 뜻은 아니다.
- 문서는 작업 트리의 현 상태를 기준으로 한다. 직전 커밋은 키움 `84ee5a3`, Toss `41b9ad4`이지만, 기존 미커밋 변경이 남아 있어 **전체 문서가 두 커밋만의 상태를 설명하지는 않는다.**
- 주문 비활성 원칙을 유지한다. 문서 작성으로 설정·프로세스·DB를 변경하지 않았다. 모의/연구의 `orders_enabled=False`를 실주문 경로 전체에 대한 독립적인 운영 감사로 해석하면 안 된다.

## 2. 전체 흐름

```text
키움: 수집 데이터와 TR/시장 컨텍스트
   -> 기술 지표·이벤트·시장 regime
   -> signal_generator의 후보/회피 판단
   -> quant_signal_score의 방향별 점수와 가드
   -> 전문가 모델 비교 + trade_plan
   -> 모의체결/후속 가격 평가 -> 5일/20일 피드백

Toss: 시세·캔들·실제 거래 틱 + 공유 허브의 국내장 근거
   -> evidence + 기존 피드백 + 시장 관계 분석
   -> GPT의 구조화된 초기 판단
   -> decision_calibration의 수치 기반 보정
   -> trade_plan -> 모의체결/후속 평가 -> 피드백

별도 연구: 읽기 전용 DB
   -> 시점 일치 표본 / 데이터 품질 필터
   -> 진입 모델 학습·보정·정책 선택·미사용 기간 검증
   -> 시간별 매도정책 비교 / 진입·매도 결합 검증
   -> 연구 보고서 (운영 설정에 자동 승격하지 않음)
```

두 프로젝트가 공통으로 쓰는 것은 최신 `exit_research_core.py`와 `holding_exit_research.py`의 다중 보유시간 연구 틀이다. 키움의 전문가 점수와 Toss의 GPT 보정 전체가 동일한 모델로 통합된 것은 아니다.

## 3. 기호와 공통 성과 공식

`clip(x,a,b) = min(max(x,a),b)`. 점수의 기본 범위는 0~100이다. 수익률과 비용의 `pct`는 퍼센트 단위다. 예를 들어 `0.33`은 0.33%이지 33%가 아니다.

```text
P0 = 진입 기준 가격, P1 = 청산 가격, c = 왕복 비용(% 단위)
gross_return_pct = 100 * (P1/P0 - 1)
net_return_pct   = gross_return_pct - c      # 고정 비용 근사식

mean_net = sum(net_return_pct) / N
win_rate_pct = 100 * count(net_return_pct > 0) / N
profit_factor = sum(양수 net_return) / abs(sum(음수 net_return))
MFE_pct = 100 * (관찰 구간 최고가/P0 - 1)
MAE_pct = 100 * (관찰 구간 최저가/P0 - 1)
```

- `MAE`는 이 정의에서 보통 음수다. 최악의 역행폭을 양의 절댓값으로 표시하는 다른 지표와 혼동하지 않는다.
- 공통 매도 연구의 MFE/MAE는 모의 청산 가격을 관찰할 때까지의 경로다. 다른 피드백의 고정 60분 경로 지표와 구간이 다를 수 있다.
- 공통 엔진에서 손실 합계가 0이면 profit factor는 무한대 대신 `None`을 반환한다. 표본 0건의 평균·승률도 `None`이다.
- 방향 평가에는 관심 후보의 `r`, 회피 판단의 `-r`를 사용하고 비용을 차감할 수 있다. 이는 판단 품질용 가정이며, 회피가 실제 주문이나 현금 이익을 발생시켰다는 뜻이 아니다.
- 승률만 올리면 작은 이익·큰 손실 조합이 생길 수 있다. 평균 순수익, 평균 이익/손실, 최악 손실, 표본 수를 같이 본다.

## 4. 거래비용: 현재 코드 기본값

### 4.1 키움

```text
c = 매수 수수료 + 매도 수수료 + 매도세금 + 2*편도 슬리피지
  = 0.015 + 0.015 + 0.20 + 2*0.05 = 0.33%
```

근거: [키움 config.py](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/config.py#L127), [trade_plan.py](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/trade_plan.py#L24).

### 4.2 Toss

```text
c = 2*미국주식 수수료 + 2*편도 슬리피지 + 매도 규제비용 버퍼 + 왕복 환전 버퍼
  = 2*0.10 + 2*0.05 + 0.01 + 0.00 = 0.31%
```

환경변수로 개별 값 또는 `TOSSINVEST_PAPER_ROUND_TRIP_COST_PCT`를 덮어쓸 수 있다. 이 문서는 기본값을 설명하며 실제 실행 환경의 덮어쓰기 값을 조회하지 않았다.

근거: [Toss config.py](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/config.py#L102).

**둘 다 분석/모의검증의 비용 가정이다. 최신 공식 세율·수수료표를 확인했다는 뜻이 아니다.** 주문 규모별 면제, 실제 스프레드, 환전 조건, 시장 충격은 별도 검증 대상이다.

### 4.3 고정 비용 연구와 모의체결의 차이

공통 매도 연구는 `gross-c`로 단순 차감한다. 반면 `paper_execution.py`는 매수·매도 가격에 슬리피지를 적용하고 실제 비용 분모와 부분 청산 비중을 계산한다.

```text
매수 체결가 = 관찰 매수가 * (1 + 편도 슬리피지/100)
초기 원가   = 매수 체결가 * (1 + 매수 비용/100)
매도 체결가 = 관찰 매도가 * (1 - 편도 슬리피지/100)
순회수액    = sum(매도 체결가_j * 청산비중_j * (1 - 매도 비용/100))
순수익률    = 100 * (순회수액/초기 원가 - 1)
```

미청산 잔량이 있으면 마지막 관찰가에 청산 비용을 가정해 평가액에 더한다. 이는 실제 실현손익이 아니다. 서로 다른 비용 엔진의 수익률을 완전히 같은 값으로 간주하면 안 된다.

근거: [Toss paper_execution.py](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/paper_execution.py#L414), [키움 paper_execution.py](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/paper_execution.py#L516).

## 5. 키움의 점수 공식

현재 버전은 `quant_signal_score_v15_strategy_funnel`이다.

기호: `T` 추세, `B` 돌파, `F` 거래량·수급, `E` 기대가치, `M` 시장 상태, `R` 위험보상, `G` GPT 일치도, `RP` 위험 페널티, `CP` 비용 페널티. `MR`은 별도로 계산된 시장 위험 점수다.

### 5.1 기본 방향별 점수

```text
long_base = 0.22*T + 0.18*B + 0.16*F + 0.20*E
          + 0.10*M + 0.10*R + 0.04*G - RP - CP

caution_base = 0.30*MR + 0.20*(100-E) + 0.18*(100-T)
             + 0.12*(100-F) + 0.10*(100-M) + 0.05*(100-R)
             + 0.05*G + 0.40*RP

quant_signal_score = clip(0.35*T + 0.30*B + 0.20*F + 0.15*신호 confidence, 0, 100)
```

이 세 값은 서로 다른 목적이다. `quant_signal_score`를 모든 가드가 적용된 `final_quant_score`와 혼동하면 안 된다. 실제 방향별 점수에는 데이터 품질, 변동성, 과거 성과, adaptive 상태 등의 추가 보정과 상한이 적용된다.

`final_quant_score`는 관심 방향이면 보정된 `long_score`, 회피 방향이면 보정된 `caution_score`, 그 외에는 둘의 평균을 사용한다. 이후 강제 위험 가드가 다시 제한할 수 있다. **높은 caution 점수는 매수 매력도가 높다는 뜻이 아니다.**

근거: [quant_signal_score.py](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/quant_signal_score.py#L450), [최종 판단 순서](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/quant_signal_score.py#L133).

### 5.2 하위 점수의 핵심 계산

| 점수 | 시작값 및 핵심 계산 | 소스 |
|---|---|---|
| T | `25 + 18*상승 timeframes - 14*하락 timeframes`. MA5 위 +6, MA20 위 +7, VWAP 위 +8, 3개 이상 연속 상승봉 +8, 연속 하락봉 -12, 하락 timeframe 2개 이상이면 추가 -18. 마지막에 clip. | `_trend_score` |
| B | 35점 시작. 박스 상단 +14, 하단 +10, 거래량 급증 +18, VWAP 지지 +12, VWAP 저항 -10, 매수호가 불균형 +10, 매도호가 불균형 -15. 상단 접근인데 거래량 급증이 없으면 -18. 박스 중앙부면 -5. | `_breakout_score` |
| F | 45점 시작. 거래량 비율 v에 `min(max(v-1,0)*12,22)`. 매수호가 +12, 매도호가 -16. 외국인 양수 +10/그 외 -12, 프로그램 양수 +8/그 외 -10. 평균 지수 등락에 `clip(4*등락,-20,12)`. 외국인 매도압력 이벤트는 45점 상한 후 -18. | `_volume_flow_score` |
| E | `50 + 12*net10 + 18*net30 + 20*net60 + 10*(PF-1) + 0.15*(방향성공률-50)`. 없는 항목은 생략. 표본 5건 미만 -20, 10건 미만 -10. 부정 기대값 라벨이면 45점 상한. | `_expected_value_score` |
| R | 1차/2차 목표 수익을 65%/35%로 혼합해 손절폭으로 나눈 RR, 비용 대비 목표폭, 손실 대비 보상 조건을 점수화. 입력이 잘못되면 35점. 강제 위험 이벤트면 20점 상한. | `_risk_reward_score` |
| G | GPT 근거가 없으면 50. 방향 일치 +20, 불일치 -20. 위험 60 초과분에 -0.20, 이의 강도 60 초과분에 -0.10, confidence 50 초과분에 +0.10. 마지막에 clip. | `_gpt_agreement_score` |

`E`의 수익률 항목은 방향·비용 반영 지표를 우선 읽고, 없으면 방향 수익/순수익/원수익으로 순서대로 대체한다. 따라서 값의 **실제 metric 이름과 basis를 함께 확인**해야 한다. 고정 가중치는 학습 모델이 찾아낸 계수가 아니라 현재 규칙의 설정값이다.

근거: [추세·돌파·수급](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/quant_signal_score.py#L831), [기대가치](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/quant_signal_score.py#L922), [RR·GPT 일치도](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/quant_signal_score.py#L1071).

### 5.3 전문가별 가중치

현재 `quant_expert_ensemble_v7_staged_switch_shadow`. 각 행의 일곱 양의 가중치는 합계 1이다.

| 모델 | T | B | F | E | M | R | G | 후보 임계점수 | RP 계수 | CP 계수 | 품질 계수 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| momentum | .28 | .22 | .20 | .12 | .08 | .06 | .04 | 58 | .45 | 1.00 | .35 |
| reversal | .18 | .10 | .20 | .20 | .14 | .12 | .06 | 62 | .80 | 1.00 | .75 |
| range_rotation | .16 | .08 | .18 | .24 | .10 | .18 | .06 | 64 | 1.00 | 1.00 | 1.00 |
| flow_absorption | .24 | .12 | .24 | .16 | .08 | .12 | .04 | 52 | .65 | 1.00 | .55 |
| breakout_absorption | .22 | .25 | .24 | .12 | .09 | .06 | .02 | 55 | .55 | 1.00 | .60 |

```text
expert_raw = sum(가중치_i * 하위점수_i) - RP*RP계수 - CP*CP계수
expert = clip(expert_raw + regime보정 + 변동성보정 + 시장위험보정
              + 데이터품질보정 + 수급흡수보정, 0, 100)

시장위험보정 = -0.25*max(MR-55,0)
데이터품질보정 = -8*품질계수 (degraded/unavailable일 때; 그 외 0)
수급흡수보정 = +14 (흡수 모델이고 해당 컨텍스트 eligible일 때; 그 외 0)
```

모델별 regime·변동성 가감표는 `_model_adjustment`에 별도로 있다. 예: momentum은 momentum_participation +8, capital_preservation -25, volatility opportunity +8, trap -10. **임계점수 통과만으로 최종 후보가 되지 않는다.** hard risk와 모델 적격성 검사도 통과해야 한다.

- momentum 계열 regime은 momentum 모델, reversal_opportunity는 reversal, transition_wait는 range_rotation, 방어 regime은 event_defensive를 기본 선택한다.
- 강한 가격 흐름·전환 확인·위험 가드를 통과하면 momentum 선택을 우선할 수 있다.
- event_defensive는 회피/주의 품질을 측정하며 관심 후보를 내지 않는다.
- 모델 교체 후보는 단계 0 blocked, 1 monitor, 2 near_threshold_watch, 3 raw_long_confirmed, 4 shadow_switch_ready로 기록한다. 자동 실주문 교체는 아니다.
- 전문가 출력은 shadow 비교다. 다만 현재 `trade_plan`은 조건부로 전문가 점수와 권장 보유시간을 **모의 계획에 사용할 수 있다.** 모든 전문가 출력이 단순 표시용이라고 단정하면 안 된다.

근거: [가중치·선택](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/quant_expert_ensemble.py#L7), [점수 보정](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/quant_expert_ensemble.py#L511), [단계별 전환](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/quant_expert_ensemble.py#L328).

### 5.4 시장 regime는 차트만 읽지 않는다

`market_pressure_score = clip(sum(pressure_components), -100,100)`.

구성 요소는 지수, 선물, 벤치마크 ETF, 투자자 수급, 프로그램 거래, 환율, 미국 AI/반도체 proxy, 정책 기대, 거시 뉴스, 기술적 breadth다.

```text
지수 성분 = clip(평균 지수등락률*18, -35,35)
기술 breadth = clip(8*상승 timeframe - 8*하락 timeframe + 3*VWAP 위 timeframe, -30,30)
```

나머지 성분은 각각의 `_score_*` 함수에서 변환한다. 전체 요소에 일률적으로 같은 비율을 곱하는 하나의 단순 가중평균이 아니다. 최종 regime와 전환 여부에는 강제 위험, 뉴스 성격, 변동성, 가격 확인 같은 조건도 들어간다.

근거: [market_regime.py](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/market_regime.py#L638).

### 5.5 키움 진입 계획과 목표

기본 진입 점수 70 이상, 시장 위험 55 이하, 중간 이상 confidence, 올바른 가격 관계 `stop < entry < target1 <= target2`, 1차 순보상/위험 1.20 이상이 요구된다. 강제 위험, adaptive blocker, 전문가 NO_TRADE, 보고서 hard risk 등이 있으면 막는다.

```text
risk_pct = 100*(entry-stop)/entry
reward_j = 100*(target_j-entry)/entry
net_RR_j = max(reward_j-c,0)/(risk_pct+c)
blended_reward = (reward_1+reward_2)/2
```

실제 손절·목표 가격은 `signal_generator.py`가 만든 값을 받는다. 1차/2차 목표의 계획상 청산 비중은 각각 50%다. 점수 계산용 RR의 65%/35% 혼합과 다르다.

- 입력 가격의 기본 진입 구간은 +/-0.15%.
- TP1 후 손절 이동 기준은 `entry*(1+c/100)`.
- trailing 폭은 `clip((ATR_pct 또는 0.5)*0.75,0.35,1.50)`%.
- 기본 최대 보유는 60분. 선택 전문가의 유효한 권장 시간은 이를 덮어쓸 수 있다. momentum/breakout_absorption은 10분, reversal/range_rotation/flow_absorption은 30분을 기록한다.
- `execution_eligibility.status=PAPER_OBSERVE`이고 전문가 임계점수가 존재하면 진입 점수와 임계값도 선택 전문가 기준으로 바뀐다. 방향·시장위험·비용·adaptive 등의 가드는 남는다.
- 표본 20건 미만은 경고, 충분한 표본의 부진한 기대값/PF는 차단 조건이다. 조건을 통과하면 paper_candidate, 추가 adaptive 승격 조건까지 통과하면 shadow_candidate. 둘 다 실주문 허용이 아니다.

근거: [trade_plan.py](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/trade_plan.py#L32), [가격·RR 계산](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/trade_plan.py#L231).

## 6. Toss의 GPT 수치 보정

```text
calibrated_score = clip(round(GPT 초기 관심점수
                    + 기존 paper 피드백 변화량
                    + quant 피드백 변화량
                    + adaptive 변화량
                    + 캔들 변화량
                    + 공유 컨텍스트 변화량),0,100)
```

이것은 키움과 같은 일곱 하위 점수의 가중합이 아니다. GPT 점수는 초기값이고 숫자 근거가 덧셈 보정과 판단 상한을 적용한다.

### 6.1 주요 점수 변화

| 근거 | 점수 변화/주요 조건 |
|---|---|
| paper 표본 0 | 점수 변화 0, confidence 최대 MEDIUM |
| paper 표본 1~2 | -2, confidence 최대 MEDIUM |
| paper 최악 경로 | -3.0% 이하 -6, -1.5% 이하 -3, -0.7% 이하 -1; 구간별 배타 분기 |
| quant 라벨 | positive +6, negative -6, mixed -3, sample_too_small -2 |
| 추가 기대값 조건 | -0.25% 미만 -5, 그 외 -0.15% 미만 -3 |
| adverse path 비율 65% 이상 | -4, HIGH risk 하한 |
| 분봉 요약 change >=1% 및 volume_ratio >=1.5 | +6 |
| 분봉 요약 change <=-1% / 그 외 음수 | -8 / -3 |
| 일봉 요약 change >=2% 및 MA5-MA20 spread >=0 | +4 |
| 일봉 요약 change <=-2% 또는 spread <=-1% | -5 |
| RSI >=78 / RSI <=35이고 분봉 요약 change 음수 | -3 / -4 |
| VWAP 거리 <=-0.4% | -3 |
| 공유 데이터 missing/stale/partial | -4, confidence 최대 MEDIUM |
| 관계 근거 weak/insufficient 또는 품질 warning | -2, confidence 최대 MEDIUM |
| 관계 근거 strong이고 앞의 경고 분기 없음 | +2 |

표의 여러 항목은 서로 다른 함수에서 누적될 수 있다. 기존 paper 피드백의 저장된 `score_adjustment`도 반영되므로 이 표만으로 모든 입력 조합의 총점이 고정되지는 않는다.

**'분봉 요약 change'는 단일 직전 1분 수익률이라고 단정하면 안 된다.** 현재 `summarize_candles`는 조회한 캔들 묶음의 최신 종가와 가장 오래된 종가로 계산한다. 일봉 요약도 같은 원리다. 레이블 `1m`은 봉의 해상도이지 변화 측정 구간의 길이가 아니다.

근거: [decision_calibration.py](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/decision_calibration.py#L21), [summarize_candles](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/focused_analysis.py#L87).

### 6.1.1 캔들 요약 지표의 실제 정의

```text
change_pct = 100*(최신 종가/조회 묶음의 가장 오래된 종가 - 1)
volume_ratio = 최신 봉 거래량/조회 묶음 평균 거래량
MA5, MA20 = 각각 최신 5개/20개 종가의 단순 평균
ma5_vs_ma20_pct = 100*(MA5/MA20 - 1)
typical_price = (high+low+close)/3
VWAP = sum(typical_price*volume)/sum(volume)  # 유효한 가격·양수 거래량 쌍
RS = 최근 최대 14개 변화의 평균 상승폭/평균 하락폭
RSI = 100 - 100/(1+RS)
```

현재 Toss RSI는 단순 평균 방식이며 Wilder 지수 평활 방식이 아니다. 하락 평균이 0이면 100을 반환한다. 조회 캔들이 부족해도 계산 가능한 범위에서 값이 나올 수 있으므로 `sample`과 함께 확인한다. 조회 묶음의 VWAP를 하루 전체 VWAP와 동일시하지 않는다.

근거: [캔들 지표 구현](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/focused_analysis.py#L87), [VWAP·RSI](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/focused_analysis.py#L207).

### 6.2 점수 외에 라벨을 바꾸는 장치

- 판단 순위는 `AVOID < RISK < OBSERVE < HOLD < WATCH`. 각 보정 단계가 상한을 낮추고 위험 등급의 하한을 높일 수 있다.
- 최종 점수 35 미만이면 최대 RISK와 HIGH risk, 50 미만 WATCH는 OBSERVE로 바뀐다.
- 70 이상이고 risk가 HIGH가 아니며 confidence가 LOW가 아니면 OBSERVE/HOLD를 WATCH로 올릴 수 있다.
- GPT가 AVOID라고 했어도 숫자 위험이 심하지 않으면 OBSERVE 또는 RISK로 완화하는 분기가 있다.
- 마지막 승격/완화가 앞서 설정한 판단 상한에 영향을 줄 수 있다. 따라서 '상한은 항상 최종까지 불변'이라고 해석하지 말고 최종 라벨·점수·이유·trade_plan blocker를 함께 확인한다.
- WATCH는 관심 라벨이다. 진입 계획의 기준점수 75보다 낮은 WATCH도 나올 수 있으며 매수 주문 의미가 아니다.

### 6.3 Toss 진입과 가격 수준

기본 조건: 안전한 분석 데이터, 유효 가격, WATCH, 점수 75 이상, HIGH risk 아님, MEDIUM/HIGH confidence, 분봉 표본 30 이상, 과열 RSI 제외, 부정/mixed 피드백 및 adaptive blocker 없음, net RR 1.20 이상.

```text
trigger = max(현재가,VWAP)                # 유효 VWAP이 없으면 현재가
span_pct = 100*(조회 구간 고가-저가)/trigger
stop_pct = clip(0.35*span_pct,0.90,2.00)
target1_pct = max(1.50*stop_pct+c,c+0.75)
target2_pct = max(2.40*stop_pct+c,c+1.50)
stop_price = trigger*(1-stop_pct/100)
target_j_price = trigger*(1+target_j_pct/100)
net_RR_j = max(target_j_pct-c,0)/(stop_pct+c)
```

진입 구간 +/-0.15%, TP1/TP2 청산 계획 50%/50%, TP1 후 손익분기 이동 `trigger*(1+c/100)`, trailing `max(0.50,0.50*stop_pct)`%. 최대 보유 기본값은 60분이다. 환경변수로 기본 기준을 바꿀 수 있다.

근거: [Toss trade_plan.py](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/trade_plan.py#L24), [가격 수준](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/trade_plan.py#L175).

## 7. 매일의 피드백은 무엇을 바꾸나

5일/20일 윈도우를 비교해 참여 상태와 모의 위험 한도를 조절한다. **매일 새 최적 가중치를 실시간으로 학습해 자동 배포하는 시스템은 아니다.** 최신 근거 반영, 규칙 보정, 오프라인 모델 학습은 서로 다른 과정이다.

### 7.1 상태별 차이

| 상태 | Toss 점수 보정 | 키움 long 상한 / caution 하한 |
|---|---:|---|
| consistent_positive | +2 | baseline 변화 없음; 추가 승격 검증 필요 |
| short_positive_medium_negative | -6 | <=45 / >=65 |
| deteriorating | -10 | <=30 / >=80 |
| consistent_negative | -12 | <=20 / >=85 |
| recent_negative_medium_insufficient | -8 | <=30 / >=75 |
| mixed | -4 | <=45 / >=60 |
| insufficient | -2 | <=55 / 추가 하한 없음 |

Toss 기본 모의 risk_multiplier는 순서대로 1.00, 0.50, 0.25, 0.00, 0.25, 0.50, 0.50이다. 이는 주문 수량을 자동 배정한다는 뜻이 아니다. 계획에서 포지션 사이징은 비활성이다.

### 7.2 충분한 표본 기준도 다르다

- Toss adaptive: 단기 5건, 중기 20건 기본값. 양수 여부, PF, guidance 라벨을 확인한다. guidance 생성에서는 10분 클러스터 대표값과 방향·비용 반영 근거를 우선한다.
- 키움 adaptive v4: 단기 20건/3세션/3에피소드, 중기 60건/8세션/8에피소드 기본값. 순기대값 0.05%, PF 1.10, 양수 세션 비율 60%, 신뢰구간 하한 및 regime 검증 같은 추가 승격 검사를 포함한다. 단순 양수 평균만으로 통과하지 않는다.
- 표본 수, 독립 세션, 독립 에피소드는 다른 지표다. 같은 순간의 반복 신호가 100개 있어도 독립 거래 기회 100개가 아니다.
- 방어 상태에서도 증거 수집과 모의 연구를 계속할 수 있도록 flags를 남긴다. blocked 계획을 정상 계획처럼 강제로 체결하는 것과는 다르다.
- 손절·익절 기준을 단기 피드백 몇 건에 맞춰 자동 완화하지 않는다. 연구 결과의 운영 승격은 별도 절차다.

근거: [키움 adaptive_guard.py](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/adaptive_guard.py#L13), [키움 점수 제한](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/quant_signal_score.py#L392), [Toss adaptive_guard.py](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/adaptive_guard.py#L11), [Toss 피드백 guidance](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/quant_feedback.py#L349).

### 7.3 모의 주문 상태 머신

```text
planned -> rejected_by_guard                         # 후보/가격 가드 실패
planned -> waiting_entry -> canceled_no_fill          # 진입 구간에 가격 없음
planned -> waiting_entry -> canceled_gap_above_zone   # 구간 위로 갭 발생
planned -> entry_filled -> open -> partial            # TP1 50% 청산
                         -> closed_stop / closed_breakeven
                         -> closed_trailing / closed_tp2 / closed_timeout
```

`planned`와 `entry_filled`는 이벤트상의 상태이고, `waiting_entry`, `open`, `partial` 등은 결과의 상태로 기록된다. 체결 가격은 저장된 관찰 가격이며 실주문 API를 호출하지 않는다.

진입 대기 기본값은 15분이지만 최대 보유시간을 넘지 않도록 제한한다. 기존 paper 엔진의 `horizon_end`는 **계획 생성 시각 + 최대 보유시간**이다. 최신 공통 매도 연구의 시간 제한은 **첫 진입 틱 + 보유시간**이므로, 늦은 진입에서는 두 경로의 실제 보유 길이가 다르다.

키움에는 별도 `prepare_shadow_observation_plan`도 있다. 활성화·허용 종목·long 방향 등의 조건에 맞는 blocked 계획을 원본의 blocker를 보존한 복사본으로 만들어 관찰용 shadow replay에 사용할 수 있다. 이 결과는 정상 가드를 통과한 후보 성과와 구분해야 하며, 실주문 허용이나 원본 전략의 승격이 아니다.

Toss의 readiness 기본 검사는 종료 체결 100건, 최근 30건, 전체/최근 양수 순기대값, PF 1.20 이상, 평균 95% 신뢰구간 하한 양수, 경로 drawdown -10% 이상을 요구한다. 이는 모의검증 기준이며 실거래 수익 보증이 아니다. 단위 거래 수익으로 만든 경로 drawdown과 실제 계좌 원장의 drawdown을 동일시하지 않는다.

근거: [Toss 모의 상태 머신](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/paper_execution.py#L26), [readiness 검사](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/paper_execution.py#L264), [키움 별도 shadow 관찰](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/paper_execution.py#L28).

## 8. 공통 다중 보유시간·매도정책 연구

### 8.1 비교하는 20개 정책

시간은 5/10/15/30/60분, 각 시간마다 네 가지 정책을 평가한다.

| family | 손절 | 익절/청산 조건 | 시간 만료 |
|---|---|---|---|
| fixed_time | 없음 | 고정 시간까지 보유 | 해당 시간에 청산 조건 발생 |
| bracket | -0.6% | +0.8% | 동일 |
| time_stop | -0.6% | +0.8%; `min(5,보유시간/2)`분 이후 진척이 +0.10% 미만이면 조기 중단 | 동일 |
| trailing | -0.6% | 최대 상승폭 +0.8% 이후 고점 대비 -0.4%이면 청산 | 동일 |

이는 **연구 후보값**이다. 운영 trade_plan의 동적 손절폭과 동일한 값이 아니며, 이미 최적이라고 결정된 값도 아니다.

### 8.2 틱 경로로 청산하는 순서

1. 결정 시각 이후 유효한 첫 관찰 틱으로 진입을 가정한다. 첫 틱이 60초 넘게 늦으면 제외한다.
2. 시간·가격·정렬·거래 시각을 검사한다. 틱 간 공백이 60초를 초과하면 해당 시간대 평가에서 제외한다.
3. 청산 조건 우선순위는 시간 만료 -> 손절 -> 익절 -> time_stop -> trailing이다.
4. 조건을 만난 가격에서 즉시 체결하지 않는다. 기본 지연 1초가 지난 첫 관찰 틱으로 청산한다. 손절 가격보다 나쁘게 체결될 수 있다.
5. 필요한 후속 틱이 없으면 `incomplete_exit`. 수익률 0으로 임의 채우지 않는다.
6. 같은 보유시간의 네 정책은 모두 평가 가능한 공통 표본만 사용한다. 5분과 60분의 표본은 다를 수 있다.

특히 짧은 정책도 해당 보유시간의 fixed_time 경로가 완성돼야 비교에 포함된다. 조기 손절했지만 이후 데이터가 끊긴 사례를 비교 집합에서 제외하는 선택 효과가 있으므로, 제외 건수와 원인을 성과 옆에 확인해야 한다.

### 8.3 순차 검증과 회피

- 기본 과거 10개 세션을 학습/선택 구간으로 쓰고 다음 한 세션을 건너뛰어 평가한다. 테스트는 두 세션 간격으로 생성한다.
- 후보는 과거 구간 20건 이상, 평균 순수익 양수일 때만 적격이다. 그중 평균 순수익이 가장 높은 정책을 그 보유시간 안에서 선택한다.
- 과거 결과가 테스트 첫 진입 이전에 확정됐는지 확인한다.
- 적격 정책이 없으면 `abstain`. 거래 기회를 억지로 만들어내지 않는다.
- 전체 기간 descriptive 최고값을 실전 정책으로 채택하지 않는다. 테스트 결과로 전체 '최적 시간'을 사후 선택하지 않는다.

### 8.4 양쪽 데이터 어댑터

- 키움은 `signal_logs`와 `ticks`를 읽는다. Toss는 `paper_trade_candidates`를 묶은 결정과 `trade_ticks`를 읽는다.
- **현재 연구의 seed는 저장된 전체 결정이며, 통과된 진입 후보만을 자동 필터링하지 않는다.** 실제 전략 수익으로 해석하려면 진입 기준과 결합한 검증이 필요하다.
- 같은 종목·세션의 결정 간격은 최소 62분. 동시 전체 포트폴리오 노출을 제어하는 원장은 아니다.
- 키움은 KST 09:00~15:30, Toss는 뉴욕 09:30~16:00 정규장으로 제한한다. 뉴욕 DST를 반영한다. 이 연구 어댑터의 세션 필터는 주말/시각 중심이며 휴일·조기폐장 달력 전체를 검증하는 엔진은 아니다.
- DB의 timezone 없는 로컬 문자열은 KST로 해석하고 명시적 UTC로 변환한다.
- Toss는 `toss_trades` 실제 거래 source만 인정한다. 폴링 대체값, 결정 전 거래, 미래 거래시각, 관찰까지 60초 초과 지연 거래는 제외한다.
- 기본 최대 seed는 60개다. 최근 결과의 작은 표본은 전체 DB 데이터가 그만큼밖에 없다는 뜻이 아니다. `limit_reached`를 확인한다.
- DB는 `mode=ro`, `PRAGMA query_only=ON`. 연구 결과는 별도 출력 파일로 쓰며 live 설정은 바꾸지 않는다.

근거: [공통 매도 엔진](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/exit_research_core.py#L26), [시뮬레이션](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/exit_research_core.py#L91), [데이터 어댑터](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/holding_exit_research.py#L49). 키움에도 동일 파일이 있다.

## 9. 오프라인 학습 모델과 진입·매도 결합

### 9.1 시점 일치 에피소드

`episode_dataset.py`는 신호 시각과 점수 생성 시각이 같은 표본을 요구하고, feature의 원천 데이터 시간이 결정 시점 이후이면 제외한다. 실제 평가 진입 지연도 제한하며 같은 종목·날짜에서 기본 60분 라벨 길이와 허용 진입 지연 60초를 합한 61분 간격으로 분리한다. 미래 라벨 유무를 확인하기 전에 간격을 예약해, 결과가 존재하는 신호만 유리하게 골라내는 편향을 줄인다. 내용 해시로 데이터셋 무결성을 확인한다.

현재 원천 시각 검사에는 feature 안의 최신 시각을 사용한다. 모든 입력 피드가 같은 시점에 신선했다는 증명이나 과거 row가 절대 수정되지 않았다는 증명은 아니다.

이 에피소드 연구의 라벨 시간은 **5/10/30/60분**이다. 공통 매도 연구의 **5/10/15/30/60분**과 다르다. 아직 모든 연구의 시간 집합이 완전히 통일된 것은 아니다.

입력 특징은 `final_quant_score`, `expected_value_score`, `market_risk_score`, `long_score`, `caution_score`, `trend_score`, `volume_flow_score`다. 그 특징의 source 품질 한계는 학습 모델에도 그대로 전파된다.

### 9.2 비교 모델과 공식

| 모델 | 공식/구조 | 현재 설정 |
|---|---|---|
| cash | 항상 진입하지 않음 | 비용·거래 없음 비교 기준 |
| fixed_long65 | long_score >=65이면 후보 | 각 라벨 시간과 비교 |
| logistic | `p = 1/(1+exp(-(b+sum(beta_i*z_i))))` | C=0.1, max_iter=500, seed=17 |
| small_tree | 작은 histogram gradient boosting 분류기 | 60회, 최대 leaf 7, 최소 leaf 표본 15, L2=5, early_stopping=False, seed=17 |

결측은 0으로 대체하면서 결측 indicator를 추가하고 표준화한다. **logistic의 beta는 고정 전문가 가중치가 아니라 매 fold의 학습 결과**다. 트리 모델은 단일 가중치 표로 표현할 수 없다. 현재 보고서만으로 모든 학습 계수가 별도 저장된다고 가정하면 안 된다.

목표 라벨은 해당 시간의 `net_return > 0`. 모델 확률은 별도 calibration 구간에서 logit을 입력으로 한 logistic calibrator로 보정한다.

```text
raw_logit = log(p_raw/(1-p_raw))     # p_raw는 1e-6~1-1e-6으로 제한
p_calibrated = sigmoid(a + b*raw_logit)
Brier = mean((p_calibrated - 실제 0/1 라벨)^2)
```

학습/보정 구간에 두 클래스가 모두 없으면 그 horizon은 학습 불가로 기록한다.

### 9.3 시간 분리와 정책 선택

- 기본 fit 10세션 -> 별도 calibration 3세션 -> 별도 selection 3세션 -> 미사용 test.
- 내부 공백과 외부 purge/embargo를 둔다. 기본 첫 fold에는 전체 20세션이 필요하다. 최소 표본은 각 주요 구간 20건이다.
- selection 구간에서 확률 임계값 0.50/0.60/0.70/0.80과 5/10/30/60분을 비교한다.
- 충분한 표본 및 양수 평균 순수익 후보 중 평균이 높은 것을 선택한다. 동률이면 높은 확률 임계값, 짧은 시간을 우선한다.
- test로 임계값·시간·모델을 재선택하지 않는다. 평가가 좋아도 자동 승격이나 장중 재학습·배포는 없다.
- 비용 민감도 proxy는 기본 순수익에서 `0.5*c`를 추가 차감한다. 총 1.5배 비용의 근사 stress이며 체결 모델 전체를 다시 시뮬레이션한 것은 아니다.
- 보고서의 세션 평균 95% 하한은 `mean - 1.96*sqrt(sample_variance/session_count)`의 정규 근사다. 공식 배포 승인 기준이나 의존성을 완전히 반영한 신뢰구간이 아니다.

### 9.4 진입·매도 결합

`joint_entry_exit_research.py`는 데이터셋 해시, 코드 버전, 비용, 에피소드와 세션 정합성을 검사하고 진입 모델의 prior selection 결과와 틱 매도 결과를 결합한다. 진입을 안 한 수, 경로가 없는 수, 중첩 제한 등을 함께 기록한다.

기존 `tick_exit_research.py`를 결합하는 연구와 최신 공통 `holding_exit_research.py`는 별도 경로다. 최신 공통 보고서가 곧바로 모든 결합 연구의 입력으로 연결됐다고 단정하지 않는다.

근거: [episode_dataset.py](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/episode_dataset.py#L76), [학습·확률 보정](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/shadow_model_benchmark.py#L34), [정책 선택·시간 분리](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/shadow_model_benchmark.py#L92), [purged_walk_forward.py](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/purged_walk_forward.py#L7), [진입·매도 결합](https://github.com/lmhk0917-cloud/KiwoomAPI_GPT_personal_ver1/blob/agent/research-docs-20261004/joint_entry_exit_research.py#L12).

### 9.5 포트폴리오 원장·체결 stress와 장기 팩터

키움에는 별도 연구용 포트폴리오 원장도 있다. `shadow_portfolio.py`의 기본값은 초기 현금 1억원, 거래별 위험 예산 0.50%, 종목별 비중 20%, 총 노출 80%, 동시 포지션 4개다. 실제 계좌 금액이나 실주문 수량 설정이 아니라 연구 기본값이다.

```text
equity = 현금 + 미청산 포지션 원금 합계
risk_notional = equity*(위험예산_pct/100)/(손절폭_pct/100)
notional = min(현금,종목비중 한도,남은 총노출 한도,risk_notional)
shares = floor(notional/진입가)
청산 후 현금 증가 = 진입 원금*(1+effective_return_pct/100)
drawdown_pct = 100*(equity/과거 equity 최고값 - 1)
```

원장은 이미 종료된 paper 행에서 계좌 경로를 재구성한다. 미청산 자산은 원금으로 집계하므로 포지션 안의 실시간 평가손익을 완전히 반영한 MTM 원장은 아니다. 수량 계산에도 모의 단순화가 있다.

체결 stress는 다음처럼 추가 비용을 차감한다. 1bp는 0.01%다.

```text
추가 왕복 비용_pct = 2*편도 추가 슬리피지_bps/100
지연 페널티_pct = (지연_ms/1000)*불리한 이동_bps_per_second/100
stressed_return = 기존 net_return - 추가 왕복 비용 - 지연 페널티
effective_return = stressed_return*체결비율 (양수일 때)
                 = stressed_return          (0 이하일 때)
```

기본 moderate는 편도 +5bp/500ms/초당 1bp/체결비율 80%, severe는 +15bp/2000ms/초당 2bp/체결비율 50%다. 수익 거래는 일부를 놓치고 손실 거래는 전부 체결될 수 있게 하는 보수적 stress다. 실제 주문장 대기열이나 랜덤 체결을 재현하지는 않는다.

`asof_factor_context.py`는 결정 시각에 이용 가능하고 freshness가 유효한 팩터만 결합하는 별도 연구 경로다. 장기/거시 팩터를 모든 운영 모델의 가중치에 이미 통합했다는 뜻은 아니다. 원장·stress·팩터 연구 결과도 검증과 수동 검토 없이 live 설정에 자동 반영하지 않는다.

근거: `shadow_portfolio.py` (local-only research module), `execution_stress.py` (local-only research module), `asof_factor_context.py` (local-only research module).

## 10. 한국·미국 시장 관계 공식

`x`는 source/driver 수익률, `y`는 target/response 수익률이다. 데이터 payload의 `driver_return_pct`/`response_return_pct` 정의를 우선 읽고, 없으면 저장된 `source_return_pct`/`target_return_pct` 필드를 사용한다. 따라서 모든 행에서 x가 무조건 국내장이라는 가정은 금물이다.

```text
rho = sum((x-x_mean)*(y-y_mean))
      / sqrt(sum((x-x_mean)^2)*sum((y-y_mean)^2))
beta = sum((x-x_mean)*(y-y_mean)) / sum((x-x_mean)^2)
alpha = y_mean - beta*x_mean
r_squared = rho^2
예측 관계: y = alpha + beta*x
```

표본이 부족하거나 분산이 0이면 계산 불가로 표시한다. 관계 regime는 충분한 paired 표본에서 `abs(rho)>=0.65` strong, `abs(rho)<=0.25` weak, 그 사이 mixed다. 음의 강한 상관도 strong일 수 있으므로 부호를 따로 봐야 한다.

```text
hit_up = count(y>0 | x>=0) / count(x>=0)
hit_down = count(y<0 | x<=0) / count(x<=0)
hit_score = 평균(계산 가능한 hit_up, hit_down)
beta_reliability = min(abs(beta),1)*min(r_squared,1)
sample_score = min(N/max(min_samples,20),1)
lead_score = 100*(0.35*min(abs(rho),1) + 0.25*beta_reliability
                 + 0.25*hit_score + 0.15*sample_score)
```

`lead_score`는 규칙형 요약 점수다. 인과관계, 실제 선행성의 통계 검정, 수익률, 승률 확률을 뜻하지 않는다. 상승/하락 hit는 같은 방향의 비율이므로 역상관의 좋은 예측력을 그대로 표현하는 지표도 아니다. 현재 구현은 threshold=0에서 source 수익률 0인 행을 상승·하락 양쪽 조건에 포함한다. 이를 엄격한 양수/음수 표본만의 hit 비율로 해석하면 안 된다.

- rolling 창의 `3m/6m`은 여기서는 **3개월/6개월**이며 분 단위가 아니다. 92/183/366/1098/3660일의 달력 창을 사용한다.
- paired 표본이 있는 correlation 근거와 현재 방향의 proxy alignment를 구분한다. proxy를 correlation으로 부르지 않는다.
- 일봉 장기자료와 실시간 틱·분봉의 해상도는 다르다. 일봉 상관이 장중 5분 진입의 수익성을 입증하지 않는다.
- 강한 상관에도 시차, 휴장, regime, 표본 구성, 환율이 영향을 줄 수 있다. time alignment와 실제 데이터 quality warning을 확인한다.

근거: [관계 분석](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/relationship_analysis.py#L226), [rolling 창](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/relationship_analysis.py#L309), [회귀·방향 통계](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/relationship_analysis.py#L490), [lead_score](https://github.com/lmhk0917-cloud/toss_trading_runtime/blob/agent/research-docs-20261004/relationship_analysis.py#L588).

## 11. 숫자로 이해하는 예시

다음은 실제 종목/성과가 아닌 설명용 가상 입력이다.

```text
T=70, B=60, F=65, E=55, M=60, R=75, G=50, RP=4, CP=2
long_base = 15.4+10.8+10.4+11+6+7.5+2-4-2 = 57.1
```

57.1은 기본 가중합일 뿐이다. adaptive가 insufficient이면 long_score 상한 55가 적용될 수 있고, 이후 방향이나 위험 override도 바뀔 수 있다. 57.1% 성공 확률이라는 뜻은 아니다.

```text
진입 100, 청산 102, 비용 가정 0.33%
gross = 2.00%, 고정 비용 연구 net = 1.67%
```

실제 계좌에서 몇 주를 체결했는지, 동시 포지션과 잔여 현금은 얼마인지가 없으므로 이 한 표본을 계좌 수익률 1.67%라고 보고할 수 없다.

## 12. 실제 결과를 읽는 확인 순서

1. 분석 시각, 가격 수신시각, source, stale/quality, 해상도를 확인한다.
2. 후보 방향과 점수 이름을 확인한다. long/caution/시장위험을 섞어 읽지 않는다.
3. 원점수 -> 보정 -> 가드 -> 최종 판단을 따라가고 blocker와 warning을 구분한다.
4. trade_plan의 `entry_score_source`, `holding_horizon_source`, 순 RR, 비용 가정을 확인한다.
5. 모의체결에서 진입 유무, entry/exit 시각, 부분 청산, 종료 이유, 잔량을 확인한다.
6. 성과가 원수익/방향 수익/비용 반영/모의체결/계좌 수익 중 무엇인지 확인한다.
7. 총 표본·독립 에피소드·독립 세션·누락 경로·제외 사유·평가 기간을 같이 본다.
8. 학습·정책 선택·미사용 테스트 구간이 분리됐는지 확인한 뒤 여러 regime와 비용 stress에서 재검증한다.

## 13. 이 문서의 한계와 갱신 방법

- 핵심 계산과 의사결정 경로를 설명한다. 수천 줄의 이벤트 예외 분기를 모두 재서술하지 않는다. 세부 조건의 최종 기준은 링크한 소스다.
- 표의 기본 수치는 소스에서 확인한 값이며, 환경변수·호출 시 settings·데이터 필드의 fallback 때문에 개별 실행 결과와 달라질 수 있다.
- 키움 v4 adaptive와 Toss v1 adaptive 등 아직 양쪽의 구현 깊이가 다르다. 같은 용어만으로 같은 검증 강도를 가정하면 안 된다.
- 공식 변경은 변경 사유, formula_version, 비용 가정, 데이터 해시, 학습/선택/테스트 범위, 이전 기준 대비 결과를 함께 남긴다. 테스트가 통과했다는 것과 수익성이 입증됐다는 것은 다르다.
- 문서에는 실제 백테스트 성과나 민감 자료를 복제하지 않았다. 이 문서를 읽기 위해 대용량 DB를 열 필요가 없다.
