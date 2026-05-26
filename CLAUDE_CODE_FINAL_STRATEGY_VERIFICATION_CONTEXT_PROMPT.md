# Claude Code 최종 전달 프롬프트 — 1d Horizon-First 전략 검증 + API/AI 후보선정 컨텍스트 레이어

> **사용법**  
> 이 파일 전체를 Claude Code 새 세션에 붙여넣는다.  
> Claude Code는 먼저 지정 문서를 `view`로 읽고, 현재 저장소 상태·테스트·디스크 상태를 확인한 뒤, **코드 변경 전 실행 계획을 운영자에게 보고하고 승인 받은 후에만 진행**한다.  
> 본 세션의 목적은 **실거래 코드 수정이 아니라 분석·검증 인프라 추가와 보고서 작성**이다.

---

## 0. 최종 목적

이 프로젝트의 최종 목적은 단순 리서치나 보고서 작성이 아니다.

**목표는 최소 1개 이상의 실거래 가능한 자동매매 전략을 확보하는 것이다.**

다만 지금까지 9개 전략군이 실패했거나 데이터 부족으로 검증되지 못했기 때문에, 더 이상 즉흥적인 전략 추가, 임계값 완화, 단기 신호 조합 사냥으로 가면 안 된다.

이번 세션의 목적은 다음 3가지를 동시에 달성하는 것이다.

1. **전략 코어 검증**  
   1d horizon 중심의 전략 후보들이 실제로 카테고리 수준의 알파를 갖는지 사전 검증한다.

2. **후보선정 컨텍스트 레이어 설계**  
   단순 시총·거래량·OI 순위가 아니라, Binance 데이터 + 외부 API + AI 리서치 위험 플래그를 이용해 “감시할 가치가 있는 후보군”을 만들 수 있는 구조를 설계한다.

3. **Single-Position Rotation 구조로 연결 가능한 결과물 작성**  
   최종적으로는 여러 코인을 동시에 난사하는 구조가 아니라, 후보를 계속 탐색하되 실제 보유는 항상 1개만 하는 선택과 집중형 자동매매 구조로 이어질 수 있어야 한다.

---

## 1. 핵심 철학

### 1-1. 기존 9번 실패에서 얻은 교훈

지금까지 실패한 핵심 원인은 “지표가 부족해서”가 아니다.

실패 원인은 다음에 가깝다.

- 너무 짧은 horizon에서 비용·노이즈·슬리피지·HFT 경쟁을 이기려 했다.
- OI 급등, funding, microstructure, ML 등 신호를 추가했지만 대부분 단기 예측형이었다.
- 표본 부족 상태에서 좋아 보이는 일부 결과를 살리려는 유혹이 있었다.
- 특정 종목 한두 개의 우연한 수익이 전략 전체 성과처럼 보였다.
- 후보 코인 선정 기준이 단순 거래대금, OI, 상승률, 시총 중심이었다.
- AI/GPT를 예측 주체처럼 쓰려는 위험이 있었다.

따라서 이번 세션에서는 다음 원칙을 따른다.

```text
전략 신호는 1d horizon 중심으로 검증한다.
외부 정보와 AI 리서치는 직접 진입 신호가 아니라 후보선정/위험 차단에만 사용한다.
최종 실행 구조는 단일 포지션 집중 구조로 설계한다.
실거래 코드는 절대 건드리지 않는다.
```

---

## 2. 기존 첨부 계획과의 관계

기존 첨부 파일 `VERIFICATION_PLAN_4_ENGINES.md`는 좋은 출발점이다. 특히 다음 내용은 그대로 채택한다.

- 4개 후보 엔진을 사전 검증한다.
- Gate 1: IC 분석
- Gate 2: Decile monotonicity
- Gate 3: Cost-aware simple backtest
- PASS 후보만 별도 R0 세션으로 넘긴다.
- 본 세션은 분석 전용이며 `trading/executor.py`, `main_7590.py`, `config/settings.py` 등 실거래 경로는 수정하지 않는다.
- 실거래 GO는 HOLD다.
- 보호종목은 진입 후보에서 제외한다.

다만 다음 부분은 반드시 수정한다.

### 수정 1 — 후보 1의 이름과 목적 변경

기존:

```text
1d 돌파 확장판
```

수정:

