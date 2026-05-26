# Claude Code 검증 Plan 프롬프트 — 4-엔진 포트폴리오 사전 검증

> **사용법**: 이 파일 전체를 복사해서 Claude Code 새 세션에 붙여넣기.
> Claude Code가 명세서 view → plan 보여주고 운영자 승인 → 검증 코드 → pytest 순서로 진행한다.
> **본 검증은 *코드 변경 없이* 순수 분석 작업이다.** 결과 따라 채택 후 별도 세션에서 코드 변경.

---

## 0. 컨텍스트 인계 (먼저 읽고 이해할 것)

### 0-1. 프로젝트 상태
- **작업본**: `C:\Users\jaeho\OneDrive\Desktop\Cursor\BINANCE_FUTURES_BOT`
- **git 클론**: `C:\Users\jaeho\OneDrive\Desktop\Cursor\BINANCE_FUTURES_BOT_GITHUB`
- **수집 클론**: `C:\bots\BINANCE_FUTURES_BOT` (무인 수집용)
- **GitHub HEAD**: `018b109` 이후 (HANDOFF 참조)
- **전체 테스트**: 367+ passed
- **Supabase**: MCP 연동됨 (프로젝트 `aoeqgptytzuhvvchsgul`, "Binance", ap-southeast-1)
- **실거래 GO**: **HOLD** (해제 금지)

### 0-2. 먼저 읽어야 할 문서 (`view` 필수)
1. `docs/HANDOFF.md` — 전체 흐름 (9개 전략 실패 이력 포함)
2. `docs/STRATEGY_REALISM_REVIEW.md` — 어떤 전략이 왜 실패했는지
3. `CLAUDE.md` — TIER 1 절대 룰 (보호종목, HOLD, 시크릿 출력 금지)
4. `config/settings.py` — 기존 보호종목·게이트 설정
5. `data/persistence.py` — Supabase 어댑터
6. `backtesting/data_loader.py` — OHLCV 로더
7. `backtesting/backtest_engine.py` — 백테스트 엔진

### 0-3. 9개 실패 전략 (재시도 금지)
1. OI-급증
2. 돌파 1h (NET NEGATIVE 입증)
3. 돌파 1d (PF 1.39였으나 n=89 부족) ← **이번 검증의 1번 후보**
4. 펀딩 페이드
5. ORB 인트라데이
6. CSM 스윙
7. LCR 이벤트
8. CCS-Lite
9. ML EV (LightGBM)

→ 본 검증은 *9개와 다른 카테고리* 후보 4개를 *사전 IC*로 검증한다.

### 0-4. 운영자 결정사항 (변경 금지)
| 항목 | 결정 |
|---|---|
| 목표 수익 | **월 5~15%** (공격) |
| MDD 허용 | **30%** |
| 자산 범위 | USDT-M perp 중심 |
| 운영 방식 | 완전 자동 24/7 |
| 빈도 | 스윙 (1~10/week) |
| 메커니즘 | 포트폴리오 (여러 엔진 조합) |
| 초기 자본 | **$1,000** (검증 후 확장) |

---

## 1. 본 세션의 목적 (단 하나)

**4개 전략 후보 각각에 *카테고리 수준의 알파*가 존재하는지 데이터로 검증.**

- 검증 결과가 PASS인 후보만 *별도 세션에서* R0 백테스트 진행
- 본 세션에서는 **trading 로직, executor, main_7590, settings 절대 수정 금지**
- 본 세션 결과물 = 분석 코드 + 검증 보고서(`docs/STRATEGY_VERIFICATION_REPORT.md`) + `backtest_runs` 적재

---

## 2. 검증할 후보 4개 (이번 세션) + 1개 (다음 세션 결정)

### 후보 1: 1d 돌파 확장판 (재검증)
**기존 결과**: PF 1.39 (IS), 1.63 (OOS), n=89 — 표본 부족이 유일한 FAIL 사유
**이번 검증 목표**: 종목 14→50, 기간 2년→4년 확장 후 n≥300 확보하여 신뢰도 검증

**피처**:
- `donchian_position` = (close - donchian_high_20) / atr_20
- `ema_distance` = (close - ema_200) / ema_200
- `adx_14` (Wilder)
- `volume_ratio` = volume / sma_20(volume)

**forward return**: +1d, +3d, +7d

