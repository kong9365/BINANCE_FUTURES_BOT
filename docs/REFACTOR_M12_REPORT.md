# M12 Report — setup_registry 결과 검토 + R0_QUALIFIED 결정

> **Milestone**: M12 — Setup Registry 결과 검토 + 운영자 결정
> **Date**: 2026-05-27
> **Branch**: refactor/blueprint-m0-foundation
> **Previous**: [REFACTOR_M11_REPORT.md](REFACTOR_M11_REPORT.md)
> **Plan**: [REFACTOR_PLAN_v2_BLUEPRINT.md](REFACTOR_PLAN_v2_BLUEPRINT.md) M12
> **Status**: ✅ 보고서 자동 생성 — **운영자 결정 대기** (옵션 A/B/C)

## 1. 신규 1파일 + 3 갱신

### 신규
- `scripts/run_m12_review.py` (200줄) — Setup Registry 검토 + 7기준 자동 판정 + 보고서 생성
- `docs/M12_R0_DECISION_REPORT.md` (자동 생성) — Setup 별 7기준 + 운영자 옵션 A/B/C

### 갱신
- `docs/HANDOFF.md` — M12 결과 명시 (testnet smoke FAIL → DISABLED, 운영자 결정 대기)
- `CLAUDE.md` TIER 1 #7 — M11 실측 결과 추가 (트리거 명시 + 운영자 결정 대기)
- `docs/REFACTOR_M12_REPORT.md` — 본 파일

## 2. M11 실측 결과 → M12 자동 판정

### Setup Registry 현재 상태

| setup_id | status | n | PF | top3_excl_PF | 판정 |
|---|---|---|---|---|---|
| `1d_tsmom_donchian_long_v1` | **DISABLED** | 4 | 0.401 | 0.0 | **FAIL (1/7)** |

### 7기준 상세 (`1d_tsmom_donchian_long_v1`)

| 기준 | 측정값 | 통과 |
|---|---|---|
| n ≥ 200 | 4 | ❌ |
| Net PF ≥ 1.25 | 0.401 | ❌ |
| Expectancy_R > 0 | -0.237 | ❌ |
| avg_win/loss ≥ 1.5 | 1.204 | ❌ |
| MDD ≤ 25% | 27.10% | ❌ |
| Single symbol < 25% | 0.56% | ✅ |
| Top-3 excluded PF ≥ 1.0 | 0.0 | ❌ |

## 3. 운영자 결정 옵션 (M12 보고서 §3 정합)

본 보고서 (`docs/M12_R0_DECISION_REPORT.md`) 의 운영자 결정 트리:

### ❌ 모든 setup DISABLED — 선택지

**옵션 A (권장) — 데이터 확장 후 재실행**:
```bash
# mainnet 데이터 read-only (USE_TESTNET=false 임시) + 18~50 종목 + 2~5년
USE_TESTNET=false python scripts/run_m11_backtest.py --backfill --universe-size 18 --years 3
```
- 주의: USE_TESTNET=false 면 실거래 키 활성화 — *backfill 명령 동안만 임시*. 직후 USE_TESTNET=true 복원.
- 표본 확보 (n ≥ 200) 가능성 ↑

**옵션 B — 청사진 §7.5 Stage 3 진입**:
- A. Manual semi-discretionary 전환
- B. Buy-and-hold + 50d MA cash exit (Grayscale 2023)
- C. 운영자 가설 청취 (다른 archetype)

**옵션 C — HANDOFF "알파 영구 중단" 재확정**:
- 2026-05-23 결정 (9개 전략군 모두 fail) 유지
- 본 리팩토링의 가치 = *거버넌스 인프라* (M0~M11) 완성 인정
- 알파 추구는 *영구* 중단

## 4. M0~M12 종합 요약

| 마일스톤 | commit | 핵심 | pytest |
|---|---|---|---|
| M0 | e795460 | CLAUDE.md 4→6 + TIER 1 #9 완화 + SPEC E-10 | 718 |
| M0.5 | dfaf368 | 대량 동기화 (72파일) | 718 |
| M1 | 433be52 | ALCOA+ + KillSwitch 어댑터 | 768 (+50) |
| M2 | e9293c3 | Strategy Skill 3개 + Registry + 7기준 | 822 (+54) |
| M3 | 9821a96 | 5-Agent + Orchestrator + 7→5 매핑 | 865 (+43) |
| M4 | 37e1c58 | KillSwitch 자동 + Stage 3 + 2-tier | 899 (+34) |
| M5 | 23edbe4 | Slack MCP + Binance MCP shadow | 929 (+30) |
| M6 | 4c0f316 | Hooks (PowerShell) + 종합 검증 | 929 |
| M7 | 0dad5a6 | main_7590 shadow 통합 + Phase 1.5 prompt | 940 (+11) |
| M8 | ecd4509 | Supabase 폐기 + 로컬 only | 940 |
| **M9** | (dry-run) | **Testnet 봇 가동 검증** | 940 |
| M10 | a862e24 | Streamlit + Plotly 6 페이지 | 967 (+27) |
| M11 | c248b7a | Testnet 백테스트 7기준 실측 (smoke) | 989 (+22) |
| **M12** | (예정) | **결과 검토 + 운영자 결정 대기** | 989 |

**누적 신규 파일**: ~190+ / **변경 라인 (기존)**: ~225줄 / **pytest 989 passed** (회귀 0)

## 5. 거버넌스 인프라 완성 (변하지 않은 가치)

- ✅ ALCOA+ 9원칙 (audit_log chain 무결성, SQLite trigger 차단)
- ✅ KillSwitch 어댑터 (file OR btc_is_halted, 자동 6조건, Stage 3)
- ✅ 5-Agent Subagent (Strategy/Risk/Execution/Data/Ops, RiskManager 7→5 매핑)
- ✅ Strategy Skill 3개 (DailyTSMOM + CandidateContext + SinglePositionRotation)
- ✅ Setup Registry + 7기준 게이트 (top3_excluded_pf 핵심 — ZEC 단일 행운 방어)
- ✅ Slack MCP + Binance MCP shadow + Outbox (fail-soft)
- ✅ Hooks PowerShell (보호 파일 차단 + py_compile + 위험 키워드)
- ✅ Supabase 폐기 (로컬 only)
- ✅ Streamlit 대시보드 6 페이지 (localhost:8501, 5초 캐시)
- ✅ 989 pytest passed (M0 기준선 718 + 271 신규, 회귀 0)

## 6. 다음 단계 (운영자 결정 후)

1. **옵션 A 선택 시**: mainnet 데이터로 M11 재실행 → 7기준 통과 시 R0_QUALIFIED → Phase 1.5 진입
2. **옵션 B 선택 시**: Stage 3 회의 → archetype 재고
3. **옵션 C 선택 시**: 본 리팩토링 *최종 완료* 인정 → 알파 추구 영구 중단

추가 작업 (운영자 결정 후):
- `audit_log.SETUP_STATUS_CHANGE` 이벤트 (운영자 결정 기록)
- `docs/HANDOFF.md` 최종 갱신 (옵션 선택 결과)
- main → refactor 브랜치 머지 (운영자 직접)

## 7. 청사진 § 매핑 (M12)

| M12 산출물 | 청사진 § |
|---|---|
| 7기준 자동 판정 | §6.5 + 운영자 권장 7기준 + HANDOFF A2-② |
| Stage 3 옵션 A/B/C | §7.5 (90d -10% / MDD -25% 백스톱) |
| audit_log SETUP_STATUS_CHANGE | §6.4 + EventType 16종 |
| 운영자 결정 트리 | §10.5 비상 절차 + Stage 3 |
