# M2 Report — Strategy Skill 3개 + Setup Registry + 7기준 게이트

> **Milestone**: M2 — Strategy Skill 3개 + Setup Registry + 7기준 게이트
> **Date**: 2026-05-26
> **Branch**: refactor/blueprint-m0-foundation
> **Previous**: [REFACTOR_M1_REPORT.md](REFACTOR_M1_REPORT.md)
> **Plan**: [REFACTOR_PLAN_v2_BLUEPRINT.md](REFACTOR_PLAN_v2_BLUEPRINT.md) M2
> **Status**: ✅ 완료 — M3 진입 가능

## 1. 신규 15파일

### skills/ (Strategy Skill 추상화 + 3개 wrap, 5파일)

| 파일 | 라인 | 역할 |
|---|---|---|
| `skills/__init__.py` | 19 | 패키지 export |
| `skills/base.py` | 105 | `StrategySkill` abstract base + PARAMS_HASH 자동 계산 |
| `skills/daily_tsmom_donchian_skill.py` | 140 | 1d Donchian 돌파 (기존 strategy/breakout.py 재사용) |
| `skills/candidate_context_skill.py` | 142 | Macro / OI / Regime 차단 (TIER 1 #10 정합 — 직접 거래 결정 X) |
| `skills/single_position_rotation_skill.py` | 152 | EV 1위 자동 선정 + 직전 1위 cooldown (청사진 §3.2.3) |

### registry/ (Setup Registry, 2파일)

| 파일 | 라인 | 역할 |
|---|---|---|
| `registry/__init__.py` | 11 | 패키지 export |
| `registry/setup_registry.py` | 235 | DB 기반 CRUD (register / update_metrics / set_status / get_active_setups) |

### backtesting/ (7기준 검증 게이트, 2파일)

| 파일 | 라인 | 역할 |
|---|---|---|
| `backtesting/signal_validation.py` | 197 | 운영자 권장 7기준 검증 + top3_excluded_pf (ZEC 단일종목 방어) |
| `backtesting/signal_validation_report.py` | 145 | 검증 + setup_registry 자동 update + 보고서 |

### tests/ (단위 테스트, 6파일, 53개 test)

| 파일 | 라인 | 테스트 수 |
|---|---|---|
| `tests/test_skills_base.py` | 100 | 7 (PARAMS_HASH 자동, 다른 PARAMS, abstract evaluate, _build_flat, _compute_raw_data_hash, dict 순서, 인스턴스 공유) |
| `tests/test_skills_daily_tsmom_donchian.py` | 130 | 9 (SETUP_ID, no signal, LONG, fields, consistency, SHORT, empty, params 공유, academic_ref) |
| `tests/test_skills_candidate_context.py` | 130 | 7 (SETUP_ID, no block, macro, oi_danger, high_vol, btc_risk_off, multiple, no LONG/SHORT) |
| `tests/test_skills_single_position_rotation.py` | 175 | 9 (SETUP_ID, EV 1위, position full, low confidence, negative EV, FLAT skip, previous cooldown, expired, empty, aggregator) |
| `tests/test_setup_registry.py` | 175 | 8 (register, idempotent, update_metrics, set_status, invalid status, unknown setup, unregistered, get_active, consistent params) |
| `tests/test_signal_validation_7_criteria.py` | 200 | 11 (모두 통과, n, PF, ZEC 패턴, trend win rate, mean_rev win rate, MDD, single, response_latency check/under, empty) |

## 2. 검증 게이트

### 단위 테스트 — 53개 모두 PASS (수정 후)
```
$ pytest tests/test_skills_base.py tests/test_skills_daily_tsmom_donchian.py \
         tests/test_skills_candidate_context.py tests/test_skills_single_position_rotation.py \
         tests/test_setup_registry.py tests/test_signal_validation_7_criteria.py -v
============================= 53 passed in 7.58s ==============================
```

수정 사항:
- `backtesting/signal_validation.py`: `total_positive_pnl == 0` 일 때 max() 빈 iterable 처리 (fail-safe)
- `tests/test_signal_validation_7_criteria.py::test_passes_all_7_criteria`: 종목별 50% win 균등 분배로 수정 (top3_excluded_pf 실제 정합)

### py_compile — 모든 M2 파일 OK
```
$ python -m py_compile skills/*.py registry/*.py backtesting/signal_validation*.py
All M2 files compile OK
```

### 운영자 권장 7기준 정확성 검증

**핵심 검증 — `test_zec_single_symbol_pattern_caught`**:
- ZEC 종목이 양수 PnL 의 80% 차지하는 데이터 → top3_excluded_pf 차단 (또는 single_symbol_max 차단)
- HANDOFF 2026-05-22 A2-② "top8 PF 2.20 → ZEC 단일 97% → 사실 PF≈1.0" 재발 방지
- 검증 통과

**추세 전략 정합성 — `test_trend_mode_low_win_rate_ok`**:
- win rate 40% (낮음) + 큰 승 / 작은 패 → trend mode OK
- mean_rev mode → 차단 (운영자 #문제3 수정 반영)

**M6 응답지연 — `test_response_latency_check`**:
- response_latency_p95_ms > 5000 → fail
- M6 라이브 측정 시 사용

## 3. 변경 통계

```
M2 commit (예상):
~15 files changed, ~1800 insertions(+)

신규:
- skills/ 5파일 (558줄)
- registry/ 2파일 (246줄)
- backtesting/ 2파일 (342줄)
- tests/ 6파일 (910줄)
```

main_7590.py 변경 0줄 (M2 shadow 통합은 M3 orchestrator 와 함께 진행 예정).

## 4. M3 진입 조건

- [x] M2 신규 15파일 작성
- [x] 운영자 7기준 검증 게이트 작동
- [x] top3_excluded_pf (ZEC 단일종목 방어) 검증 통과
- [x] StrategySkill abstract + 3개 구현
- [x] SetupRegistry DB CRUD
- [x] 53 신규 단위 테스트 PASS
- [x] py_compile 통과
- [ ] 전체 회귀 pytest (백그라운드 진행 중, 718 + 50 (M1) + 53 (M2) = 821 기대)
- [ ] 클론 동기화 + commit + push
- [ ] M3 진입 GO

## 5. M3 작업 (다음)

- 5-Agent Subagent 인프라 (Strategy / Risk / Execution / Data / Ops)
- RiskAgent ↔ RiskManager 7→5 매핑 표
- AgentOrchestrator (병렬 + 순차 워크플로우)
- main_7590.py shadow SignalDecision + orchestrator 통합 (~5줄)
- 12 신규 파일

## 6. 청사진 § 매핑

| 모듈 | 청사진 § |
|---|---|
| StrategySkill abstract | §3.2.1 |
| DailyTSMOMDonchianSkill | §3.2.1 사례 1, Han·Kang·Ryu 2023 |
| CandidateContextSkill | §3.1.4 + TIER 1 #10 |
| SinglePositionRotationSkill | §3.2.3 |
| SetupRegistry | §6.5 |
| 7기준 검증 (top3_excluded_pf) | HANDOFF A2-② + 운영자 권장 |