**구체 룰 (Gate 3 backtest용)**:
- 진입: `close > donchian_high_20.shift(1)` AND `close > ema_200` AND `adx_14 > 25`
- SL: 진입가 − 2 × ATR(14, 1d)
- TP1: +1.5R (50% 청산)
- TP2: +3.0R (30% 청산)
- 트레일: Donchian low_10 (20% 청산)
- 시간스톱: 30일 미달성 → 종가 청산

---

### 후보 2: 횡단면 모멘텀 (Cross-Sectional Momentum)
**학계 근거**: Han·Kang·Ryu SSRN 4675565 (암호 모멘텀 Sharpe ≈1.5), Asness 30년 연구

**피처**:
- `momentum_30d` = (close_t / close_t-30) - 1
- `momentum_skip_30d` = (close_t-7 / close_t-30) - 1 (단기 reversal 제거)
- `volatility_30d` = std(returns_1d, 30)
- `risk_adjusted_momentum` = momentum_skip_30d / volatility_30d

**전략**:
- 매주 월요일 00:00 UTC 리밸런싱
- 50종목 중 `risk_adjusted_momentum` 상위 10% (5종목) LONG
- 하위 10% (5종목) SHORT
- Market-neutral (자본 LONG = 자본 SHORT)

**forward return**: +7d (다음 리밸런싱까지)

---

### 후보 3: BTC-베타 추종 (Beta-Adjusted Trend)
**가설**: BTC가 강한 추세일 때 high-beta 알트가 outperform

**피처**:
- `alt_beta_60d` = rolling regression coefficient (alt 1h return ~ BTC 1h return, 60 bars)
- `btc_above_ema200` = (btc_close > btc_ema_200)
- `btc_adx_14` (1d frame)
- `btc_trend_strength` = (btc_close - btc_ema_200) / btc_ema_200

**전략**:
- 게이트: `btc_above_ema200` AND `btc_adx_14 > 25` AND `btc_trend_strength > 0`
- 게이트 통과 시: `alt_beta_60d > 1.5` 인 종목 LONG (top 10)
- 게이트 미통과: 무거래

**forward return**: +4h, +1d, +3d

---

### 후보 4: 펀딩 정산 이벤트 (Funding Settlement Play)
**가설**: 펀딩 정산 직전 헤지 청산 압력 → 정산 직후 회복

**피처**:
- `funding_value` (다음 정산 펀딩 추정치)
- `funding_abs_z` = |funding| z-score (60d rolling)
- `oi_change_1h` = (oi_t - oi_t-1h) / oi_t-1h
- `minutes_to_settlement` (정산까지 시간)

**전략**:
- 트리거: `funding_abs_z > 2.0` AND `30 < minutes_to_settlement < 35` (5분 윈도)
- 펀딩 양수 (LONG 쏠림) → SHORT 진입, +60분 후 청산
- 펀딩 음수 (SHORT 쏠림) → LONG 진입, +60분 후 청산

**forward return**: +30분, +1h, +2h

---

### 후보 5: 신규 상장 효과 (이번 세션 X — 후보 1~4 결과 보고 결정)
*후보 1~4 결과 종합 후 PASS 0~1개면 추가 진행, 2~3개면 우선 그것부터.*

---

## 3. 검증 방법 — 3-Gate 사전확정

**모든 후보에 동일 방법론 적용.** 변형 금지.

### Gate 1: IC (Information Coefficient)
- 피처 vs forward return 의 **Spearman 상관계수** (비선형 robust)
- 측정 horizon: 4h, 1d, 7d 동시
- 임계:
  - **IC > 0.05** → ✅ 통과
  - IC ∈ [0.02, 0.05] → 🟡 borderline
  - IC < 0.02 → ❌ FAIL

### Gate 2: Decile Monotonicity
- 피처값을 **10등분** → 각 구간의 평균 forward return
- Top decile − Bottom decile = `decile_spread`
- 단조성 검증: 10개 구간 평균이 *대체로 단조* (Spearman corr(decile_idx, mean_return) > 0.5)
- 임계 (4h forward 기준):
  - **spread > 0.5%** AND 단조성 통과 → ✅ 통과
  - spread ∈ [0.2%, 0.5%] OR 단조성 약함 → 🟡 borderline
  - spread < 0.2% OR 비단조 → ❌ FAIL

