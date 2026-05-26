# Claude Code Phase 1 마스터 프롬프트 v2 (FINAL) — 1d 돌파 확장 검증

> **버전**: v2.0 (2026-05-26) — v1 + 3가지 보완점 반영
> **사용법**: 이 파일 전체를 복사해서 Claude Code 새 세션에 붙여넣기.
> Claude Code가 명세서 view → plan 보여주고 운영자 승인 → 코드 → pytest 순서로 진행.
> **본 세션은 *Phase 1만* 수행한다.** Phase 1.5 ~ Phase 4 는 별도 세션.
>
> **본 프롬프트의 정당성**: **5명의 독립 외부 평가자** 가 *수렴* 한 결론
> = "Horizon-First + 1d 돌파 우선 + 외장 SSD + Microstructure는 execution filter".
> 9번의 전략 실패 + 검증 인프라 자산 + 학계 메타-분석 모두 반영.
>
> **v2 변경 사항** (v1 대비):
> 1. **Bailey-Lopez de Prado Deflated Sharpe Ratio (DSR ≥ 0.95)** 사전확정 추가
> 2. **arXiv 2502.18625 "The Market Maker's Dilemma" (Feb 2025)** maker 비용 실증 증거 명시
> 3. **Phase 1 FAIL 시 Stage 3 하드 룰** — 자동매매 archetype 재고 옵션 명시
> 4. **Han·Kang·Ryu TSMOM (28d × 5d)** 사이드 검증 추가 (선택)

---

## 0. 컨텍스트 인계 (먼저 읽고 이해할 것 — 누락 금지)

### 0-1. 프로젝트 상태
- **작업본**: `C:\Users\jaeho\OneDrive\Desktop\Cursor\BINANCE_FUTURES_BOT`
- **git 클론**: `C:\Users\jaeho\OneDrive\Desktop\Cursor\BINANCE_FUTURES_BOT_GITHUB` (origin: kong9365/BINANCE_FUTURES_BOT)
- **수집 클론**: `C:\bots\BINANCE_FUTURES_BOT` (기존, OI 수집 데몬용)
- **GitHub HEAD**: 018b109 이후 (HANDOFF 참조)
- **전체 테스트**: 367+ passed
- **실거래 GO**: **HOLD** (Phase 1 PASS 전까지 유지)
- **운영 환경**: 집 PC 단일 (회사 PC 미사용)

### 0-2. 먼저 읽어야 할 문서 (`view` 필수, 순서 엄수)
1. `docs/HANDOFF.md` — 9개 전략 실패 이력 (특히 1d 돌파 OOS PF 1.63 / n=89)
2. `docs/STRATEGY_REALISM_REVIEW.md` — 각 전략 실패 사유
3. `CLAUDE.md` — TIER 1 절대 룰
4. `config/settings.py` — 보호종목, 게이트 설정
5. `data/persistence.py` — Supabase 어댑터 (이번에 비활성화 대상)
6. `backtesting/backtest_engine.py` — 기존 `strategy="breakout"` 구현 (재사용)
7. `backtesting/data_loader.py` — OI+OHLCV 로더 (재사용)
8. `backtesting/collect_oi.py` — 수집 CLI (참조)
9. `backtesting/backfill_history.py` — 다년 backfill (있다면, 재사용/확장)
10. `backtesting/evaluate.py` — 사전확정 평가 helpers
11. `backtesting/universe.py` — top-N 종목 선정

### 0-3. 9개 실패 전략 (절대 재시도 금지)
1. OI-급증 모멘텀 — PF 0.91 (최선), 64조합 전부 음수
2. **돌파 1h** — NET NEGATIVE (PF 0.95) ← *시간축 짧음이 원인*
3. **돌파 1d** — PF 1.39 (OOS 1.63), n=89 ← **이번 검증 대상**
4. 펀딩 페이드 — 빈도 부족
5. ORB 인트라데이 — MDD 76%, Sharpe -3.88
6. CSM 스윙 — LAB 단일 종목 46% 집중
7. LCR 이벤트 (마이크로구조) — borderline FAIL (+4h 평균 +0.387%)
8. CCS-Lite — R0 FAIL
9. ML EV (LightGBM) — Spearman 0.0004 (random)

→ 본 세션은 #3 (1d 돌파) 의 *표본 확장 + 강건성 + 다중검정 보정* 만 수행. 다른 전략 재시도 금지.

### 0-4. 외부 평가 5차 수렴 결론 (변경 금지)
| 평가 | 점수 | 핵심 |
|---|---|---|
| 1차 | 7.5/10 | OOS>IS 과장 정정, ZEC 집중 우려 |
| 2차 (자료) | 6.5/10 | 인프라 좋음, 5분 알파 위험 |
| 3차 | 8.5/10 | 메이커 4-case 분해 |
| 4차 | 8.8/10 | 상장일 필터, 연도별 PF 현실화, Phase 1.5 추가 |
| **5차 (deep research)** | **8.5/10** | **실무 구현 강함, 운영 자동화 강함** |
| **6차 (내 학계 리서치)** | **8.5/10** | **DSR, arXiv 메이커 증거, Han·Kang·Ryu** |

→ **6명 독립 평가자 *수렴* = "1d horizon 우선 + Microstructure는 execution filter"**.