```text
Vol-targeted Long-or-Flat 1d TSMOM / Donchian Core
```

이유:

1d 돌파는 단순 돌파 신호가 아니라, **time-series momentum + Donchian breakout + ATR volatility targeting** 계열로 봐야 한다. 이번 검증의 1순위 후보는 이 전략 코어다.

### 수정 2 — Cross-Sectional Momentum의 역할 격하

기존 계획은 Cross-Sectional Momentum을 독립 엔진으로 본다.

수정 관점:

```text
Cross-Sectional Momentum은 독립 전략 후보로 검증하되,
우선순위는 1d TSMOM/Donchian보다 낮다.
검증 결과가 약하면 단독 엔진이 아니라 후보 랭킹 보조 피처로 격하한다.
```

### 수정 3 — Candidate Context Layer 추가

기존 계획에는 CoinMarketCap, CoinGecko, Upbit, 뉴스/공시, AI 리서치 기반 후보선정이 반영되어 있지 않다.

이번 세션에는 최소한 **설계와 스키마, mock 기반 테스트**까지 포함한다.

### 수정 4 — Supabase raw 저장 지양

기존 계획의 backfill/Supabase 적재는 디스크 한계와 충돌할 수 있다.

이번 세션 원칙:

```text
대용량 raw 또는 확장 backfill 기본 저장소 = 로컬 Parquet / DuckDB / SQLite
Supabase = 요약, backtest_runs, 상태, 보고서 인덱스 용도
```

1d OHLCV처럼 크기가 작은 데이터는 기존 loader가 Supabase만 지원한다면 임시로 읽을 수 있으나, 새로 대량 적재하는 방향은 피한다. 디스크가 부족하면 반드시 운영자에게 보고한다.

---

## 3. 반드시 먼저 읽을 문서

Claude Code는 코드 변경 전 아래 문서를 먼저 `view`한다.

1. `docs/HANDOFF.md`
2. `docs/STRATEGY_REALISM_REVIEW.md`
3. `CLAUDE.md`
4. `config/settings.py`
5. `data/persistence.py`
6. `backtesting/data_loader.py`
7. `backtesting/backtest_engine.py`
8. `VERIFICATION_PLAN_4_ENGINES.md` 또는 운영자가 제공한 동일 내용 문서
9. 현재 저장소에 존재한다면 `docs/AUTO_TRADING_SIGNAL_FACTORY_LOCAL_DATALAKE_SPEC.md`
10. 현재 저장소에 존재한다면 `docs/STRATEGY_VERIFICATION_REPORT.md`

읽은 뒤 아래를 요약해서 운영자에게 보고한다.

- 현재 HEAD
- 전체 테스트 개수
- 실거래 GO 상태
- 보호종목 목록
- 현재 전략 코어가 OI/AI 단기 구조인지 여부
- Supabase 디스크 상태
- 현재 OHLCV/Funding/OI 데이터 가용 범위
- 본 세션에서 건드릴 파일 / 절대 건드리지 않을 파일

---

## 4. 절대 금지 사항

아래는 어떤 경우에도 위반하지 않는다.

1. 실거래 주문 실행 금지
2. `trading/executor.py` 수정 금지
3. `main_7590.py` 수정 금지
4. `config/settings.py`의 보호종목·리스크 게이트 완화 금지
5. `RiskManager`, `KillSwitch`, `PairWhitelist`, `CapitalManager`, `executor` 완화 금지
6. API 키, 토큰, `.env` 출력 금지
7. `git add .` 또는 `git add -A` 금지
8. 실패한 전략의 임계값을 사후 완화하여 재시도 금지
9. AI 리서치 결과를 직접 매수/매도 신호로 사용 금지
10. 외부 웹페이지 무단 스크래핑 금지. 공식 API 또는 허용된 데이터 소스만 사용

---

## 5. 이번 세션의 최종 산출물

### 신규/수정 파일 후보

분석 전용 파일만 추가한다.

```text
analytics/verification/feature_engineering.py
analytics/verification/ic_analysis.py
analytics/verification/decile_analysis.py
analytics/verification/simple_backtest.py
analytics/verification/runner.py
analytics/verification/context_layer.py
analytics/verification/ai_research_schema.py
scripts/run_verification.py

tests/test_verification_features.py
tests/test_verification_ic.py
tests/test_verification_decile.py
tests/test_verification_backtest.py
tests/test_verification_context_layer.py
tests/test_ai_research_schema.py
tests/test_verification_runner.py

docs/STRATEGY_VERIFICATION_REPORT.md
docs/CANDIDATE_CONTEXT_LAYER_SPEC.md
docs/HANDOFF.md  # 결과 요약만 갱신
```