### Gate 3: Cost-Aware Simple Backtest
- 위 §2의 각 후보별 "구체 룰"로 단순 백테스트
- **비용 모델 (고정)**:
  - 메이커 진입 0.018% + 테이커 청산 0.045% + 슬립 0.05% = RT 0.113%
  - 자금조달비용은 펀딩 정산 시점에 보유 시 적용
- 임계:
  - **PF > 1.15** AND
  - **n ≥ 100** AND
  - **MDD ≤ 25%** AND
  - **평균 net trade > 0.2%** AND
  - **거래 종목 중 ≥ 40% net positive** → ✅ 통과
  - 일부만 충족 → 🟡 borderline
  - 그 외 → ❌ FAIL

### 종합 판정
| 결과 | 의미 | 다음 단계 |
|---|---|---|
| 🟢 **PASS** | 3개 게이트 모두 통과 | 별도 세션에서 R0 본 검증 |
| 🟡 **CONDITIONAL** | 2개 통과 + 1개 borderline | 운영자 결정 (R0 진행 OR 추가 분석) |
| 🔴 **FAIL** | 그 외 | 정직 종료, 후보에서 제외 |

### 룩어헤드 차단 (절대)
- 모든 피처는 t 봉 close 까지 데이터만 사용
- forward return은 t+1 봉 시가 진입 가정 (next-bar fill)
- Rolling 통계의 fit window 명확히 분리 (forward leak 차단)
- 50종목 universe는 **점-인-타임** 추출 (오늘의 top-50을 과거에 적용 금지) — *불가능하면 차선책*: 검증 기간 전체에 *연속 존재한* 종목만 사용

### 비용 모델 (고정, 변형 금지)
- 단일 진입(메이커): 0.018%
- 단일 청산(테이커): 0.045%
- 슬립(tier 2 알트 평균): 0.05%
- **RT = 0.113%** (단일 종목 단일 트레이드)
- 펀딩 정산 시 보유 중이면 actual funding rate × notional 차감

---

## 4. 데이터 Prereq (검증 전 작업)

### 4-1. 종목 확장 (14 → 50)
**현재**: Supabase ohlcv 14종목 × 2년 = 245,280행
**목표**: 50종목 × 4년

**선정 룰**:
1. Binance USDT-M perp 현재 거래대금 top-50
2. **보호종목 6 제외** (BTCUSDT, ETHUSDT, HOLOUSDT, CFXUSDT, LYNUSDT, INJUSDT)
   - *주의*: BTCUSDT는 *시장 인덱스 용도*로는 read-only 적재 유지 (보호종목 룰은 *거래 금지*이지 *데이터 사용 금지*가 아님)
   - 단, 본 검증의 *진입 후보*에서는 제외
3. 검증 기간 4년 연속 존재 종목만 (생존편향 회피 차선책)

**구현**:
- `backtesting/backfill_history.py` 확장 — `--symbols-top-n 50 --years 4`
- 페이지네이션 klines (Binance API limit 1000 per call) + funding history
- Supabase에 멱등 upsert (UNIQUE 제약 활용)
- 추정 추가 행: 50종목 × 1d × 1460일 = 73,000행 + 4h × 8760 = 438,000행 (총 ~510k)
- Supabase free tier (현재 사용 1.93GB / 2GB)에서 **위험**할 수 있음

### 4-2. Supabase 디스크 사전 점검 (필수)
**HANDOFF Phase 2-E**: 무료 티어 디스크 1.93GB / 2GB (96.5%) 상태.

**Claude Code 작업 시작 전 반드시 확인**:
```sql
-- MCP execute_sql 로
SELECT pg_size_pretty(pg_database_size('postgres')) AS db_size;
SELECT pg_size_pretty(pg_total_relation_size('ohlcv')) AS ohlcv_size;
SELECT pg_size_pretty(pg_total_relation_size('agg_trades')) AS agg_trades_size;
```

**조치**:
- agg_trades > 500MB: Phase 2-E §Step 3의 cleanup SQL 실행 (HANDOFF 참조)
  - `DELETE FROM agg_trades WHERE ts < now() - interval '1 hour';`
  - `VACUUM FULL agg_trades;`
- 디스크 < 1.5GB 확보 후 backfill 진행
- 그래도 부족하면 **운영자에게 보고**, backfill 종목/기간 축소 협의

