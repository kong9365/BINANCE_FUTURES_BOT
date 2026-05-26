# M12 — R0_QUALIFIED 결정 보고서

> **Generated**: 2026-05-26T16:27:36.478655+00:00
> **Milestone**: M12 (setup_registry 결과 검토)
> **Plan**: docs/REFACTOR_PLAN_v2_BLUEPRINT.md M12

---

## 1. Setup Registry 전체 상태

- 총 setup: **1**
- 활성 (R0_QUALIFIED + PAPER_ONLY): **0**
- 평가 이력 (setup_evaluations): 1 setups

## 2. Setup 별 7기준 판정

### `1d_tsmom_donchian_long_v1`

- **status**: DISABLED
- **last_evaluation_ts**: 2026-05-26T16:21:23.408958+00:00
- **reason**: 6 criteria failed: min_n, min_pf, min_expectancy_r, min_avg_win_loss_ratio, max_mdd_pct, min_top3_excluded_pf

- 평가 횟수: 2
- 최근 평가: 2026-05-26T16:21:23.408958+00:00

**7기준 판정: FAIL (DISABLED, 6 fails) (1/7)**

| 기준 | 측정값 | 통과 |
|---|---|---|
| n >= 200 | 4 | NO |
| Net PF >= 1.25 | 0.40124960685997735 | NO |
| Expectancy_R > 0 | -0.23663636898424048 | NO |
| avg_win/loss >= 1.5 | 1.2037488205799323 | NO |
| MDD <= 25% | 27.10143069124191% | NO |
| Single symbol < 25% | 0.5620617266476294% | YES |
| Top-3 excluded PF >= 1.0 | 0.0 | NO |

---

## 3. 운영자 결정 옵션

### ❌ 모든 setup DISABLED

M11 실측 결과 모든 setup 이 7기준 fail (데이터 부족 또는 알파 부재).

**선택지**:

**옵션 A — 데이터 확장 후 재실행** (권장):
```bash
# mainnet 데이터 read-only (USE_TESTNET=false 임시) + 18~50 종목 + 2~5년
USE_TESTNET=false python scripts/run_m11_backtest.py --backfill --universe-size 18 --years 3
```

**옵션 B — Stage 3 (청사진 §7.5)**:
- A. Manual semi-discretionary 전환
- B. Buy-and-hold + 50d MA cash exit (Grayscale 2023)
- C. 운영자 가설 청취 (다른 archetype)

**옵션 C — HANDOFF '알파 영구 중단' 결정 *재확정***:
- 2026-05-23 결정 (9개 전략군 모두 fail) 유지
- 본 리팩토링은 *거버넌스 인프라 완성* (M0~M10 + 대시보드) 의 가치만 인정
- 알파 추구는 *영구* 중단 (HANDOFF.md 라인 14-15 정합)

---

## 4. 다음 단계

1. 본 보고서 검토
2. 대시보드 페이지 6 (Setup Registry) 에서 시각화 확인:
   - `scripts/run_dashboard.bat` → http://localhost:8501
3. 운영자 명시 결정 (A/B/C 또는 데이터 확장)
4. [docs/HANDOFF.md](HANDOFF.md) 갱신 (재검토 최종 결과)
5. PASS 시: [docs/PHASE1_5_MICRO_LIVE_PROMPT.md](PHASE1_5_MICRO_LIVE_PROMPT.md) 절차 진행