상황에 따라 로컬 저장 관련 유틸이 필요하면 다음을 추가할 수 있다.

```text
data/local_datalake.py
tests/test_local_datalake.py
```

단, 이번 세션의 핵심은 실거래 기능이 아니라 **검증과 후보선정 컨텍스트 설계**다.

---

## 6. 검증할 전략 후보

### 후보 1 — Vol-targeted Long-or-Flat 1d TSMOM / Donchian Core

#### 목적

가장 유망한 1순위 전략 후보를 검증한다.

#### 핵심 가설

```text
1d 기준의 time-series momentum / Donchian breakout은
5m~4h 단기 신호보다 비용과 노이즈 영향을 덜 받으며,
ATR 기반 변동성 조절을 결합하면 소액 자동매매의 첫 실전 후보가 될 수 있다.
```

#### 피처

- `momentum_28d`
- `momentum_28d_skip_3d` 또는 `momentum_30d_skip_7d`
- `donchian_breakout_20`
- `donchian_breakout_55`
- `donchian_position`
- `ema_200_distance`
- `adx_14`
- `atr_20_pct`
- `volume_ratio_20`

#### 기본 룰

```text
진입 후보:
close > donchian_high_20.shift(1)
AND close > ema_200
AND adx_14 > 25
AND BTC risk-off 비활성

포지션 방향:
LONG only 또는 Long-or-Flat

손절:
entry - 2 × ATR(14 또는 20)

익절:
TP1 +1.5R 50%
TP2 +3.0R 30%
나머지 Donchian low_10 trailing 또는 ATR trail

시간스톱:
30일 내 추세 미발생 시 종가 청산

사이징:
ATR volatility targeting 기반
```

#### 주의

BTCUSDT, ETHUSDT 등 보호종목은 진입 후보에서는 제외하되, BTC는 시장 인덱스 read-only로 사용 가능하다.

---

### 후보 2 — Cross-Sectional Momentum

#### 목적

암호화폐에서 횡단면 모멘텀이 독립 엔진으로 가치가 있는지 검증한다.

#### 보수적 관점

Cross-sectional momentum은 time-series momentum보다 근거가 약할 수 있으므로, FAIL 시 단독 전략에서 제외하고 후보 랭킹 보조 피처로 격하한다.

#### 피처

- `momentum_30d`
- `momentum_skip_30d`
- `volatility_30d`
- `risk_adjusted_momentum`

#### 전략

```text
매주 월요일 00:00 UTC 리밸런싱
상위 10% LONG
하위 10% SHORT
Market-neutral
```

#### 보수적 대안

Short 레그가 불안정하면 Long-only top decile도 별도 보조 분석하되, 사전 기준은 변경하지 않는다. 대안 분석은 보고서의 참고 섹션에만 둔다.

---

### 후보 3 — BTC-Beta Adjusted Trend

#### 목적

BTC가 강한 추세일 때 high-beta 알트가 outperform하는지 검증한다.

#### 피처

- `alt_beta_60d`
- `btc_above_ema200`
- `btc_adx_14`
- `btc_trend_strength`
- `btc_risk_off_state`

#### 전략

```text
BTC gate:
btc_close > btc_ema200
AND btc_adx_14 > 25
AND btc_trend_strength > 0

통과 시:
alt_beta_60d > 1.5 종목 중 top 10 LONG

미통과:
무거래
```

#### 주의

이 후보는 독립 전략이 아니라 후보선정 레짐 필터로도 가치가 있을 수 있다.

---

### 후보 4 — Funding Settlement Play

#### 목적

펀딩 정산 직전/직후의 포지션 정리 효과가 단기 알파로 존재하는지 확인한다.

#### 보수적 관점

과거 funding fade가 실패했기 때문에, 이 후보는 매우 엄격하게 본다. 단순 펀딩 역추세 재시도가 되면 안 된다.

#### 피처