### 4-3. 4년 backfill 실행
**커맨드** (운영자 승인 후):
```bash
python -m backtesting.backfill_history \
    --years 4 \
    --interval 1d \
    --symbols-top-n 50 \
    --exclude-protected \
    --write-supabase
```
- 1d 먼저 (가장 작음, 50 × 1460 = 73k 행)
- 1d 검증 후 필요시 4h, 1h 추가

---

## 5. 작업 단계 (순서 엄수)

### 단계 A — 환경 점검 + Plan 보고 (15분, 운영자 승인 게이트)
1. 위 §0 문서 모두 `view`
2. `pytest tests/ -q` → 367+ passed 확인
3. Supabase 디스크 상태 점검 (§4-2 SQL 실행)
4. 검증 plan 요약 + 4-1 backfill 규모 산정 → **운영자에게 보고하고 승인 받기**
5. 승인 후에만 단계 B 진행

### 단계 B — 데이터 Prereq (운영자 승인 후)
1. (필요 시) Supabase cleanup (Phase 2-E §Step 3)
2. `backfill_history.py` 확장 — `--symbols-top-n` 및 `--years` 옵션
3. 1d backfill 실행 (50종목 × 4년)
4. 적재 검증: `SELECT count(*), min(ts), max(ts), count(distinct symbol) FROM ohlcv`
5. 단위테스트 추가 (`tests/test_backfill_history.py` 확장)

### 단계 C — 검증 모듈 작성 (신규 코드)
**신규 파일** (`analytics/` 폴더에 모듈, `tests/` 에 단위테스트):
1. `analytics/verification/feature_engineering.py`
   - 4개 후보의 모든 피처 함수
   - 룩어헤드 차단 단위테스트 필수
2. `analytics/verification/ic_analysis.py`
   - Spearman IC 계산 (per feature × per horizon)
   - 통계적 유의성 (t-stat, p-value)
3. `analytics/verification/decile_analysis.py`
   - Top/Bottom decile sort + 평균 forward return
   - 단조성 점수 (decile index vs mean return Spearman)
4. `analytics/verification/simple_backtest.py`
   - Gate 3 백테스트 (각 후보별 단순 룰)
   - 룩어헤드 차단 + 비용 모델 + next-bar fill
5. `analytics/verification/runner.py`
   - 4개 후보 동시 평가
   - 결과를 `backtest_runs` Supabase 테이블에 적재
6. `scripts/run_verification.py` — CLI 엔트리

**단위테스트** (반드시 동반):
- `tests/test_verification_features.py` — 피처 룩어헤드 차단
- `tests/test_verification_ic.py` — IC 계산 정확성 (합성 데이터)
- `tests/test_verification_decile.py` — 단조성 측정
- `tests/test_verification_backtest.py` — Gate 3 단순 룰 동작 + 비용 적용
- `tests/test_verification_runner.py` — 4개 후보 통합 mock 흐름

**금지 사항** (절대):
- `trading/executor.py`, `main_7590.py`, `config/settings.py`(보호종목 등) 수정 금지
- 기존 라이브 코드 경로 변경 금지
- 본 세션은 *순수 분석 추가*만, 라이브/실거래 영향 0

### 단계 D — 4개 후보 검증 실행
1. `python scripts/run_verification.py --candidates 1d_breakout cross_sectional_mom btc_beta funding_settlement`
2. 결과:
   - 각 후보별 3-Gate 결과 콘솔 출력
   - `backtest_runs` 적재 (run_id 예: `verify_1d_breakout_20260525`, `verify_cross_sec_mom_20260525`, etc.)
3. 보고서 생성: `docs/STRATEGY_VERIFICATION_REPORT.md`
   - 각 후보별 IC/Decile/Backtest 결과표
   - PASS/CONDITIONAL/FAIL 판정 + 사유
   - 차후 권고 (R0 진행 후보 / 폐기 후보)

### 단계 E — 보고 + GitHub 반영 (A안)
1. `pytest tests/ -q` → 회귀 0 확인
2. 작업본 → 클론 명시 복사 (`backtesting/backfill_history.py`, `analytics/verification/*`, `scripts/run_verification.py`, `tests/test_verification_*`, `docs/STRATEGY_VERIFICATION_REPORT.md`)
3. 클론에서 보안 체크 (.env, 토큰 노출 0 확인)
4. `git add <파일명 명시>` (`-A` / `.` 금지)
5. 커밋: `git -c user.name="kong9365" -c user.email="jaehong9365@gmail.com" commit ...`
6. 커밋 메시지 끝: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
7. push
8. 운영자에게 결과 보고 + **다음 세션 권고** (R0 진행 후보 / 신규 상장 검증 추가 여부)

