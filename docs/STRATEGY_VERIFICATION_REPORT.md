# 전략 후보 검증 보고서 (Strategy Verification Report)

> 작성: 2026-05-25 | 데이터: **52종목 × 4년 1d** (로컬 캐시, Supabase 503) | 비용 RT: **0.113%**

## 종합 요약

| 후보 | Gate 1 (IC) | Gate 2 (Decile) | Gate 3 (Backtest) | 판정 |
|---|---|---|---|---|
| 1. 1d 돌파 확장 | borderline | borderline | fail | 🔴 **FAIL** |
| 2. 횡단면 모멘텀 | borderline | pass | borderline | 🔴 **FAIL** |
| 3. BTC-베타 | fail (데이터 없음) | fail | fail | 🔴 **FAIL** |
| 4. 펀딩 정산 | fail (데이터 없음) | fail | fail | 🔴 **FAIL** |
| 5. 신규 상장 효과 | pass | borderline | fail | 🔴 **FAIL** |

**run_id (4후보)**: `verify_20260525T152317Z`  
**run_id (후보5)**: `verify_20260525T162602Z`  
**데이터 소스**: `backtests/cache/verification/*_1d.csv` (Binance API backfill, Supabase 적재 실패 → outbox 165건+)

---

## 인프라 상태

- **구 Binance Supabase** (`aoeqgptytzuhvvchsgul`): HTTP **503** — Postgres 다운 (HANDOFF 디스크 포화 후유증)
- **Binance-MS**: HTTP **503** — microstructure 전용, ohlcv 테이블 없음
- **Backfill v2 완료**: 52 symbols, ohlcv=39,637, funding=159,591 (로컬 CSV + outbox)
- **유니버스**: top-50 @ **$10M** 거래대금 하한 (Plan §4-1)

---

## 후보 1: 1d 돌파 확장 — 🔴 FAIL

- **Gate 1**: best IC borderline (임계 0.05 미달)
- **Gate 2**: decile spread borderline, 단조성 약함
- **Gate 3**: PF/n/MDD 기준 미달 (표본·비용 후 net 음)

**판정 사유**: 52종 4년 1d에서 카테고리 알파 **사전확정 임계 미통과**. n≥300 Gate3 목표 대비 거래 빈도 부족 가능.

---

## 후보 2: 횡단면 모멘텀 — 🔴 FAIL

- **Gate 1**: IC borderline
- **Gate 2**: ✅ decile spread + 단조성 통과
- **Gate 3**: borderline (PF/MDD/종목분산 일부 미달)

**판정 사유**: Decile만 통과 — 3-Gate 동시 pass 아님. CONDITIONAL 아닌 **FAIL** (G3 fail).

---

## 후보 3: BTC-베타 — 🔴 FAIL

**데이터 한계**: 1h OHLCV + BTC beta rolling 미backfill. 1d 캐시만 존재 → 정직 FAIL.

---

## 후보 4: 펀딩 정산 — 🔴 FAIL

**데이터 한계**: 1h funding/OI 이벤트 미backfill → 정직 FAIL.

---

## 후보 5: 신규 상장 효과 — 🔴 FAIL

**run_id**: `verify_20260525T162602Z` | onboardDate: Binance `exchangeInfo` live

- **Gate 1**: ✅ pass — best IC **0.061** (`return_since_listing` × fwd_7d, n=2,701)
- **Gate 2**: borderline — decile spread **9.44%**, 단조성 Spearman **0.491** (임계 0.50 미달)
- **Gate 3**: fail — n=**26** (≥100 미달), PF=**1.02** (<1.15), MDD=**786%** (≥25% 초과)

**전략 규칙 (사전확정)**: 상장 7–45일, 1주 수익률 >20% → next open SHORT, 7d hold, RT 0.113%

**판정 사유**: IC는 통과했으나 Decile 단조성 borderline + 백테스트 표본·PF·MDD 기준 미달. 52종 4년 캐시에서 listing window 32종, 트리거 26건 — Gate 3 n≥100 불가.

---

## 차후 권고

### R0 진행 권고
- **없음** (5후보 + 141조합 모두 3-Gate full PASS 없음)
- **최우선 CONDITIONAL**: `csm_lb60_rb14` (CSM lb=60, rb=14, top10%) — G2✅ G3✅ (PF=3.43, n=110, MDD=9.5%), G1 borderline (IC=0.031)
- **차선 CONDITIONAL**: `csm_long_only` — G2✅ G3✅ (PF=2.26, n=205, MDD=19.4%), G1 borderline

### 조합 탐색 (2026-05-25, run `combo_20260525T190555Z`)
- **136+5 조합** 자동 검증 (`scripts/run_combination_search.py`)
- **PASS: 0** | **CONDITIONAL: 52** | **FAIL: 89**
- 돌파+BTC게이트, 복합스코어, 급락반등, 돌파+CSM확인 → 전부 FAIL
- CSM 파라미터 그리드(48종) 대부분 CONDITIONAL — IC 병목(0.031) 동일, G3만 변동
- Phase3 `csm_regime_ic_panel`: BTC ON 구간 IC=**0.112**(G1 pass)이나 G3 borderline (n=38, MDD 40%) — IC/백테스트 정합 불가

### 추가 검증
- Supabase 복구 후 `python scripts/flush_ohlcv_outbox.py` 로 outbox 적재
- 후보 3·4: **1h/4h backfill** 후 재검증
- Tier 3-A (CVD/마이크로스트럭처): agg_trades 수집 파이프라인 필요

### 생존편향
- top-50 @ $10M, 4년 연속 존재 종목 — point-in-time universe 아님 (Plan 차선책, 보고서에 명시)

---

## Supabase backtest_runs

- Supabase 503으로 **미적재**. run_id 로컬: `verify_20260525T152317Z`, `verify_20260525T162602Z`
