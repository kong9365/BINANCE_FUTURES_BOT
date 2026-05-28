# REFACTOR M15 — 라이브 1d DailyTSMOM + shadow 공유게이트 통합

> **일자**: 2026-05-28 | **브랜치**: refactor/blueprint-m0-foundation
> **계기**: 운영자 "운영기록 확인" → Paper 1차 가동(11.5h) 분석 중 *3개 구조적 단절* 발견 → 아키텍처 검토 → M15 설계 → 구현

---

## 1. 배경 — Paper 1차 가동 분석에서 발견한 3개 단절

운영자가 5/27 23:17 직접 가동한 Paper 봇(testnet dry-run)이 ~11.5h 운영 후 중지됐고,
운영 기록 확인 결과 **5-Agent shadow review 가 0회 작동**. 원인 조사(아키텍처 검토) 결과:

| # | 단절 | 근거 |
|---|---|---|
| **1** | 라이브 루프에 1d 전략 부재 | `_iter` 분기는 `oi_surge`/`breakout(1h)` 뿐 — 둘 다 9개 실패 전략. DailyTSMOM(6/7 CONDITIONAL)은 백테스트 전용, 라이브 미연결 |
| **2** | shadow 가 oi_surge 경로에만 연결 | `run_shadow` 가 `_handle_signal`(oi_surge)에만 존재. breakout 은 `_execute_decision` 직접 호출 → shadow 우회 |
| **3** | shadow setup_id 하드코딩 | `run_shadow(..., setup_id="1d_tsmom_donchian_long_v1")` — 모든 candidate 를 1d_tsmom 으로 오라벨 |
| 부수 | setup_registry 메트릭 = seed값 | 등록값(n=250/PF=1.35/MDD=20%) ≠ M13 v2 실측(n=216/PF=1.260/MDD=9.92%) |

> 결론: Paper 1차 가동의 shadow 0회는 *버그가 아니라 검증 대상 전략(1d)이 라이브에 없어서* 발생.

---

## 2. 설계 (Plan agent + 운영자 결정)