### 0-5. 운영자 결정사항 (절대 변경 금지)
| 항목 | 결정 |
|---|---|
| 목표 수익 | 월 5~15% (공격) |
| MDD 허용 | 30% |
| 자산 범위 | USDT-M perp 중심 |
| 운영 방식 | 완전 자동 24/7 |
| 빈도 | 스윙 (1~10/week) |
| 메커니즘 | 포트폴리오 (여러 엔진) |
| 초기 자본 | $1,000 (검증 후 확장) |
| **데이터 저장** | **외장 SSD 1TB (Samsung T7 Shield 권장)** |
| **Supabase** | **연결 비활성화 (코드는 유지)** |
| **Local Data Lake** | **Phase 3에서 raw microstructure만 도입** |
| **운영 PC** | **집 PC 단일 환경** |

### 0-6. **v2 핵심 학계 근거** (변경 금지, 인용 시 정확히)

#### 근거 1 — Bailey & Lopez de Prado (2014)
**"The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality"**
- Journal of Portfolio Management 40(5), pp. 94-107
- SSRN 2460551
- **인용 (정확)**: *"Backtest optimizers search for combinations of parameters that maximize the simulated historical performance of a strategy, leading to backtest overfitting... Not controlling for the number of trials involved in a particular discovery leads to over-optimistic performance expectations."*
- **운영자 봇 적용**: 9개 전략 × 평균 10 파라미터 = effective N ~50~90. DSR 보정 후 통계적 유의성 확인 필수.

#### 근거 2 — arXiv 2502.18625 (Feb 27, 2025)
**"The Market Maker's Dilemma: Navigating the Fill Probability vs. Post-Fill Returns Trade-Off"** (Albers et al.)
- **데이터**: Binance BTCUSDT 무기한, 232,897건 실제 maker 주문 실험 (Feb 12-19, 2024)
- **결정적 결과**:
  - Top-of-book maker post 기대수익: **-0.8 bp gross / -0.3 bp net of rebate**
  - Naive market-making 전략: 3일 7시간만에 **-60% 손실, Sharpe -109.0**
  - Front-of-queue 1초 markout: **-0.058 bp** vs back-of-queue **-0.775 bp**
  - *"Orders submitted when the near-side queue is large and the opposite-side queue is small have a low fill probability, as low as 30%."*
  - *"If the next price move is against a maker order in the top-of-book queue, that order automatically fills, with probability 1."*
- **운영자 봇 적용**: 메이커 비용 1/3 가정은 *실증적으로 약화*. 메이커는 *alpha 원천이 아닌 cost 절감 도구* 로만 가치.

#### 근거 3 — Han, Kang, Ryu (Dec 26, 2023)
**SSRN 4675565 - "Cryptocurrency Time-Series and Cross-Sectional Momentum Strategies"**
- **결정적 결과**:
  - (28-day lookback × 5-day holding) long-only TSMOM: **Sharpe 1.51 vs 시장 0.84**
  - 28-day가 grid search 최적치 (자기 caveat: "optimistic view")
  - *"Evidence of time-series momentum is strong, whereas evidence of cross-sectional momentum is weak."*
  - *"Selling the market when market falls yields negative profits in most cases"* → **Long-or-Flat 구조 권장**
- **운영자 봇 적용**: 1d Donchian 돌파의 alpha 원천 검증. Phase 1 본 검증 외 *사이드 비교 검증* 가능.

---

## 1. 본 세션 목적 (단 하나)

**1d Donchian/ATR 돌파 전략의 *확장 검증* + *다중검정 보정* — 50종목 × 4~5년 + 상장일 필터 + 강건성 분해 + DSR 보정 → 사전확정 통과 시 Setup Registry에 등록.**

본 세션 결과물:
1. 외장 SSD 인프라 셋업
2. Supabase 1d 데이터 → 외장 SSD Parquet 이전
3. Binance API로 50종목 4~5년 추가 backfill
4. 1d 돌파 walk-forward + 강건성 분해 검증
5. **DSR (Deflated Sharpe Ratio) 계산 및 사전확정 통과 여부 판정**
6. **메이커 4-case 분석 + arXiv 2502.18625 caveat 명시**
7. (선택) Han·Kang·Ryu TSMOM (28d × 5d) *사이드 비교* 검증
8. Setup Registry 등록 (PASS/FAIL 무관 — 기록 의무)
9. 검증 보고서 `docs/PHASE1_VERIFICATION_REPORT.md`
10. HANDOFF.md Phase 1 결과 갱신
11. GitHub push (A안)

본 세션 *금지*:
- 실거래 주문 (HOLD 유지)
- main_7590.py 라이브 로직 수정
- 다른 전략 (Phase 2~4) 작업
- 9개 실패 전략 재시도
- 사전확정 임계 *완화*
- DSR 계산 *생략*

---

## 2. 절대 룰 (TIER 1, 위반 시 즉시 작업 중단)

1. **시크릿/토큰/API 키 출력 금지** (코드·로그·메시지·보고서 일체)
2. **보호종목 거래 금지**: BTCUSDT, ETHUSDT, HOLOUSDT, CFXUSDT, LYNUSDT, INJUSDT
   - 단, BTC/ETH는 *시장 인덱스 read-only*로 데이터 활용 허용
   - **진입 후보에서는 6종목 모두 제외**