- `funding_value`
- `funding_abs_z`
- `oi_change_1h`
- `minutes_to_settlement`

#### 전략

```text
trigger:
funding_abs_z > 2.0
AND 30 < minutes_to_settlement < 35

funding 양수:
SHORT 진입, +60분 후 청산

funding 음수:
LONG 진입, +60분 후 청산
```

#### 주의

데이터 부족 시 임계 완화 금지. 데이터 부족은 데이터 부족으로 보고한다.

---

## 7. Candidate Context Layer — 외부 API + AI 리서치 후보선정 구조

이번 세션에서 반드시 추가 설계한다.

### 7-1. 목적

후보 선정은 단순 시총 순위가 아니다.

목표는 다음이다.

```text
전체 Binance USDT-M universe
→ 거래 가능 필터
→ 시장/섹터/외부 관심도 필터
→ 전략 코어 후보
→ EV 랭킹
→ 최종 1개 WATCH 후보
```

즉 외부 API와 AI 리서치는 “매수 버튼”이 아니라 “후보 우선순위와 위험 차단”에만 사용한다.

---

### 7-2. Candidate Score 기본 구조

향후 실시간 후보선정 점수는 다음 구조를 따른다.

```text
Candidate Score
= 50% Binance 1d Strategy Score
+ 20% Liquidity / Tradability Score
+ 15% Sector / External Context Score
+ 10% Upbit / KRW Flow Score
+ 5% AI Research Context Score
- Risk Penalties
```

비중은 이번 세션에서 코드 상수로 확정하지 않아도 된다. 다만 문서와 mock 테스트에는 위 구조를 반영한다.

AI 리서치 가중치는 최대 5~10%를 넘기지 않는다.  
단, 악재는 가산점보다 강해야 한다.

```text
긍정 정보:
소폭 가산

부정 정보:
강한 감점 또는 hard block

불확실 정보:
보류 또는 confidence penalty
```

---

### 7-3. External Context 데이터 소스

#### CoinMarketCap / CoinGecko

사용 목적:

- 섹터/카테고리 분류
- 트렌딩 관심도
- 시총/거래대금
- 신규 상장 또는 급격한 관심 증가
- 카테고리별 강도

주의:

- 단독 매수 신호 금지
- API key는 `.env`에서만 로드
- 키가 없으면 mock client로 테스트

#### Upbit / KRW Flow

사용 목적:

- 한국 현물 거래대금 급증
- Upbit KRW 마켓 수급
- Upbit-Binance 가격 괴리
- KRW premium proxy
- 국내 관심도 변화

주의:

- Upbit Data Lab 화면 스크래핑 금지
- 공식 Open API 또는 허용된 데이터만 사용
- 자동매매 직접 신호가 아니라 후보 가중치

#### News / Official Event / AI Research

사용 목적:

- 해킹
- 상폐/거래지원 종료
- 규제/소송
- 토큰 언락
- 메인넷 장애
- 거래소 상장/상장폐지
- 공식 프로젝트 공지
- 거짓 루머/낚시성 뉴스 감지

주의:

- AI가 자유문장으로 판단하면 안 된다.
- 반드시 JSON schema로 구조화한다.
- 공식 출처 우선, 불명확한 소셜 루머는 confidence를 낮춘다.

---

### 7-4. AI Research JSON Schema

AI 리서치 출력은 반드시 고정 schema다.

```json
{
  "symbol": "FETUSDT",
  "asset": "FET",
  "timestamp_utc": "2026-05-26T00:00:00Z",
  "lookback_hours": 24,
  "context_score": 1,
  "risk_level": "LOW",
  "confidence": "MEDIUM",
  "source_count": 4,
  "official_source_count": 1,
  "staleness_minutes": 35,
  "event_types": ["sector_attention", "exchange_listing"],
  "positive_flags": ["ai_sector_strength"],
  "negative_flags": [],
  "hard_block": false,
  "hard_block_reason": "",
  "summary": "AI sector attention increased. No major negative official event detected."
}
```

#### 필드 규칙

- `context_score`: -2, -1, 0, +1, +2 중 하나
- `risk_level`: LOW, MEDIUM, HIGH, CRITICAL
- `confidence`: LOW, MEDIUM, HIGH
- `hard_block`: true이면 후보에서 제외
- `summary`: 사람이 읽는 요약. 봇의 결정 근거가 아니라 감사용