---

## 6. 절대 룰 (TIER 1, 절대 위반 금지)

1. **시크릿/토큰/API 키 출력 금지** — 코드·로그·메시지·보고서 일체.
2. **보호종목 거래 금지**: BTCUSDT, ETHUSDT, HOLOUSDT, CFXUSDT, LYNUSDT, INJUSDT는 *진입 후보에서 제외*. 단, *시장 인덱스 read-only*는 허용.
3. **실거래 GO = HOLD 유지**. 본 세션은 *분석* 작업, 주문 0건. `--dry-run` 도 실행 안 함 (필요 없음).
4. **게이트(RiskManager, CostGuard, PairWhitelist, CapitalManager) 코드 수정 금지**.
5. **`trading/executor.py`, `main_7590.py`, `config/settings.py` 수정 금지**. 분석 모듈만 추가.
6. **운영자 승인 게이트** (강제):
   - 단계 A 끝 → 운영자 plan 승인 후 단계 B
   - 단계 B 끝 → backfill 완료 보고 후 단계 C
   - 단계 D 끝 → 보고서 보고 후 단계 E
   - 운영자 승인 없이 다음 단계 진행 금지

---

## 7. 작업 메커니즘 (A안 GitHub 반영)

```
[작업본] OneDrive\...\BINANCE_FUTURES_BOT
   ↓ (수정·실행)
[명시 복사] 파일별 cp (`git add -A` 금지)
   ↓
[클론] BINANCE_FUTURES_BOT_GITHUB
   ↓ (pytest, 보안 체크)
[명시 add] git add <파일1> <파일2> ...
   ↓
[커밋 override] git -c user.name=... -c user.email=... commit ...
   ↓
[push] origin main
```

**절대 커밋 금지 파일**:
- `.env`, `.env.SAFETY_BACKUP`
- `logs/`, `reports/`
- `data/*.db`, `backtests/cache/`
- 실제 키·토큰

---

## 8. 출력 형식 — `docs/STRATEGY_VERIFICATION_REPORT.md`

다음 양식으로 보고서 생성:

```markdown
# 전략 후보 검증 보고서 (Strategy Verification Report)

> 작성: 2026-05-25 | 데이터: 50종목 × 4년 (보호종목 제외) | 비용 RT: 0.113%

## 종합 요약

| 후보 | Gate 1 (IC) | Gate 2 (Decile) | Gate 3 (Backtest) | 판정 |
|---|---|---|---|---|
| 1. 1d 돌파 확장 | IC=0.XXX | spread=X.XX% | PF=X.XX, n=XXX, MDD=XX% | 🟢/🟡/🔴 |
| 2. 횡단면 모멘텀 | IC=0.XXX | spread=X.XX% | PF=X.XX, n=XXX, MDD=XX% | 🟢/🟡/🔴 |
| 3. BTC-베타 | IC=0.XXX | spread=X.XX% | PF=X.XX, n=XXX, MDD=XX% | 🟢/🟡/🔴 |
| 4. 펀딩 정산 | IC=0.XXX | spread=X.XX% | PF=X.XX, n=XXX, MDD=XX% | 🟢/🟡/🔴 |

## 후보 1: 1d 돌파 확장 — [판정]

### Gate 1 — IC Analysis
| 피처 | IC (4h) | IC (1d) | IC (7d) | 결론 |
| donchian_position | 0.0XX | 0.0XX | 0.0XX | ... |
| ... |

### Gate 2 — Decile Sort (4h forward)
| Decile | Mean Return | N |
| 0 (low) | X.XX% | XXX |
| ... |
| 9 (high) | X.XX% | XXX |
| **spread** | **X.XX%** | |
| 단조성 (Spearman) | 0.XX |

### Gate 3 — Simple Backtest
- 거래 횟수: XXX
- 승률: XX.X%
- Profit Factor: X.XX
- Max DD: XX.X%
- 평균 net trade: X.XX%
- 횡단면 (40+ 종목 중 양+): XX%

### 판정 사유
... (서술)

## 후보 2~4: 동일 양식 ...

## 차후 권고

### R0 진행 권고 후보 (PASS / CONDITIONAL)
- ...

### 폐기 권고 후보 (FAIL)
- ... (정직한 사유)

### 추가 검증 권고
- 후보 5 (신규 상장 효과) 검증 진행 여부
- backfill 추가 (4h, 1h) 가치 평가

## Supabase backtest_runs 적재 ID
- verify_1d_breakout_YYYYMMDD
- verify_cross_sec_mom_YYYYMMDD
- verify_btc_beta_YYYYMMDD
- verify_funding_settlement_YYYYMMDD
```