3. **실거래 GO = HOLD** (본 세션 주문 0건, dry-run도 안 함)
4. **게이트 수정 금지**: RiskManager, CostGuard, PairWhitelist, CapitalManager
5. **라이브 코드 수정 금지**: `main_7590.py`, `trading/executor.py`, `config/settings.py`(보호종목 등)
6. **운영자 승인 게이트** (강제):
   - 단계 A 끝 → 운영자 plan 승인 후 단계 B
   - 단계 B 끝 → 외장 SSD 셋업 확인 후 단계 C
   - 단계 C 끝 → backfill 완료 보고 후 단계 D
   - 단계 D 끝 → 보고서 + 판정 보고 후 단계 E
   - **승인 없이 다음 단계 진행 절대 금지**
7. **사전확정 임계 변경 금지** — 검증 *후* 임계 완화는 다중검정 함정 (Bailey·Lopez de Prado 2014)
8. **9개 실패 전략 재시도 금지** — 본 세션은 1d 돌파만
9. **DSR 계산 *반드시* 수행** — 생략 시 Phase 1 미완료 처리

---

## 3. 외장 SSD 인프라 (Phase 1의 핵심 추가)

### 3-1. 운영자 사전 작업 (본 세션 시작 전)
- Samsung T7 Shield 1TB (또는 동급 USB 3.2 NVMe 외장 SSD) 준비
- USB-C/USB 3.2 포트에 연결
- **드라이브 문자 확인** (예: E: 또는 D:)
- 본 세션 시작 시 Claude Code에게 알려줄 것

### 3-2. 환경변수 (`.env` 추가)
```bash
BOT_DATA_DIR=E:/bot_data
SUPABASE_ENABLED=false
```

### 3-3. 폴더 구조 (자동 생성)
```
E:/bot_data/
├── candles/                  # 봉 데이터
│   ├── ohlcv_1d.parquet
│   ├── ohlcv_1h.parquet      # Phase 2~3에서 사용
│   ├── funding.parquet
│   ├── market_global.parquet
│   └── universe_meta.parquet # 상장일 메타
│
├── state/                    # 운영 상태 (SQLite)
│   ├── bot.sqlite
│   ├── setup_registry.sqlite # 신규 — 본 세션
│   ├── micro_live.sqlite     # Phase 1.5 이후
│   └── backtest_runs.sqlite
│
├── lake/                     # Phase 3 이후
│   ├── raw/
│   ├── features/
│   └── catalog/
│
├── logs/
│   ├── bot/
│   ├── collector/
│   └── reports/
│
└── backups/
    └── YYYY-MM-DD/
```

### 3-4. 백업 자동화
- 매일 03:00 UTC: state/, candles/ → 내장 SSD `C:/bot_backup/`
- 매주 일요일 04:00 UTC: 전체
- 매월 1일: 운영자 수동 클라우드 권장

---

## 4. Supabase 마이그레이션

### 4-1. 가벼운 데이터 export
- Supabase MCP `execute_sql` 또는 운영자 직접:
```sql
COPY (SELECT * FROM ohlcv WHERE interval = '1d') TO STDOUT WITH CSV HEADER;
COPY (SELECT * FROM funding_history) TO STDOUT WITH CSV HEADER;
COPY (SELECT * FROM trades) TO STDOUT WITH CSV HEADER;
COPY (SELECT * FROM backtest_runs) TO STDOUT WITH CSV HEADER;
COPY (SELECT * FROM market_global) TO STDOUT WITH CSV HEADER;
```

### 4-2. CSV → Parquet/SQLite 변환
- `scripts/migrate_supabase_to_local.py` 신규
- 무결성 검증: row count + min/max timestamp

### 4-3. Supabase 비활성화 (코드 유지)
- `data/persistence.py`: `SUPABASE_ENABLED=false` 시 *로컬만*
- 코드 삭제 금지 (향후 복귀)

### 4-4. Supabase 무거운 데이터 *삭제* (운영자 안내)
```sql
DROP TABLE IF EXISTS agg_trades;
DROP TABLE IF EXISTS l2_book_snapshots;
```

---

## 5. 1d 돌파 확장 Backfill

### 5-1. 종목 선정 (50 후보)
1. Binance USDT-M perp 거래대금 현재 top-60
2. 보호종목 6 제외
3. 50종목 추출

### 5-2. Point-in-Time + 상장일 필터 (외부 평가 4차 핵심)

**절대 룰**:
1. **상장일 이전 데이터 사용 금지** (Binance API `/fapi/v1/exchangeInfo` 의 `onboardDate`)
2. **상장 후 90일 경과 후부터 백테스트 포함**
3. **거래 중단/상폐 구간 제외**
4. **연도별 universe 스냅샷 기록**

**구현 차선책**: 완벽한 point-in-time 어려우면, 종목별 onboardDate 메타 + backtest 시 t 시점 활성 종목만 사용 + 한계 명시.

### 5-3. Backfill 실행

**기간**: 4~5년 (2021-01-01 ~ 2026-05-25)

**대상 데이터**:
- 1d OHLCV (50종목)
- Funding history (50종목)
- 1h OHLCV (Phase 2에서 필요 시, 본 세션 *제외*)

**저장**: `E:/bot_data/candles/`

**구현**: `backtesting/backfill_history.py` 확장
- `--symbols-top-n 50`
- `--years 5`
- `--include-onboard-filter`
- `--exclude-protected`
- `--output-dir E:/bot_data/candles`
- `--format parquet`

---

## 6. 1d 돌파 검증

### 6-1. 전략 룰 (재사용 — 변경 금지)

**기존 `strategy/breakout.py` 의 1d 로직 그대로**:
- 진입: `close > donchian_high_20.shift(1)` AND `close > ema_200` AND `adx_14 > 25`
- SL: 진입가 − 2 × ATR(14, 1d)
- TP1: +1.5R (50% 청산)
- TP2: +3R (30% 청산)
- 트레일: Donchian low_10 (20% 청산)
- 시간 스톱: 30일

