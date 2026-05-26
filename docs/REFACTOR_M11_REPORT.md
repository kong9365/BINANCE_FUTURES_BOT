# M11 Report — Testnet 백테스트 7기준 실측

> **Milestone**: M11 — Testnet 데이터로 DailyTSMOMDonchianSkill 백테스트
> **Date**: 2026-05-27
> **Branch**: refactor/blueprint-m0-foundation
> **Previous**: [REFACTOR_M10_REPORT.md](REFACTOR_M10_REPORT.md)
> **Plan**: [REFACTOR_PLAN_v2_BLUEPRINT.md](REFACTOR_PLAN_v2_BLUEPRINT.md) M11
> **Status**: ✅ 완료 (smoke 실행). 실측 결과 = **FAIL** (데이터 부족 원인)

## 1. 신규 6파일 + 1 수정

### 인프라 (3파일)
- `db/migrations/v3_2_1_to_v3_2_2.sql` — `ohlcv_local` 테이블 (Supabase 폐기 후 로컬)
- `backtesting/local_ohlcv_store.py` (175줄) — upsert_many / get_candles / count / get_symbols / get_date_range
- `scripts/run_m11_backtest.py` (260줄) — entry point (backfill + 백테스트 + 7기준 검증 + 보고서)

### 테스트 (2파일, 22 tests)
- `tests/test_local_ohlcv_store.py` (16 tests) — upsert/get_candles/멱등/breakout 형식 호환
- `tests/test_m11_backtest_smoke.py` (6 tests) — ohlcv→breakout 흐름, registry register, validate_and_persist PASS/FAIL, v3.2.2 마이그레이션

### 수정 (1파일)
- `db/init_db.py` — v3.2.2 마이그레이션 등록 (v3.1.1, v3.1.2 패턴 동일 멱등)
- `backtesting/signal_validation_report.py` — UnicodeEncodeError 대응 (✓→[OK], ✗→[X])

## 2. 실측 결과 (2026-05-27, Testnet 1년 1d × 3종목)

### Backfill 결과
| Symbol | 캔들 | 상태 |
|---|---|---|
| SOLUSDT | 365 | ✅ 적재 |
| AVAXUSDT | 0 | ❌ APIError(-1003) Rate limit (IP banned 일시적) |
| LINKUSDT | 365 | ✅ 적재 |

**합계**: 730 캔들 (2종목 정상)

### 백테스트 결과
- **Total trades: 4** (signal frequency 매우 낮음 — 1년 1d × 2종목 → 평균 2 trades/symbol)

### 운영자 권장 7기준 (FAIL 6/7)

```
=== 7-Criteria Validation Report (mode=trend) ===
Result: FAIL [X]

Metrics:
  n                          = 4 (min 200)              ❌
  Net PF                     = 0.401 (min 1.25)         ❌
  Expectancy_R               = -0.2366 (min 0.0)         ❌
  avg_win/avg_loss           = 1.204 (min 1.5)          ❌
  MDD %                      = 27.10% (max 25.0%)       ❌
  Single Symbol Max %        = 0.56% (max 25.0%)        ✅
  Top-3 Excluded PF          = 0.000 (min 1.0)          ❌
  Win Rate                   = 25.0%

Failed criteria (6): min_n, min_pf, min_expectancy_r,
                     min_avg_win_loss_ratio, max_mdd_pct,
                     min_top3_excluded_pf
```

### 자동 status 변경

- `setup_registry.set_status("1d_tsmom_donchian_long_v1", "DISABLED")` ✓
- 사유: "6 criteria failed: min_n, min_pf, min_expectancy_r, min_avg_win_loss_ratio, max_mdd_pct, min_top3_excluded_pf"
- audit_log `SETUP_STATUS_CHANGE` 이벤트 (예상 — 직접 확인은 M12)

## 3. 결과 해석 (M12 전 운영자 참고)

### *데이터 부족이 주요 원인*

- **trades=4** 는 *7기준 통과 불가능한 표본*. n≥200 미달.
- 1년 1d × 3종목 (실제 2) → signal frequency 너무 낮음.

### 권장 추가 검증 (운영자 결정 사항)

**옵션 A — 데이터 확장 후 재실행**:
```bash
# mainnet 데이터 read-only (USE_TESTNET=false 임시) + 2~5년 + 18~50 종목
USE_TESTNET=false python scripts/run_m11_backtest.py --backfill --universe-size 18 --years 3
```
주의: USE_TESTNET=false 면 실거래 키 활성화 — *backfill 동안만 임시 변경*.

**옵션 B — Testnet 그대로 + 더 작은 timeframe**:
```bash
# 1h 또는 4h 로 trade frequency 증가
python scripts/run_m11_backtest.py --backfill --universe-size 18 --interval 4h --years 2
```

**옵션 C — HANDOFF "알파 영구 중단" 결정 유지**:
- 운영자가 2026-05-23 결정 (5개 전략군 모두 FAIL) 을 *재확정*
- M11 결과는 *이를 다시 검증* — 1d Donchian 도 testnet 환경에서 FAIL
- 청사진 §7.5 Stage 3 옵션 A/B/C (Manual / Buy-and-hold + 50d MA / 다른 archetype)

## 4. M12 진입 조건

- [x] M11 entry point + 인프라 작성
- [x] local_ohlcv_store 구현 + 22 tests PASS
- [x] v3.2.2 마이그레이션 자동 적용
- [x] DailyTSMOMDonchianSkill 7기준 백테스트 실측
- [x] setup_evaluations + setup_registry 적재
- [x] `docs/SETUP_VERIFICATION_REPORT_v3_2_0.md` 자동 생성
- [ ] M12 운영자 결정 (Stage 3 옵션 A/B/C 또는 데이터 확장 후 재실행)

## 5. 다음 (M12)

1. **대시보드 페이지 6 (Setup Registry)** 에서 결과 시각화 확인 ([http://localhost:8501](http://localhost:8501))
2. **운영자 의사결정**:
   - 데이터 확장 후 재실행 → M11 옵션 A/B
   - Stage 3 옵션 → M12 결정 보고서
3. `docs/M12_R0_DECISION_REPORT.md` 작성
4. `CLAUDE.md` TIER 1 #9 갱신 (재검증 결과)
5. `docs/HANDOFF.md` 최종 갱신