| 결정 | 채택안 |
|---|---|
| 1d 연결 | **신규 `_scan_daily_tsmom` 분기** (breakout 1h 재사용 X — TIER1 #7 + registry 연결 유지) |
| shadow 위치 | `_handle_signal` → **`_execute_decision` 공유게이트** 이동 (3 전략 단일 지점) |
| O2 (운영자) | shadow 를 게이트 **앞**에서 실행 (모든 신호 리뷰 — 거버넌스 비교 가치) |
| setup_id | 신규 `run_shadow_for_decision(decision)` — skill 의 실제 setup_id 보존 |
| O1/O3/O4 | CONDITIONAL=set_status / MDD=9.92% / breakout 도 shadow 리뷰 (모두 권장 채택) |

---

## 3. 구현 (5 단계)

### M15-1 — registry 메트릭 갱신 (`scripts/m15_update_registry_metrics.py`)
- register(멱등) → update_metrics(M13 v2 실측) → set_status(CONDITIONAL)
- `data/bot.db` 적용 완료: **status=CONDITIONAL, n=216, PF=1.260, MDD=9.92%, top3=1.102**
- 테스트: `tests/test_m15_update_registry_metrics.py` (4)

### M15-2 — shadow_runner 확장 (`governance/shadow_runner.py`)
- 신규 `run_shadow_for_decision(decision, capital_snapshot)` — orchestrator.review_signal 직접 (변환 X)
- `run_shadow` 기본 setup_id: `"1d_tsmom_donchian_long_v1"` → `"shadow_unknown"` (하드코딩 제거)
- 테스트: `tests/test_shadow_runner.py` +6 (총 17)

### M15-3 — settings (`config/settings.py`)
- `StrategyConfig`: `daily_tsmom_interval="1d"`, `daily_tsmom_limit=250`
- `_resolve_active_strategy` 화이트리스트에 `"daily_tsmom"` 추가
- 테스트: `tests/test_breakout_live.py` +2

### M15-4 — main_7590 통합 (보호파일, 6 surgical edits)
1. import `DailyTSMOMDonchianSkill`
2. `__init__`: `self.daily_tsmom_skill = DailyTSMOMDonchianSkill()`
3. `_iter` 3-way 분기 (breakout / daily_tsmom / oi_surge)
4. `_handle_signal`: shadow 블록 제거 (게이트로 이동)
5. `_execute_decision`: `decision=None` kwarg + shadow 블록(게이트 앞, decision→for_decision / candidate→run_shadow(setup_tag))
6. 신규 `_scan_daily_tsmom` (룩어헤드 `candles[:-1]` 차단, skill.evaluate → _execute_decision)
- 테스트: `tests/test_main_scan_daily_tsmom.py`(5) + `tests/test_main_shadow_relocation.py`(5)

### M15-5 — 검증 + 문서
- **pytest 1011 통과** (989 → +22 신규, 회귀 0)
- **E2E smoke PASS**: `_scan_daily_tsmom` 전체 경로 → audit_log **1 SIGNAL_GENERATED + 5 AGENT_REVIEW + 1 SIGNAL_REVIEWED**, 모두 `1d_tsmom_donchian_long_v1` 라벨, orchestrator APPROVED

---

## 4. 검증 — 3개 단절 모두 해소 (E2E smoke)

```
=== audit_log (7 rows) ===
  SIGNAL_GENERATED: 1     ← 단절1 해소: 1d DailyTSMOM 라이브 실행
  AGENT_REVIEW: 5         ← 단절2 해소: shadow 가 daily_tsmom 경로에서 작동 (5 agent 전부)
  SIGNAL_REVIEWED: 1
  setup_id(s): {'1d_tsmom_donchian_long_v1'}   ← 단절3 해소: 정확한 라벨
[SMOKE] PASS
```

---

## 5. 운영자 작업 — Paper 재가동

```bash
# .env 추가/확인 (★ ACTIVE_STRATEGY 필수)
ACTIVE_STRATEGY=daily_tsmom
ENABLE_SHADOW_AGENTS=true
USE_TESTNET=true

# 봇 가동 (별도 터미널, 로그 파일 저장 권장)
python main_7590.py --dry-run --duration 1209600 >> logs/paper.log 2>&1
```

1d 봉 마감(UTC 00:00) 시점에만 신호 평가 → 후보 발생은 드묾(정상).
신호 발생 시 audit_log 에 1 GEN + 5 REVIEW + 1 REVIEWED 적재.

---

## 6. 발견된 후속 과제 (M16 후보)

- **orchestrator 가 `agent_reviews` 테이블에 미적재**: 5-Agent 리뷰는 audit_log(불변 체인)에는
  적재되나, 대시보드 page 2 + PAPER_OPERATION_GUIDE §2-2 가 읽는 `agent_reviews` 테이블에는
  기록되지 않음. `audit/agent_review.py:to_db_row()` 존재하나 orchestrator 가 호출 안 함.
  → Paper 운영 중 대시보드 5-Agent 페이지가 빈 것처럼 보일 수 있음(데이터는 audit_log 에 있음).
  M3/M10 설계 갭 — M15 3개 단절과 별개. 별도 task 로 분리.

---

## 7. 변경 파일

**신규**: `scripts/m15_update_registry_metrics.py`, `tests/test_m15_update_registry_metrics.py`,
`tests/test_main_scan_daily_tsmom.py`, `tests/test_main_shadow_relocation.py`, `docs/REFACTOR_M15_REPORT.md`

**수정**: `main_7590.py`(보호, 6 edit), `governance/shadow_runner.py`, `config/settings.py`,
`tests/test_shadow_runner.py`, `tests/test_breakout_live.py`, `docs/PAPER_OPERATION_GUIDE.md`

**DB**: `data/bot.db` setup_registry → CONDITIONAL + M13 v2 실측 (스크립트 적용)