### 6-2. 비용 모델 — Maker/Taker 4-case 분해 (**v2 강화**)

#### 4-case 정의 (변경 금지)

```python
CASE_A_MAKER_FILLED = {
    "entry_fee_pct": 0.018,   # 메이커 진입
    "exit_fee_pct": 0.045,    # 테이커 청산
    "slip_entry_pct": 0.0,    # 메이커 슬립 없음
    "slip_exit_pct": 0.05,
    # ⚠️ arXiv 2502.18625 (Feb 2025) 실증 데이터:
    # Front-of-queue 1초 markout: -0.058 bp (best case)
    # Back-of-queue 1초 markout: -0.775 bp (worst case)
    # → 실거래 시 case A는 *낙관* 추정. paper-trade에서 실측 보정 필수.
}

CASE_B_MAKER_UNFILLED_NO_FALLBACK = {
    # 미체결 → 다음 1h 재시도, 6h 후 스킵
    # 거래 없음, missed trade 카운트
    # ⚠️ arXiv 2502.18625: 호가 큐 유리 위치(near-side small, opposite-side large) 
    #    fill probability 30~90% 변동 → case B 가정 합리적
}

CASE_C_MAKER_UNFILLED_TAKER_FALLBACK = {
    "entry_fee_pct": 0.045,
    "exit_fee_pct": 0.045,
    "slip_entry_pct": 0.10,   # 추격 슬립
    "slip_exit_pct": 0.05,
}

CASE_D_MAKER_FILLED_ADVERSE_SELECTION = {
    "entry_fee_pct": 0.018,
    "exit_fee_pct": 0.045,
    "slip_entry_pct": 0.0,
    "adverse_selection_penalty_pct": 0.20,  # 체결 직후 역행
    # ⚠️ arXiv 2502.18625 결정적 인용:
    # "If the next price move is against a maker order in the top-of-book queue,
    #  that order automatically fills, with probability 1."
    # = case D는 *확률 1*. 이론적 가능성 아닌 *기본 가정*.
}

# 백테스트 기본 case: CASE_A (낙관) + CASE_C (보수) 두 가지 모두 측정 의무
# 단일 PF 보고 X — case 분포 + 각 case별 PF 분해 보고
# **v2 추가**: 보고서에 arXiv 2502.18625 경고 명시 의무
```

#### v2 추가 요구사항

1. **Case A 결과 발표 시 *반드시 caveat 동반*** (arXiv 2502.18625 인용):
   > "메이커 1/3 비용 가정은 232,897건 실증 데이터(arXiv 2502.18625)에서 -0.3 bp net of rebate로 약화. Adverse selection으로 인해 favorable position일수록 fill 안 됨."

2. **Case A vs Case C 비교** 시 *Case C 기준으로 사전확정 평가*. Case A는 *상한 추정*.

3. **Phase 1.5 paper-trade 의무화** 명시:
   - 실제 메이커 체결률
   - Adverse selection 실측
   - Missed trade 비율
   - → 이 측정 후만 Phase 2 진입

### 6-3. Walk-Forward 설정
- **IS**: 2021-01-01 ~ 2024-12-31 (4년)
- **OOS**: 2025-01-01 ~ 2026-05-25 (16~17개월)
- **약세장 OOS 분리**: 2022-05-01 ~ 2023-01-31 (LUNA, FTX)
- 룩어헤드 5단계 차단 (기존 의무 룰)

### 6-4. **사전확정 11-체크리스트 (v2 강화, 변경 금지)**

```
✓ 1.  n ≥ 200                                    (표본 충분)
✓ 2.  Net PF ≥ 1.30                              (v1: 1.25 → v2: 1.30 강화)
✓ 3.  OOS PF ≥ 1.0                               (홀드아웃)
✓ 4.  MDD ≤ 25%                                  (운영자 30% 안)
✓ 5.  단일 종목 기여 < 25%                       (다양화)
✓ 6.  ZEC 제외 PF ≥ 1.10~1.15                    (단일 종목 의존)
✓ 7.  상위 3종목 제거 PF ≥ 1.0                   (강건성)
✓ 8.  2022 약세장 PF ≥ 0.90                      (자본 보존)
✓ 9.  연도별 PF — 1개 연도 0.85까지 허용,         (추세추종 현실)
       나머지 ≥ 1.0
✓ 10. Maker/Taker 4-case 분석 + arXiv caveat     (실증 증거 명시)
✓ 11. **DSR (Deflated Sharpe Ratio) ≥ 0.95**     (v2 신규)
       Bailey & Lopez de Prado (2014) JPM 40(5)
       — 9개 시도 + 본 시도의 multiple testing 보정 필수
```

### 6-5. **v2 신규: Deflated Sharpe Ratio (DSR) 계산** (반드시 수행)

#### 공식 (Bailey & Lopez de Prado 2014)

```
DSR(SR*, T, N, γ3, γ4) = Z[(SR* − E[max(SR)]) × √(T-1) / √(1 − γ3·SR* + (γ4-1)/4 · SR*²)]

where:
- SR* = 관측된 Sharpe (annualized)
- T = 표본 trade 수
- N = effective number of trials (다중검정 횟수)
- γ3 = trade return skewness
- γ4 = trade return kurtosis
- E[max(SR)] = SR null distribution 최대값 기댓값
- Z = standard normal CDF
```