#### Hard Block 조건

아래 중 하나라도 확인되면 `hard_block=true` 가능.

- 공식 상장폐지/거래중단
- 해킹/익스플로잇
- 체인 중단
- 프로젝트 공식 보안 사고
- 거래소 지갑 입출금 장기 중단
- 법적/규제 이슈로 거래 리스크 급증
- 대규모 토큰 언락이 임박했고 유동성이 부족함

---

### 7-5. Context Layer는 이번 검증에서 어떻게 사용할 것인가

중요하다.

외부 API와 AI 리서치는 과거 4년 백테스트에 완전히 재현하기 어렵다. 따라서 이번 세션에서 다음처럼 분리한다.

#### 역사 검증에 사용 가능

- Binance OHLCV
- Funding
- OI
- BTC market regime
- 가능한 경우 CoinMarketCap/Coingecko historical category snapshot이 있으면 참고

#### 이번 세션에서는 설계/스키마/Mock까지만

- AI Research
- News context
- Upbit Data Lab 성격의 관심도 데이터
- 실시간 CMC trending

즉, 이번 세션은 아래를 수행한다.

```text
전략 코어 4개 = 과거 데이터로 검증
Context Layer = 향후 실시간 후보선정에 붙일 schema와 테스트 작성
```

Context Layer를 과거 백테스트에 억지로 끼워 넣지 않는다. 재현 불가능한 데이터를 사후 가정하면 데이터마이닝이다.

---

## 8. Single-Position Rotation 구조

이번 세션은 실거래를 구현하지 않지만, 검증 결과는 다음 구조로 이어질 수 있어야 한다.

```text
SCAN
→ RANK
→ WATCH
→ ENTERED
→ MANAGE
→ EXITED
→ RE-SCAN
```

### 운영 원칙

- 후보는 계속 탐색한다.
- 실제 보유는 항상 1개만 한다.
- 보유 중 신규 진입은 금지한다.
- 보유 중에도 다음 후보 랭킹은 계산할 수 있다.
- 청산 완료 후 반드시 후보 전체를 재평가한다.
- 이전 1위 후보가 아니라, 청산 시점의 새 1위 후보만 진입 대상이 된다.
- 동일 코인 연속 손실 시 12~24시간 cooldown을 둔다.
- BTC risk-off 중 신규 진입 금지다.

### 최종 선택 기준

```text
최고 점수 코인 X
최고 기대값(EV) 코인 O
```

EV는 다음을 포함해야 한다.

- 예상 보상
- 손절 거리
- ATR 변동성
- 수수료
- 슬리피지
- 체결 가능성
- 미체결 리스크
- 후보 컨텍스트 위험 플래그

---

## 9. 검증 방법 — 3-Gate 유지

첨부 계획의 3-Gate는 유지한다.

### Gate 1 — IC Analysis

- 피처 vs forward return Spearman IC
- horizon: 4h, 1d, 3d, 7d 중 후보별 적절히 사용
- 기준:
  - IC > 0.05: PASS
  - IC 0.02~0.05: BORDERLINE
  - IC < 0.02: FAIL

### Gate 2 — Decile Monotonicity

- 피처값 10등분
- top decile - bottom decile spread
- decile index vs mean return Spearman
- 기준:
  - spread > 0.5% AND monotonic corr > 0.5: PASS
  - 0.2~0.5% 또는 단조성 약함: BORDERLINE
  - 그 외: FAIL

### Gate 3 — Cost-Aware Simple Backtest

고정 비용 모델:

```text
maker entry: 0.018%
taker exit: 0.045%
slippage: 0.05%
round-trip: 0.113%
```

추가로 1d 후보에서는 maker/taker 체결 케이스를 보고서에서 분리한다.

```text
A. maker 체결 성공
B. maker 미체결 후 신호 무효
C. maker 미체결 후 taker fallback
D. maker 체결 후 adverse selection
```

기준:

- PF > 1.15
- n ≥ 100
- MDD ≤ 25%
- 평균 net trade > 0.2%
- 거래 종목 중 ≥ 40% net positive

단, 후보 1이 R0 후보가 되려면 별도 R0 세션에서 더 엄격한 기준을 적용한다.

---

## 10. 데이터 아키텍처

### 10-1. 로컬 우선