---

## 9. 다음 세션 인계 (작업 종료 후)

본 세션 종료 시점에 `docs/HANDOFF.md` 갱신:
- 검증 세션 결과 요약 (PASS/CONDITIONAL/FAIL 후보 수)
- 다음 세션 권고 (R0 진행 후보별)
- 신규 상장 후보 검증 진행 여부

---

## 10. 시작 명령 — Claude Code가 처음 받을 메시지

본 마크다운 파일 전체를 새 세션에 붙여넣고 다음과 같이 끝맺으면 된다:

> 위 검증 plan을 누락 없이 이해했다면 다음을 진행:
>
> 1. §0-2 문서 모두 `view`
> 2. `pytest tests/ -q` 실행하여 367+ passed 확인
> 3. Supabase MCP `execute_sql`로 §4-2 디스크 상태 점검
> 4. §5 단계 A의 plan을 운영자에게 보여주고 **승인 받은 후에만** 단계 B 진행
> 5. 각 단계 종료 시 운영자 명시 승인 게이트 (단계 B/C/D/E 진입 전 보고)
>
> 코드 변경은 plan 보여주고 승인 후에만. `trading/executor.py`, `main_7590.py`, `config/settings.py`는 절대 수정 금지. 본 세션은 *분석 추가만*.
>
> 단계 A 끝나면 멈추고 운영자 응답 대기.

---

## 11. 트러블슈팅 (Claude Code 자주 묻는 경우)

### Q. 본 세션 중 9개 실패 전략을 "수정해서 재시도"하면 안 되나?
**A. 절대 금지**. 9개는 정직 종료됨. 본 세션은 *다른 카테고리* 4개 후보의 *카테고리 알파* 검증. 9개 재시도 = 다중검정 함정 재발.

### Q. 임계 (PF 1.15 등) 가 미세하게 안 맞으면 임계 완화?
**A. 절대 금지**. 사전확정 임계는 운영자 목표(월 5~15%)에 정합된 *현실적* 기준. 9개 실패는 임계 사후 완화 시도가 아니라 *임계 통과 못 함*. 본 검증도 같은 룰.

### Q. Phase 2-E 디스크 cleanup 도 같이 해야 하나?
**A. 별개 작업**. 본 세션은 ohlcv backfill (1d 봉, 디스크 부담 적음). agg_trades 가 디스크 잡고 있으면 *cleanup만* 하고 (mass insert 안 함), 디스크 < 1.5GB 확보 후 ohlcv backfill. Phase 2-E 의 데몬 재가동·1m aggregate 도입 등은 *별도 세션*.

### Q. 50종목 universe 가 *점-인-타임* 추출 불가능하면?
**A. 차선책**: 검증 기간 4년 *연속 존재*한 종목만 사용 + 분석 보고서에 *생존편향* 한계 명시. 운영자가 알고 R0 진행 여부 결정하도록 *honest reporting*.

### Q. 후보 4 (펀딩 정산) 가 funding_history 부족으로 검증 곤란하면?
**A. 4년 backfill에 funding_history 도 포함** (binance API 5년+ 제공). 그래도 부족하면 보고서에 *데이터 한계* 명시하고 FAIL 처리 (임계 완화 금지).

### Q. 단계 C 검증 모듈 작성 중 추가 발견되는 *명백한 버그* (예: 룩어헤드)?
**A. 발견 즉시 운영자에게 보고**, 승인 후 정정. `docs/CORRECTIONS_v3_1_2.md` 양식으로 기록 (인라인 주석 `# v3.1.2 정정 / 검증세션`).

---

## 끝.

본 plan 을 누락 없이 따르면 9번 실패의 진짜 교훈 (*카테고리 사전 검증 없이 가설부터 들어감*) 을 살려 10번째 실패를 피할 수 있다.