#### Effective N 추정 (운영자 봇 환경)

```
N = 운영자 9개 strategy × strategy당 평균 10 파라미터 조합 + 1d 돌파 본 검증
  = 90 + 1 ≈ 91

⚠️ 다중검정 보호: N=91이면 raw Sharpe 1.6 결과조차 DSR 보정 시 통계 유의성 한계.
   PF 1.39, OOS 1.63도 DSR 검증 의무.
```

#### 구현

- `analytics/dsr_calculator.py` 신규 모듈
  - `compute_deflated_sharpe(returns, n_trials) -> float`
  - 입력: trade return 시계열, effective N
  - 출력: DSR (0~1, > 0.95 = 통계적 유의)
- `tests/test_dsr_calculator.py` — Bailey-Lopez de Prado 논문 예제로 검증

#### DSR 평가 기준

| DSR | 해석 | 판정 |
|---|---|---|
| ≥ 0.95 | 95% 신뢰도로 통계 유의 | ✅ 통과 |
| [0.80, 0.95) | borderline | 🟡 (전체 11-체크리스트 종합 판정) |
| < 0.80 | 통계 유의 부족 | ❌ FAIL (다른 체크리스트 모두 통과해도 FAIL) |

#### Python 의존성

```python
# requirements.txt 확인
scipy >= 1.10  # 정규분포 CDF, skewness, kurtosis
numpy >= 1.24
```

### 6-6. 종합 판정 (v2)

- 🟢 **PASS**: 1~11번 모두 통과 (Case A caveat 명시 의무)
- 🟡 **CONDITIONAL**: 1~11번 중 1개 borderline → 운영자 결정
- 🔴 **FAIL**: 2개 이상 미달 → 정직 종료

**FAIL 시 절대 룰** (v2 강화):
- 임계 *완화* 금지 (Bailey-Lopez de Prado 함정)
- 다른 시간축 (4h, 12h) 시도 금지
- "1d 돌파도 운영자 봇 환경에서 작동하지 않음" 정직 결론
- **운영자에게 다음 옵션 권고** (v2 신규):
  - 옵션 A: Manual semi-discretionary 트레이딩 전환
  - 옵션 B: Buy-and-hold + 50d MA cash exit (Grayscale 2023 모델)
  - 옵션 C: 운영자 가설 청취 (혹시 모를 영역)
  - **자동매매 archetype 재고 권장** — 10번째 실패 방지

### 6-7. **v2 신규 (선택): Han·Kang·Ryu TSMOM (28d × 5d) 사이드 검증**

운영자가 시간 여유 있고 동의 시:
- 학계 검증 최적 파라미터 (Sharpe 1.51 보고)
- 1d 돌파와 *나란히* 비교
- TSMOM이 더 강하면 → Phase 2 엔진 우선순위 재정렬
- **사전확정 동일 적용** (PF ≥ 1.30, DSR ≥ 0.95)

구현:
- `strategy/tsmom.py` (long-or-flat, 28d lookback, 5d hold)
- ATR(20) vol-targeting overlay (Kim-Tse-Wald 권고)
- 별도 setup_id `tsmom_28d_5d_long_v1`

→ 단계 D 내 *선택 작업*. 운영자 요청 시만.

---

## 7. Setup Registry (신규 SQLite 테이블)

### 7-1. 스키마 (`E:/bot_data/state/setup_registry.sqlite`)

```sql
CREATE TABLE setup_registry (
    setup_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,                -- CANDIDATE / PAPER_ONLY / MICRO_LIVE / PROMOTED / COOLING / DISABLED / ARCHIVED
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    params_hash TEXT NOT NULL,
    params_json TEXT NOT NULL,
    last_evaluation_ts TIMESTAMP,
    last_metric_pf REAL,
    last_metric_n INTEGER,
    last_metric_mdd_pct REAL,
    last_metric_dsr REAL,                -- v2 신규
    reason TEXT,
    notes TEXT
);

CREATE TABLE setup_evaluations (
    eval_id TEXT PRIMARY KEY,
    setup_id TEXT NOT NULL,
    eval_ts TIMESTAMP NOT NULL,
    eval_type TEXT NOT NULL,
    data_range_start TIMESTAMP,
    data_range_end TIMESTAMP,
    n_trades INTEGER,
    pf REAL,
    sharpe REAL,
    dsr REAL,                            -- v2 신규
    n_trials_assumed INTEGER,            -- v2 신규 (DSR 계산 시 사용한 N)
    mdd_pct REAL,
    win_rate REAL,
    avg_trade_net_pct REAL,
    single_symbol_max_pct REAL,
    yearly_pf_json TEXT,
    case_distribution_json TEXT,         -- v2 신규 (Maker/Taker 4-case 분포)
    metric_full_json TEXT,
    verdict TEXT,                        -- PASS / CONDITIONAL / FAIL
    FOREIGN KEY (setup_id) REFERENCES setup_registry(setup_id)
);

CREATE INDEX idx_eval_setup_ts ON setup_evaluations(setup_id, eval_ts DESC);
```

### 7-2. 본 세션 등록