새 대용량 데이터는 로컬 우선이다.

권장 경로 예시:

```text
E:/bot_data/lake/ohlcv/
E:/bot_data/lake/funding/
E:/bot_data/lake/oi/
E:/bot_data/lake/context/
E:/bot_data/lake/reports/
```

파일 형식:

```text
Parquet for time-series
SQLite for setup registry / summary
DuckDB for analysis query
```

### 10-2. Supabase 역할

Supabase는 raw 저장소가 아니다.

허용:

- backtest_runs 요약
- 검증 결과 요약
- daily summary
- setup status
- report index

지양:

- raw trades 대량 저장
- raw depth 대량 저장
- 장기 microstructure raw 저장
- 대량 1h 이하 backfill

### 10-3. 점-인-타임 유니버스

가능한 경우 point-in-time universe를 사용한다.

불가능하면 차선책:

```text
검증 기간 연속 존재 종목만 사용
상장 후 90일 이전 데이터 제외
상폐/거래중단 구간 제외
보고서에 생존편향 한계 명시
```

---

## 11. 작업 단계

### 단계 A — 환경 점검 및 Plan 보고

1. 필수 문서 view
2. `pytest tests/ -q`
3. Supabase 디스크 상태 점검
4. 기존 데이터 범위 확인
5. 로컬 데이터 저장 경로 확인
6. 본 세션에서 생성/수정할 파일 목록 제시
7. 운영자 승인 대기

승인 전 코드 수정 금지.

---

### 단계 B — 데이터 로더/저장 구조 점검

1. 기존 `backtesting/data_loader.py` 사용 가능성 확인
2. Supabase 의존이 강하면 local parquet loader wrapper 설계
3. 1d OHLCV 50종목 × 4년 확보 가능성 확인
4. funding/OI는 후보 4/3에 필요한 범위만 확인
5. 데이터 부족 후보는 부족하다고 보고한다. 임계 완화 금지.

---

### 단계 C — Verification 모듈 작성

신규 모듈:

```text
analytics/verification/feature_engineering.py
analytics/verification/ic_analysis.py
analytics/verification/decile_analysis.py
analytics/verification/simple_backtest.py
analytics/verification/context_layer.py
analytics/verification/ai_research_schema.py
analytics/verification/runner.py
scripts/run_verification.py
```

단위 테스트:

```text
tests/test_verification_features.py
tests/test_verification_ic.py
tests/test_verification_decile.py
tests/test_verification_backtest.py
tests/test_verification_context_layer.py
tests/test_ai_research_schema.py
tests/test_verification_runner.py
```

필수 테스트:

- 룩어헤드 차단
- next-bar fill
- 비용 적용
- decile monotonicity 계산
- context score deterministic
- AI research JSON validation
- hard block 동작
- protected symbols 제외
- BTC read-only 허용
- Single-position ranking에서 1개만 선택되는지 mock 검증

---

### 단계 D — 검증 실행

실행 예시:

```bash
python scripts/run_verification.py \
  --candidates tsmom_donchian cross_sectional_mom btc_beta funding_settlement \
  --data-source local_or_supabase \
  --years 4 \
  --universe-size 50 \
  --exclude-protected
```

출력:

- 콘솔 요약
- `docs/STRATEGY_VERIFICATION_REPORT.md`
- `docs/CANDIDATE_CONTEXT_LAYER_SPEC.md`
- 가능 시 `backtest_runs` 요약 적재

---

### 단계 E — 보고 및 커밋

1. `pytest tests/ -q`
2. 보안 체크
3. 작업본에서 GitHub 클론으로 명시 복사
4. `git add <파일명 명시>`
5. 커밋
6. push
7. 운영자에게 결과 보고

절대 `git add .` 금지.

---

## 12. 보고서 형식

### `docs/STRATEGY_VERIFICATION_REPORT.md`

반드시 아래 섹션을 포함한다.

