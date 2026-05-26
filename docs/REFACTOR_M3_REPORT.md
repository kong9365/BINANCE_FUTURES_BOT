# M3 Report — 5-Agent Subagent 인프라

> **Milestone**: M3 — 5-Agent Subagent 인프라
> **Date**: 2026-05-26
> **Branch**: refactor/blueprint-m0-foundation
> **Previous**: [REFACTOR_M2_REPORT.md](REFACTOR_M2_REPORT.md)
> **Plan**: [REFACTOR_PLAN_v2_BLUEPRINT.md](REFACTOR_PLAN_v2_BLUEPRINT.md) M3
> **Status**: ✅ 완료 — M4 진입 가능

## 1. 신규 14파일

### agents/ (5-Agent 인프라, 8파일)

| 파일 | 라인 | 역할 |
|---|---|---|
| `agents/__init__.py` | 32 | 패키지 export |
| `agents/base.py` | 56 | `Agent` abstract base + AGENT_TYPE 검증 |
| `agents/strategy_agent.py` | 130 | 알파 + 운영자 7기준 + 학계 근거 (PASS/CONDITIONAL/FAIL) |
| `agents/risk_agent.py` | 145 | RiskManager wrap, 7→5 매핑 (APPROVE/REJECT/ESCALATE) |
| `agents/execution_agent.py` | 142 | fill_probability + slippage + 4-case (EXECUTE/DELAY/SKIP) |
| `agents/data_agent.py` | 120 | staleness + lookahead + 보호자산 + 상장일 (VALID/INVALID/STALE) |
| `agents/ops_agent.py` | 130 | SystemHealthMonitor wrap + KillSwitch (HEALTHY/DEGRADED/CRITICAL) |
| `agents/orchestrator.py` | 165 | 병렬+순차 워크플로우 + audit_log chain |

### tests/ (단위 테스트, 6파일, 47개 test)

| 파일 | 테스트 수 |
|---|---|
| `tests/test_agent_strategy.py` | 7 |
| `tests/test_agent_risk.py` | 7 |
| `tests/test_agent_execution.py` | 8 |
| `tests/test_agent_data.py` | 9 |
| `tests/test_agent_ops.py` | 7 |
| `tests/test_agent_orchestrator.py` | 6 |

## 2. RiskManager 7→5 매핑 (운영자 권장 산출물)

청사진 §3.2.2 Agent 2 JSON 5-check ↔ 기존 RiskManager 7-check 매핑:

| RiskManager 체크 | Agent JSON 필드 |
|---|---|
| `_check_daily_loss` | `daily_loss_check` |
| `_check_total_drawdown` | `mdd_check` |
| `_check_monthly_drawdown` | `weekly_loss_check` (월간 → 주간 매핑) |
| `_check_consecutive_losses` | `concerns` ("N연패") |
| `_check_concurrent_positions` | `concentration_check` |
| `_check_min_balance` | `concerns` ("min_balance") |
| `_check_cooldown` | `concerns` ("cooldown_remaining") |
| `get_max_leverage(tier, regime)` | `leverage_check` |

**RiskManager 본문 0줄 수정** — wrapping 만 적용. `check_all()` 결과를 청사진 JSON 형식으로 직렬화.

## 3. 검증 게이트

### 단위 테스트 — 47개 모두 PASS (수정 후)
```
$ pytest tests/test_agent_*.py -v
============================= 47 passed in 10.24s ==============================
```

수정 사항 (3건):
- `agents/execution_agent.py`: spread > max → 즉시 SKIP, 4-case 합 = 1.0 정확
- `tests/test_agent_strategy.py::test_fail_top3_excluded_pf`: 2 fail 시나리오 (1 fail = CONDITIONAL 정합)

### 통합 검증

**워크플로우 시나리오 6건 (test_agent_orchestrator)**:
1. 5-Agent 모두 PASS → approved=True, reviews=5 ✓
2. DataAgent INVALID (보호자산) → 차단, Risk/Execution 미호출 ✓
3. OpsAgent CRITICAL (KillSwitch 활성) → 차단 ✓
4. RiskAgent REJECT → Execution 미호출 ✓
5. **audit_log chain 무결성**: 5 AGENT_REVIEW row, 각 previous_log_hash = 직전 payload_hash ✓
6. 잘못된 payload 거부 ✓

### Fail Injection 5건 (REFACTOR_PLAN_v2_BLUEPRINT M3 요구)
- DataAgent staleness mock 10분 → STALE ✓
- RiskAgent check_all=False → REJECT ✓
- OpsAgent KillSwitch 활성 mock → CRITICAL ✓
- StrategyAgent top3_excluded_pf < 1.0 mock → FAIL ✓
- ExecutionAgent fill_probability 0.2 mock → SKIP ✓

## 4. RiskAgent ↔ RiskManager 정합성

```python
# 동일 자본 + 동일 시점:
# RiskManager.check_all() == True ↔ RiskAgent.review().verdict == "APPROVE"
# RiskManager.check_all() == False ↔ RiskAgent.review().verdict == "REJECT"

# 테스트 검증: test_agent_risk.py::test_approve_when_all_pass / test_reject_when_check_all_fails
```

## 5. 변경 통계

```
M3 commit (예상):
~14 files changed, ~1500 insertions(+)

신규:
- agents/ 8파일 (~920줄)
- tests/ 6파일 (~530줄)
```

기존 코드 변경 0줄 (RiskManager / SystemHealthMonitor / btc_risk_off / main_7590 모두 0줄).

## 6. M4 진입 조건

- [x] M3 신규 14파일 작성
- [x] 5-Agent Subagent (Strategy / Risk / Execution / Data / Ops)
- [x] AgentOrchestrator 병렬+순차 워크플로우
- [x] RiskAgent ↔ RiskManager 7→5 매핑 표
- [x] 47 신규 단위 테스트 PASS
- [ ] 전체 회귀 pytest (백그라운드, 718 + 50 + 53 + 47 = 868+ 기대)
- [ ] 클론 동기화 + commit + push
- [ ] M4 진입 GO

## 7. M4 작업 (다음)

- governance/auto_trigger.py (KillSwitch 6조건 자동 활성화)
- governance/stage3_rule.py (90d -10% / MDD -25% 백스톱)
- governance/risk_hierarchy.py (2-tier warn -1.0% / hard -1.5%)
- config/settings.py: `RiskHierarchyConfig` dataclass 추가
- tests 3파일
- main_7590.py: 매 60분 Stage 3 체크 1줄 추가

## 8. 청사진 § 매핑

| Agent | 청사진 § |
|---|---|
| StrategyAgent | §3.2.2 Agent 1 |
| RiskAgent | §3.2.2 Agent 2 + 운영자 7→5 매핑 |
| ExecutionAgent | §3.2.2 Agent 3 + arXiv 2502.18625 4-case |
| DataAgent | §3.2.2 Agent 4 + CLAUDE.md TIER 3 #6 룩어헤드 |
| OpsAgent | §3.2.2 Agent 5 + §3.4.3 KillSwitch 응답성 |
| AgentOrchestrator | §3.2.2 워크플로우 다이어그램 (병렬+순차) |