```python
setup_id = "1d_breakout_long_v1"
params = {
    "interval": "1d",
    "donchian_period": 20,
    "ema_period": 200,
    "adx_period": 14,
    "adx_threshold": 25,
    "atr_period": 14,
    "atr_sl_multiplier": 2.0,
    "tp1_r": 1.5,
    "tp1_portion": 0.5,
    "tp2_r": 3.0,
    "tp2_portion": 0.3,
    "trail_period": 10,
    "time_stop_days": 30,
    "side": "LONG",
}
params_hash = sha256(json.dumps(params, sort_keys=True))

# 본 세션 후 상태:
# - PASS  → status="PAPER_ONLY"
# - CONDITIONAL → status="CANDIDATE"
# - FAIL  → status="DISABLED"
```

### 7-3. 다중검정 보호 (v2 강화)
- 임계를 *조금 바꿔* 재시도 시 → 새 hash → 새 setup_id 자동
- `setup_evaluations.n_trials_assumed` 매번 +1 누적
- DSR 보정 시 *cumulative N 사용*
- 본 세션 *임계 절대 변경 금지* — 단 1회 검증

---

## 8. 작업 단계 (순서 엄수, 각 단계 운영자 승인 게이트)

### 단계 A — 환경 점검 + 운영자 협의 (30분)
1. 위 §0-2 문서 모두 `view`
2. `pytest tests/ -q` → 367+ passed 확인
3. **운영자 확인 사항**:
   - 외장 SSD 드라이브 문자 (E:?, D:?)
   - 모델 (Samsung T7 Shield 또는 다른 모델)
   - 여유 공간
4. Supabase MCP `execute_sql` 로 1d ohlcv 현재 행 수 확인
5. `backtesting/backfill_history.py` 존재 확인
6. **scipy 의존성 확인** (DSR 계산용, requirements.txt에 있는지)
7. 단계 B~E plan 운영자에게 보고 + **승인 받은 후에만** 단계 B 진행
8. 운영자 응답 대기

### 단계 B — 외장 SSD 인프라 + Supabase 마이그레이션 (1~2시간)
1. `.env` 에 `BOT_DATA_DIR`, `SUPABASE_ENABLED=false` 추가
2. `E:/bot_data/` 폴더 구조 자동 생성
3. `scripts/setup_external_ssd.py` 신규 작성
4. `scripts/migrate_supabase_to_local.py` 신규 작성
5. `data/persistence.py` 수정 (`SUPABASE_ENABLED` 인식)
6. 단위테스트
7. `pytest tests/ -q` → 회귀 0
8. 운영자 승인 → 단계 C

### 단계 C — 50종목 4~5년 Backfill (2~4시간)
1. `backtesting/backfill_history.py` 확장
2. `data/universe_meta.py` 신규
3. Backfill 실행 (1d ohlcv + funding)
4. `universe_meta.parquet` 저장
5. DuckDB 적재 검증
6. 단위테스트
7. 운영자 승인 → 단계 D

### 단계 D — 1d 돌파 검증 + Setup Registry 등록 (2~4시간, v2 확장)
1. `analytics/phase1_verification/` 폴더 신규
   - `feature_engineering.py`
   - `verifier.py`
   - `cost_model_4case.py`
   - `robustness_analysis.py`
   - **`dsr_calculator.py` (v2 신규)** — Bailey-Lopez de Prado 2014
   - `report_generator.py`
2. `analytics/setup_registry.py` 신규
3. `scripts/run_phase1_verification.py` CLI
4. 검증 실행:
   - 기존 `BacktestEngine(strategy="breakout", interval="1d")` 사용
   - Walk-forward (IS 4년 / OOS 1.4년)
   - 약세장 OOS 별도
   - 종목별/연도별/ZEC 제외/상위 3종목 제거 분해
   - 4-case 비용 분석 + **arXiv 2502.18625 caveat 명시**
   - **DSR 계산 (N=91)** ← v2 핵심
   - 11-체크리스트 평가 (v2 강화)
5. **(선택) Han·Kang·Ryu TSMOM 사이드 검증** (운영자 요청 시)
6. Setup Registry 등록
7. `docs/PHASE1_VERIFICATION_REPORT.md` 생성 (§9 양식)
8. 단위테스트:
   - `tests/test_dsr_calculator.py` (v2 신규)
   - 외 기존 테스트들
9. `pytest tests/ -q` → 회귀 0
10. 운영자 승인 → 단계 E

### 단계 E — 보고서 + HANDOFF 갱신 + GitHub 반영 (1~2시간)
1. `docs/PHASE1_VERIFICATION_REPORT.md` 최종 검토
2. `docs/HANDOFF.md` 갱신:
   - Phase 1 결과
   - 외장 SSD 인프라
   - DSR 결과
   - 다음 세션 권고
3. GitHub 반영 (A안)
4. 운영자 최종 보고:
   - 판정 (PASS/CONDITIONAL/FAIL)
   - DSR 결과
   - 다음 세션 권고

---

## 9. 출력 양식 — `docs/PHASE1_VERIFICATION_REPORT.md` (v2)