```markdown
# 전략 후보 검증 보고서

## 1. Executive Summary

| 후보 | Gate 1 IC | Gate 2 Decile | Gate 3 Backtest | 최종 판정 |
|---|---|---|---|---|

## 2. 데이터 범위

- 기간
- 종목 수
- 보호종목 제외 여부
- BTC read-only 사용 여부
- 점-인-타임 여부
- 생존편향 한계

## 3. 후보 1 — Vol-targeted 1d TSMOM / Donchian Core

### Gate 1
### Gate 2
### Gate 3
### Robustness
- ZEC 제외
- 상위 3종목 제거
- 연도별 성과
- 2022 약세장
- 단일 종목 기여도

## 4. 후보 2 — Cross-Sectional Momentum

동일 양식

## 5. 후보 3 — BTC Beta Trend

동일 양식

## 6. 후보 4 — Funding Settlement

동일 양식

## 7. Candidate Context Layer 적용 계획

- CMC/Coingecko
- Upbit/KRW flow
- AI research schema
- Risk hard block
- Score weight
- Backtest 불가 데이터의 한계

## 8. Single-Position Rotation 연결 방안

- 후보 랭킹
- 최종 1개 WATCH
- 진입 전 재평가
- 청산 후 재스캔

## 9. 최종 권고

- R0 진행 후보
- 폐기 후보
- Context Layer 구현 우선순위
- Micro-live 가능 여부
```

---

### `docs/CANDIDATE_CONTEXT_LAYER_SPEC.md`

반드시 아래 섹션 포함.

```markdown
# Candidate Context Layer Specification

## 목적

## 데이터 소스

## 후보 점수 구조

## AI Research JSON Schema

## Hard Block 규칙

## Upbit/KRW Flow 반영 방식

## CMC/Coingecko 섹터 반영 방식

## Single-Position Rotation과 연결

## 테스트 계획

## 한계
```

---

## 13. 최종 판정 규칙

후보별 판정:

```text
PASS:
Gate 1, 2, 3 모두 통과

CONDITIONAL:
2개 통과 + 1개 borderline

FAIL:
그 외
```

후보 1의 최종 R0 후보 조건은 더 엄격하게 본다.

```text
n ≥ 200
Net PF ≥ 1.25
OOS PF ≥ 1.0
MDD ≤ 25%
단일 종목 기여 < 25%
상위 3종목 제거 후 PF ≥ 1.0
ZEC 제외 후 PF ≥ 1.10
2022 약세장 PF ≥ 0.90
```

후보 1이 이 조건에 못 미치면, 실거래 후보로 승격하지 않는다.

---

## 14. AI 리서치 사용 원칙

AI 리서치는 다음에만 사용한다.

```text
후보선정 보조
위험 플래그
이벤트 요약
감사 로그
운영자 보고
```

AI 리서치는 다음에 사용 금지다.

```text
직접 LONG/SHORT 결정
포지션 사이즈 결정
손절/익절 자동 변경
보호종목 정책 우회
RiskManager 우회
```

AI가 낸 긍정 정보는 최대 소폭 가산이다.  
AI가 낸 부정 정보는 hard block이 될 수 있다.

---

## 15. Claude Code 시작 메시지

Claude Code는 이 프롬프트를 받은 뒤 다음을 수행한다.

1. §3 필수 문서 모두 view
2. 현재 테스트 실행
3. Supabase 디스크 상태 점검
4. 데이터 가용성 점검
5. 본 세션의 실행 plan을 운영자에게 보고
6. 운영자 승인 전 코드 수정 금지

운영자에게 보고할 때 반드시 아래를 포함한다.

```text
- 현재 HEAD
- 현재 테스트 결과
- 사용할 데이터 소스
- 생성/수정 예정 파일
- 실거래 경로 무수정 확인
- 보호종목 제외 방식
- Candidate Context Layer를 historical backtest에 직접 넣지 않는 이유
- AI 리서치를 매수 신호가 아니라 후보/위험 플래그로만 쓰는 방식
- 다음 단계 진입 전 승인 필요
```

---

## 16. 최종 한 줄 목표

이번 세션의 최종 목표는 다음이다.

```text
1d TSMOM/Donchian 계열이 첫 번째 실거래 후보가 될 수 있는지 검증하고,
동시에 CoinMarketCap/CoinGecko/Upbit/API/AI 리서치 정보를
후보선정 컨텍스트 레이어로 안전하게 결합할 수 있는 구조를 만든다.
단, 실거래 코드는 절대 수정하지 않는다.
```

---

## 끝

위 프롬프트를 기준으로 작업하라.  
Plan 승인 전 코드 변경 금지.  
실거래 GO는 계속 HOLD다.
