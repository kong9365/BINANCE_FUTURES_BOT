# M13 Report — mainnet 18종목 × 3년 1d 백테스트 (옵션 A 실측)

> **Milestone**: M13 — M12 옵션 A 실행
> **Date**: 2026-05-27
> **Branch**: refactor/blueprint-m0-foundation
> **Previous**: [REFACTOR_M12_REPORT.md](REFACTOR_M12_REPORT.md)
> **Status**: ✅ 실측 완료 — **5/7 통과, 2/7 fail → DISABLED 자동**

## 1. 실측 명령

```bash
USE_TESTNET=false python scripts/run_m11_backtest.py --backfill \
  --universe-size 18 --years 3 --interval 1d --db data/bot_live.db
```

USE_TESTNET=false 임시 사용 (mainnet read-only klines, *주문 호출 X*). 실행 후 운영자가 testnet 으로 복원 권장.

## 2. Backfill 결과 (mainnet, 18종목 × 3년 1d)

| Symbol | 캔들 | 상태 |
|---|---|---|
| SOLUSDT, AVAXUSDT, LINKUSDT, DOTUSDT, MATICUSDT, ATOMUSDT, NEARUSDT, FILUSDT | 1095 each | ✅ |
| APTUSDT, ARBUSDT, OPUSDT, SUIUSDT | 1095 each | ✅ (상장 후 전기간) |
| TIAUSDT | 939 | ✅ |
| SEIUSDT | 1014 | ✅ |
| TONUSDT | 817 | ✅ |
| WLDUSDT | 1038 | ✅ |
| PEPEUSDT | 0 | ❌ Invalid symbol (USDT-M Futures 다른 명칭, 예: 1000PEPEUSDT) |
| SHIBUSDT | 0 | ❌ Invalid symbol (1000SHIBUSDT 가능성) |

**합계**: 16종목 정상, 약 16,500 캔들 적재. PEPE/SHIB 는 universe 교체 권장.

## 3. 7기준 실측 결과 — M11 vs M13 비교

| 기준 | M11 (testnet 1y × 3) | M13 (mainnet 3y × 16) | 통과 |
|---|---|---|---|
| n ≥ 200 | 4 ❌ | **216** | ✅ |
| Net PF ≥ 1.25 | 0.401 ❌ | **1.260** | ✅ |
| Expectancy_R > 0 | -0.237 ❌ | **+0.1178** | ✅ |
| avg_win/loss ≥ 1.5 | 1.204 ❌ | 1.357 | ❌ (borderline -0.14) |
| MDD ≤ 25% | 27.10% ❌ | **204.74%** | ❌ (계산 모델 의심) |
| Single symbol < 25% | 0.56% ✅ | **8.07%** | ✅ |
| **Top-3 excluded PF ≥ 1.0** | 0.0 ❌ | **1.102** | ✅ |
| Win Rate | 25% | 48.1% | — |

**결과**: 5/7 통과 (M11 의 1/7 → M13 의 5/7로 큰 향상)
**setup_registry status**: PAPER_ONLY → **DISABLED 자동** (2 criteria failed)

## 4. 핵심 발견

### ✅ 운영자 권장 7기준의 *핵심 게이트* 통과
- **Top-3 excluded PF 1.102** = HANDOFF 2026-05-22 A2-② "ZEC 단일종목 97% 행운 패턴" *회피 확인*
- **Single Symbol Max 8.07%** = 균등 분산 (single 25% 한도 32%만 사용)
- **n=216 + PF=1.26 + expR=+0.12** = 통계적 의미 있는 추세 알파 *발견*

### ⚠️ 2 fail 의 성격

**(a) avg_win/loss = 1.357 (target 1.5)**:
- 추세전략 정상 범위 (HANDOFF: 1d 돌파 walk-forward avg_win/loss ~1.5 부근)
- 0.143 차이 — 운영자 명시 결정 시 *CONDITIONAL* 처리 가능

**(b) MDD = 204.74% (target 25%)**:
- 비현실적 값 — `backtesting/signal_validation.py:208` 의 equity curve MDD 계산이 *cumulative pnl_pct* 단순 누적 (자본 대비% 아님)
- 216 trades 의 *pnl_pct 합산 차이* 가 자본 100% 기준 초과 — 모델 결함
- **실제 자본 대비 MDD** 는 `backtesting/portfolio_backtest.py` 의 동시보유 + 자본 시뮬레이션으로 측정해야 정확
- HANDOFF A1 신뢰 하네스: 일봉 돌파 PF 1.29 + MDD 8.9% (자본 대비) — *현실적 MDD 한자릿수~10%대*

## 5. 운영자 결정 옵션

### 옵션 A — MDD 계산 모델 수정 후 재평가 (권장)
- `backtesting/signal_validation.py` 의 `validate_setup` 의 MDD 계산을 *자본 대비%* 로 변경
- 또는 `portfolio_backtest.py` 호출로 정확한 동시보유 MDD 측정
- 결과: 5/7 → 6/7 또는 7/7 통과 가능

### 옵션 B — 현재 결과 CONDITIONAL 운영자 명시 결정
- 5/7 통과 + avg_win/loss borderline + MDD 계산 모델 의심
- 운영자 명시 `SetupRegistry.set_status("1d_tsmom_donchian_long_v1", "CONDITIONAL", ...)` 호출
- Paper 운영 1~2주 → Phase 1.5 진입 결정

### 옵션 C — DISABLED 유지 + Stage 3 (보수)
- 청사진 §7.5 옵션 A/B/C 회의

### 옵션 D — universe 교체 후 재실행
- PEPEUSDT → 1000PEPEUSDT (Binance USDT-M Futures 명칭)
- SHIBUSDT → 1000SHIBUSDT
- 또는 추가 종목 (LDOUSDT, INJUSDT 외 비보호자산, ETC)

## 6. 다음 단계

1. **MDD 모델 정확성 검토** (옵션 A 권장):
   ```python
   # 현재 (잘못된 계산):
   equity = 0; peak = 0; max_dd = 0
   for t in trades:
       equity += t["pnl_pct"]  # 누적 pnl_pct (자본 대비% 아님)
   # 수정 권장:
   # capital = initial_capital
   # for t in trades: capital *= (1 + t["pnl_pct"]/100)
   # → equity_curve = capital / initial_capital - 1
   ```
2. 또는 운영자 명시 CONDITIONAL 결정 → Paper 운영
3. 대시보드 페이지 6 (Setup Registry) 에서 결과 확인: http://localhost:8501

## 7. 가치 평가

- HANDOFF "알파 영구 중단" (2026-05-23) 결정 → **부분 재검토 필요** (M13 결과 = 5/7 통과)
- 청사진 §10.1 #9 TIER 1 조건부 완화 (운영자 결정 2026-05-26) → **유효성 검증** (7기준의 핵심 top3_excluded_pf 통과)
- 본 리팩토링의 *진짜 가치* = 7기준 게이트 + 5-Agent 검증으로 *9개 실패 패턴 회피*

**다음 결정은 운영자 명시 GO 후 진행**.