```markdown
# Phase 1 검증 보고서 — 1d Donchian/ATR Breakout (Long) v2

> 작성: YYYY-MM-DD | 데이터: 50종목 × 4~5년 (보호종목 제외, 상장일 필터)
> Setup ID: 1d_breakout_long_v1 | params hash: <sha256...>
> **v2 적용 학계 근거**:
> - Bailey & Lopez de Prado (2014) JPM 40(5) — DSR 다중검정 보정
> - arXiv 2502.18625 (Feb 2025) — Maker 232,897건 실증 데이터
> - Han·Kang·Ryu (2023) SSRN 4675565 — TSMOM 학계 검증 (사이드 비교)

## 종합 판정: 🟢 PASS / 🟡 CONDITIONAL / 🔴 FAIL

## 1. 데이터 요약
- 종목 수 (활성): XX종목
- 기간: YYYY-MM-DD ~ YYYY-MM-DD
- 총 1d 봉 수: XXX,XXX

## 2. 사전확정 11-체크리스트 (v2)

| # | 기준 | 결과 | 판정 |
|---|---|---|---|
| 1 | n ≥ 200 | n = XXX | ✓ / ✗ |
| 2 | Net PF ≥ 1.30 | PF = X.XX | ✓ / ✗ |
| 3 | OOS PF ≥ 1.0 | OOS PF = X.XX | ✓ / ✗ |
| 4 | MDD ≤ 25% | MDD = XX.X% | ✓ / ✗ |
| 5 | 단일 종목 < 25% | Max = XX.X% (SYMBOL) | ✓ / ✗ |
| 6 | ZEC 제외 PF ≥ 1.10 | PF = X.XX | ✓ / ✗ |
| 7 | 상위 3종목 제거 PF ≥ 1.0 | PF = X.XX | ✓ / ✗ |
| 8 | 2022 약세장 PF ≥ 0.90 | PF = X.XX | ✓ / ✗ |
| 9 | 연도별 PF (1개 ≥ 0.85) | 2022:X.XX 2023:X.XX ... | ✓ / ✗ |
| 10 | Maker/Taker 4-case + caveat | (§5 참조) | ✓ (보고만) |
| 11 | **DSR ≥ 0.95** | DSR = X.XXX (N=91) | ✓ / ✗ |

## 3. 전체 성과
- 거래 횟수: XXX
- 승률: XX.X%
- Profit Factor: X.XX
- Sharpe (annualized): X.XX
- **Deflated Sharpe Ratio: X.XXX (Bailey-Lopez de Prado 2014)**
  - effective N (multiple testing trials): 91
  - skewness: X.XX, kurtosis: X.XX
- Max DD: XX.X%
- 평균 net trade: X.XX%

## 4. 종목별 기여 분해
| Rank | Symbol | n | PF | Contribution % |
|---|---|---|---|---|

## 5. 4-case Maker/Taker 분석 (v2)
- Case A (Maker filled, 낙관): XX% — PF X.XX
- Case B (Maker unfilled, no fallback): XX% — missed trades XXX
- Case C (Maker unfilled, taker fallback, 보수): XX% — PF X.XX
- Case D (Maker filled, adverse selection): XX% — PF X.XX

**⚠️ arXiv 2502.18625 (Feb 2025) 경고**:
> "232,897건 실제 Binance BTCUSDT 무기한 maker 주문 실험에서 top-of-book maker 
> 기대수익 -0.8 bp gross / -0.3 bp net of rebate. Naive market-making 전략은 
> 3일 7시간만에 -60% 손실 (Sharpe -109.0).
> Adverse selection 본질: favorable position일수록 fill 안 됨."

→ **Case A는 *상한 추정*. 사전확정 평가는 Case C 기준.**
→ Phase 1.5 paper-trade에서 메이커 체결률 실측 의무.

## 6. 연도별 성과
| Year | n | PF | MDD | Win Rate |

## 7. 약세장 OOS (2022-05 ~ 2023-01)

## 8. 강건성 분해 (Leave-one-out)
- ZEC 제외 PF
- 상위 3종목 제거 PF
- 상위 5종목 제거 PF (참고)

## 9. **DSR 분석 (v2 신규)**

### Bailey-Lopez de Prado Deflated Sharpe Ratio
- 관측 Sharpe: X.XX (annualized)
- effective N: 91 (운영자 9 strategy × 평균 10 파라미터 + 1d 본 검증)
- E[max(SR)]: X.XX (null distribution)
- DSR: **X.XXX** (0~1 scale)

### 해석
- DSR ≥ 0.95: 95% 신뢰도로 통계 유의 → 통과
- DSR < 0.95: 다중검정 보정 시 *통계 유의 부족* → 우연 가능성 ≥ 5%

### 의미
> "Backtest optimizers search for combinations of parameters that maximize 
> the simulated historical performance... Not controlling for the number 
> of trials involved in a particular discovery leads to over-optimistic 
> performance expectations." (Bailey & Lopez de Prado 2014)

## 10. 한계 및 주의
- 생존편향 보정 정도
- 메이커 비용 모델: arXiv 2502.18625 실증 약화
- OOS 기간 시장 상황
- DSR effective N 추정의 보수성

## 11. 다음 단계 권고

### PASS 시
- Phase 1.5 진입 (Micro-live Sandbox $30~50)
- 메이커 체결률 실측 필수
- 실제 adverse selection 측정

### CONDITIONAL 시
- 운영자 결정 — Phase 1.5 진입 또는 추가 검증

### FAIL 시 (v2 강화)
**임계 완화 금지 (다중검정 함정)**

다음 옵션 권고:
1. **Manual semi-discretionary 트레이딩 전환** — 자동매매 archetype 재고
2. **Buy-and-hold + 50d MA cash exit** (Grayscale 2023 모델)
   - Sharpe 1.9 (수수료 미차감, 실거래 ~1.5 예상)
   - 운영자 30% MDD 한도 내 운영 가능
3. **운영자 가설 청취** — 혹시 모를 영역

> **10번째 실패 방지를 위해 자동매매 자체 *중단* 도 정직한 선택**.

## 12. (선택) Han·Kang·Ryu TSMOM 사이드 비교

운영자 요청 시 수행. 결과:
- TSMOM (28d × 5d) Sharpe: X.XX vs 1d 돌파: X.XX
- 더 강한 쪽 → Phase 2 우선 엔진 후보

## 13. Setup Registry 등록
- setup_id: 1d_breakout_long_v1
- status: PAPER_ONLY / CANDIDATE / DISABLED
- params_hash: <sha256>
- DSR: X.XXX
- registered_at: YYYY-MM-DD HH:MM:SS UTC
```

---

## 10. 다음 세션 인계

`docs/HANDOFF.md` 갱신 필수:
- Phase 1 결과 요약 (PASS/CONDITIONAL/FAIL)
- 외장 SSD 인프라
- Supabase 비활성화
- Setup Registry 첫 등록
- DSR 결과
- 신규 모듈 리스트
- 다음 세션 권고

---

## 11. 트러블슈팅

### Q. DSR이 0.95에 *약간* 미달 (예: 0.93)
**A. CONDITIONAL 판정**. 임의로 PASS 처리 금지. 다른 10개 체크리스트가 모두 통과면 운영자 결정. 단, Phase 1.5 paper-trade에서 추가 검증 필수.

### Q. arXiv 2502.18625 maker 데이터가 BTCUSDT만이라 다른 알트에 적용 가능한가?
**A. 보수적 일반화 권고**. 알트는 일반적으로 spread 넓음, depth 얕음 → maker 비용 *더 나쁠* 가능성. Case A가 BTCUSDT보다 더 낙관일 위험.

### Q. 운영자 9개 시도가 정말 effective N = 91인가? 더 작을 수도 있나?
**A. 보수적 추정**. 9개 strategy × 10 파라미터 (각각 grid search 가정). 실제 N은 50~150 범위 가능. DSR 계산 시 N=91, N=50, N=150 *3가지* 시나리오 보고 권장.

### Q. Han·Kang·Ryu TSMOM 사이드 비교가 1d 돌파 결과를 *왜곡*하지 않나?
**A. 별도 setup_id로 등록**. 사이드 비교는 *부수 정보*. 1d 돌파 판정에 영향 X. 다중검정 카운트 +1.

### Q. Phase 1 PASS 했는데 Phase 1.5 micro-live에서 손실 발생
**A. 정상**. Backtest vs 실거래 갭은 항상 존재. 90일 누적 -10% 또는 MDD > 25% 도달 시 *Stage 3 하드 룰* 적용 — 자동매매 중단 검토.

### Q. 어떤 임계가 약간 안 맞는데 *조금만* 바꿔보고 싶음
**A. 절대 금지** (Bailey-Lopez de Prado 2014 다중검정 함정). 임계 변경 = 새 setup_id = effective N +1 = DSR 추가 보정. 본 세션은 단 1회 검증.

---

## 12. 시작 명령 (Claude Code가 처음 받을 메시지)

> 위 Phase 1 plan v2 를 누락 없이 이해했다면 다음을 진행:
> 
> 1. §0-2 문서 모두 `view` (필수)
> 2. `pytest tests/ -q` 실행하여 367+ passed 확인
> 3. **scipy 의존성 확인** (`pip show scipy`)
> 4. Supabase MCP 사용 가능 확인 (`execute_sql "SELECT 1"`)
> 5. §8 단계 A 의 plan 을 운영자에게 보여주고 **승인 받은 후에만** 단계 B 진행
> 6. 각 단계 종료 시 운영자 명시 승인 게이트
> 7. **운영자에게 즉시 물어볼 것** (단계 A 보고에 포함):
>    - 외장 SSD 드라이브 문자 (E:? D:? 다른 것?)
>    - Samsung T7 Shield 또는 다른 모델인지
>    - 외장 SSD 현재 여유 공간
>    - **Han·Kang·Ryu TSMOM 사이드 검증 진행 여부** (선택 작업)
> 
> 코드 변경은 plan 보여주고 승인 후에만. `trading/executor.py`, `main_7590.py`, `config/settings.py`(보호종목 등)은 절대 수정 금지.
> 
> **사전확정 임계 절대 변경 금지** (Bailey-Lopez de Prado 다중검정 함정).
> **9개 실패 전략 재시도 절대 금지**.
> **DSR 계산 생략 절대 금지** (Phase 1 미완료 처리).
> 
> 단계 A 끝나면 멈추고 운영자 응답 대기.

---

## 끝.

본 plan v2를 누락 없이 따르면:
- **9번 실패의 진짜 메타-원인 (Bailey-Lopez de Prado 다중검정)** 보정
- 6명 외부 평가자 수렴 결론 실행
- 운영자가 *이미 가진* 1d 돌파 후보를 *DSR로 검증된* 형태로 *완성*
- 외장 SSD로 *비용 0, 용량 무제한* 인프라
- Setup Registry로 다중검정 자동 차단
- **arXiv 2502.18625 메이커 실증 증거** 반영으로 비용 가정 보수화
- (선택) Han·Kang·Ryu TSMOM 사이드 비교로 *대안 카테고리* 확인
- 실거래 GO 가능성 (Phase 1.5 진입 자격)
- 10번째 실패 방지 (Stage 3 하드 룰)

**다음 세션 (Phase 1.5)**: Phase 1 PASS 시 운영자가 별도 prompt 요청. Micro-live Sandbox $30~50, 1d 돌파 setup 만, 시스템 검증 + 메이커 실측.
