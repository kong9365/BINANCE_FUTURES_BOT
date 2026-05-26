# 리팩토링 마스터플랜 v2 — BINANCE_FUTURES_BOT 청사진 v1.0 적용 (영구 원본)

> **Plan ID**: blueprint-refactor-v2
> **Owner**: 혜리 (광동제약 QC2 팀장)
> **Created**: 2026-05-26
> **Status**: M0 실행 중
> **PC 로컬 캐시**: `C:\Users\jaeho\.claude\plans\c-users-jaeho-onedrive-desktop-cursor-b-iridescent-meerkat.md` (PC별 다름, 본 파일이 권위 원본)
> **GitHub origin**: kong9365/BINANCE_FUTURES_BOT — 멀티 PC 작업 시 본 파일 참조

---

## Context

### 청사진 + 운영자 결정

[SYSTEM_DESIGN_BLUEPRINT.md](../SYSTEM_DESIGN_BLUEPRINT.md) (2,601줄, v1.0) 은 *4-Layer 아키텍처 + 5-Agent Subagent 검토단 + ALCOA+ 데이터 인티그리티 + GMP IQ→MV→OQ→PQ + Phase 1~5 로드맵*. 본 plan은 이를 SPEC v3.1.1 의 안전 룰 (CapitalManager, RiskManager 7-layer, PairWhitelist 4겹) 보존 하에 점진 도입 (M0~M6).

### 운영자 결정 8건 (2026-05-26 확정)

| # | 항목 | 결정 |
|---|---|---|
| v1-1 | 범위 | M0~M6 (Phase 1 IQ+MV+PQ) |
| v1-2 → v2-2 | 임계값 hierarchy | **2-tier 단순화** (warn -1.0% / hard -1.5%) |
| v1-3 | 보호 자산 | **6개** (BTCUSDT/ETHUSDT/HOLOUSDT/CFXUSDT/LYNUSDT/INJUSDT) + CLAUDE.md 동기화 |
| v1-4 | MCP | Slack ON, Notion 보류, Binance MCP shadow opt-in, WebSocket 유지 |
| v2-1 | 리팩토링 의도 | **청사진 전체 재실행** (HANDOFF "알파 영구 중단" 재검토, TIER 1 #9 조건부 완화) |
| v2-3 | Strategy Skill | **DailyTSMOMDonchian + CandidateContext + SinglePositionRotation 3개** (OISurge 제외) |
| v2-4 | KillSwitch 통합 | 청사진 기준 + btc_risk_off 어댑터 (OR 결합) |
| 추가 | 백테스트 7기준 | n≥200 / Net PF≥1.25 / Expectancy_R>0 / avg_win/avg_loss≥1.5 / MDD≤25% / single_symbol<25% / **상위 3종목 제거 PF≥1.0** (M2/M6 게이트) + 응답지연<5s (M6 라이브) |

### 5문제 + 9발견 (v1 → v2 변경)

| # | 변경 | 결과 |
|---|---|---|
| 문제 2 | Risk hierarchy | 3-tier (warn/soft/hard) → **2-tier** (warn/hard) |
| 문제 3 | "시그널 적중률 >55%" 추세 부적합 | 운영자 권장 **7기준** 채택 |
| 문제 4 | OISurgeSkill | **제외** (HANDOFF #1 OI-급증 64조합 음의 기대값) |
| 문제 5 | KillSwitch 중복 (사용자 "HALT 파일") | **어댑터**: `KillSwitch.is_active() = file OR btc_is_halted`. btc_risk_off는 메모리 상태머신 (파일 아님) — 사용자 주장은 부분 부정확하나 의도는 합리적 |
| 발견 1 | HANDOFF "알파 영구 중단" 결정 | 운영자 결정 #v2-1: 재검토 + TIER 1 #9 조건부 완화 |
| 발견 2 | 1d 돌파 = ZEC 단일 97% | 7기준의 "상위 3종목 제거 PF≥1.0" 직접 방어 |
| 발견 3 | TIER 1 #9 ↔ DailyTSMOMDonchianSkill 충돌 | "재시도가 아니라 *재검증* (표본 확장 + 다중검정 보정)" 명시 |
| 발견 4 | 기준선 706 → 실제 718 | M0 측정 (2026-05-26) |
| 발견 5 | strategy/breakout.py timeframe-agnostic | DailyTSMOMDonchianSkill 은 evaluate_breakout 재사용, 별도 모듈 X |
| 발견 6 | settings.py 보호자산 이미 6개 | CLAUDE.md만 동기화 (코드 0줄) |
| 발견 7 | migrations 멱등 패턴 검증됨 | v3.2.0 동일 패턴 |
| 발견 8 | WebSocket/Binance MCP v1 모호 | v2-1: Slack ON + Binance MCP shadow opt-in, WS 유지 |
| 발견 9 | RiskAgent ↔ RiskManager 7→5 매핑 | M3 산출물 (매핑 표) |

---

## 핵심 원칙 (TIER 0)

1. **재사용 우선** — 기존 모듈 보존, adapter/decorator/wrapper. 변경 라인 ≤ 145줄
2. **각 단계 독립 검증** — pytest (기준선 718) + smoke + 운영자 GO
3. **롤백 가능** — git branch + 신규 파일 제거 + 멱등 마이그레이션
4. **점진 활성화** — `ENABLE_XXX` flag, 검증 전 *shadow mode*
5. **TIER 1 룰 정합** — 보호 파일 본문 0줄 수정 원칙. 조건부 hook 추가만 운영자 명시 승인 후

---

## 단계별 마일스톤 (M0~M6)

### M0 — 사전 정렬 + TIER 1 #9 완화 (1~2일)
- CLAUDE.md 4→6 보호자산 + TIER 1 #9 조건부 완화 + 청사진 §10.1 통합
- SPEC E-10 신규 (2-tier hierarchy)
- HANDOFF.md "재검토 시작" 갱신
- docs/REFACTOR_PLAN_v2_BLUEPRINT.md (본 파일) 영구화
- docs/REFACTOR_M0_BASELINE.md (운영자 결정 + 기준선 718)
- git branch `refactor/blueprint-m0-foundation`

**검증**: pytest 718 + protected_symbols 6 + 운영자 GO

### M1 — ALCOA+ 기반 + KillSwitch 어댑터 (1~2주, 신규 14파일)
- db/migrations/v3_1_2_to_v3_2_0.sql (6개 테이블 + SQLite trigger)
- audit/ (signal_decision, agent_review, audit_log, chain, audit_logger)
- governance/kill_switch.py (어댑터: file OR btc_is_halted)
- governance/preflight.py
- tests/test_* (5개)
- main_7590.py: KillSwitch.is_active() 체크 1줄 추가

**검증**: pytest 723+ / KillSwitch 어댑터 4시나리오 / ALCOA+ chain 무결성

### M2 — Strategy Skill 3개 + Setup Registry + 7기준 게이트 (2~3주, 신규 18파일)
- skills/base.py (StrategySkill abstract)
- skills/daily_tsmom_donchian_skill.py (strategy/breakout.py 재사용, 1d candles)
- skills/candidate_context_skill.py (Macro + OI + AI Research mock wrap)
- skills/single_position_rotation_skill.py (청사진 §3.2.3 EV 1위)
- registry/setup_registry.py
- backtesting/signal_validation.py (운영자 7기준 + top3_excluded_pf)
- backtesting/signal_validation_report.py
- tests/test_* (6개)

**검증**: pytest 729+ / 재현성 (params_hash) / 24h shadow / 7기준 게이트 (DailyTSMOM)

### M3 — 5-Agent Subagent 인프라 (1~2주, 신규 12파일)
- agents/{base, strategy_agent, risk_agent, execution_agent, data_agent, ops_agent, orchestrator}
- RiskManager 7→5 매핑 표
- tests/test_agent_* (6개)

**검증**: pytest 735+ / RiskAgent ↔ RiskManager 100% 정합 / 24h 5-Agent shadow / Fail injection 5건

### M4 — KillSwitch 자동 + Stage 3 + 2-tier (1주, 신규 8파일)
- governance/auto_trigger.py (6조건)
- governance/stage3_rule.py
- governance/risk_hierarchy.py (2-tier warn -1.0% / hard -1.5%)
- config/settings.py: RiskHierarchyConfig 추가
- tests/test_* (3개)

**검증**: pytest 738+ / 6 자동 활성화 시뮬레이션 / 2-tier 도달 가능성 / Stage 3 mock

### M5 — Slack MCP + Binance MCP shadow (1~2주, 신규 7파일) — Notion 보류
- mcp/binance_mcp.py (shadow opt-in)
- mcp/slack_mcp.py
- mcp/outbox.py
- governance/slack_command_handler.py (`/bot halt`)
- db/migrations/v3_2_0_to_v3_2_1.sql (slack_outbox, mcp_diff_log)
- tests/test_* (3개)

**검증**: pytest 741+ / Binance MCP shadow 1주 99%+ 일치 / Slack 60초 이내 Kill Switch / Outbox 복구

### M6 — Hooks + 응답지연 + 종합 검증 (1주, 신규 5파일)
- .claude/hooks/{pre_tool_use, post_tool_use, user_prompt_submit}.sh
- .claude/hooks/README.md
- .claude/settings.local.json 업데이트

**종합 검증**:
- A. pytest 731~743+ 100% 통과
- B. End-to-End shadow 1주 dry-run
- C. 운영자 7기준 + 응답지연<5s
- D. Kill Switch 시나리오 6건 (Slack /bot halt, Stage 3, OpsAgent CRITICAL, SSD unmount, btc_risk_off 어댑터, EXECUTE 차단)
- E. Hooks 작동 (보호 파일 차단, py_compile, 위험 키워드)
- F. ALCOA+ 9원칙 매트릭스
- G. REFACTOR_M6_FINAL_REPORT.md 자동 생성

**Phase 1.5 prompt 진입**: 모든 검증 통과 + 운영자 명시 GO

---

## 위험 + 완화

| 위험 | 영향 | 확률 | 완화 |
|---|---|---|---|
| HANDOFF "알파 영구 중단" 재검토 → 9개 실패 패턴 재발 | 자본 손실 | 중간 | M2 7기준 (top3_excluded_pf ≥ 1.0) 직접 방어 |
| RiskAgent wrap 시 RiskManager 회귀 | 치명적 | 낮음 | M3 본문 0줄 수정 + 정합성 매트릭스 검증 |
| KillSwitch 어댑터에서 btc_risk_off 자동 쿨다운 vs 파일 수동 해제 혼선 | 운영 혼란 | 중간 | M1 4시나리오 검증. 둘 OR / audit_log 기록 |
| TIER 1 #9 완화 후 9개 실패 누적 | HANDOFF 부담 | 중간 | 7기준 fail → 즉시 영구 비활성. setup_registry 자동 기록 (DSR 다중검정 보정) |
| 보호 파일 위배로 작업 차단 | 진행 불가 | 중간 | 모든 변경은 운영자 명시 승인 후 |
| M2 7기준 모두 fail | 진행 불가 | 중간 | Stage 3 옵션 A/B/C |
| -3% 임계값 -1.5% 자동 차단과 혼동 | 운영 혼란 | 낮음 | M0 문서 명시 + M4 시뮬레이션 |
| Binance MCP shadow diff 폭증 | 알림 노이즈 | 중간 | M5 shadow 1주 threshold 학습 |
| Strategy Skill 3개 → M2 일정 ↑ | 일정 지연 | 중간 | M2를 2~3주로 늘림. 각 Skill 독립 검증 |

---

## 재사용 매트릭스

| 모듈 | 경로 | 등급 | 변경 라인 |
|---|---|---|---|
| CapitalManager | data/capital_manager.py | KEEP 100% | 0 |
| RiskManager | trading/risk_manager.py | KEEP 100% | 0 (M3 wrap) |
| TradeExecutor | trading/executor.py (1,420줄) | KEEP 100% | 0 |
| ExitPlanController | trading/exit_plan.py | KEEP | 0 |
| PairWhitelist | strategy/pair_whitelist.py | KEEP 100% | 0 |
| btc_risk_off | strategy/btc_risk_off.py | KEEP 100% | 0 (M1 KillSwitch 어댑터) |
| 17개 strategy/* | strategy/ | KEEP | 0 (M2 breakout만 wrap, OISurge 제외) |
| DynamicPositionSizer | sizing/dynamic_sizer.py | KEEP | 0 |
| WeeklyGPTAnalyst | analytics/weekly_report.py | KEEP | 0 |
| MacroEventAnalyzer | analytics/macro_event_analyzer.py | KEEP | 0 (M2 CandidateContextSkill wrap) |
| SystemHealthMonitor | ops/system_health_monitor.py | KEEP | 0 (M3 OpsAgent wrap) |
| ShadowRecorder | analytics/shadow_mode.py | EXTEND | M2에서 SignalDecision 변환 |
| db/schema.sql | db/schema.sql | KEEP | 0 (M1/M5 마이그레이션) |
| main_7590.py | main_7590.py (1,468줄) | EXTEND | ~25줄 (M1 1 + M2 5 + M3 5 + M4 1 + M5 5) |
| config/settings.py | config/settings.py (461줄) | EXTEND | ~25줄 (RiskHierarchyConfig, KillSwitchConfig, AuditConfig, AgentConfig) |
| CLAUDE.md | 루트 | UPDATE | ~40줄 (M0 완료) |
| docs/HANDOFF.md | docs/ | UPDATE | M0 ~20줄 (재검토 명시) |
| docs/SPEC_v3.1_APPENDIX_E.md | docs/ | UPDATE | M0 ~80줄 (E-10 2-tier hierarchy) |

**합계 변경 라인 (기존)**: ~145줄
**신규 파일**: ~66개
**신규 테스트**: ~25개 → 총 ~743 (M0 기준선 718에서)

---

## 청사진 § 매핑

| Milestone | 청사진 § | SPEC v3.1.1 § |
|---|---|---|
| M0 | §10.1 #2, §7.4 | §9, 부록 E-3, E-10 (신규) |
| M1 | §3.4.3, §6.1, §6.4, §7.2, §3.2.1 | §13, 부록 E-7 |
| M2 | §3.2.1, §6.5, §3.2.3 | §8 |
| M3 | §3.2.2, §6.3 | §9, §8-6 |
| M4 | §3.4.3 6조건, §7.4, §7.5 Stage 3 | §9 (-1.5% 유지) |
| M5 | §3.1.2, §3.4.2, §7.2 | (신규) |
| M6 | §3.4.1, §10.3, §10.4 | TIER 1 #2 |

---

## GitHub 중심 작업 워크플로우 (멀티 PC)

### 환경 구조
- **작업본** (이 PC, OneDrive): `C:\Users\jaeho\OneDrive\Desktop\Cursor\BINANCE_FUTURES_BOT` — git 아님, 코드 수정·실행
- **git 클론** (이 PC): `C:\Users\jaeho\OneDrive\Desktop\Cursor\BINANCE_FUTURES_BOT_GITHUB` — origin = kong9365/BINANCE_FUTURES_BOT
- **수집 전용 클론** (이 PC): `C:\bots\BINANCE_FUTURES_BOT` — Windows Task 데몬용
- **다른 PC**: `git clone` + `.env` 수동 복사

### 세션 시작 (모든 PC 공통)
1. `git -C ...GITHUB pull origin main` (또는 작업 branch)
2. 클론 변경분 → 작업본 명시 복사 (또는 작업본 = 클론)
3. `pytest tests/ -q` (기준선 확인, M0=718)
4. `docs/HANDOFF.md` + `docs/REFACTOR_M<N-1>_REPORT.md` 확인
5. 현재 마일스톤 상태 파악

### 세션 종료 (마일스톤 완료 시)
1. `pytest tests/ -q` 통과 확인
2. `docs/REFACTOR_M<N>_REPORT.md` 자동 생성
3. `docs/HANDOFF.md` 갱신
4. 작업본 → 클론 명시 복사 (A안)
5. 클론에서 `git status` + `pytest`
6. `git add <명시 파일>` (`-A` 금지, TIER 1 #4)
7. `git -c user.name="kong9365" -c user.email="jaehong9365@gmail.com" commit -m "..."` (Co-Authored-By 포함)
8. `git push origin <branch>`
9. 운영자 보고 + 다음 PC 시작 명령

### 운영자 "GitHub 최신 확인하고 다음 작업 진행해" 명령 패턴
1. `git fetch` + `git status` + `git log -5 --oneline`
2. `git pull` (충돌 없으면)
3. 작업본 diff 확인
4. `docs/HANDOFF.md` + `docs/REFACTOR_M<N>_REPORT.md` 최신 read
5. 현재 마일스톤 진행 상태 파악
6. 다음 작업 명시 + 운영자 GO 후 진행

### 보안 절대 룰
- 절대 커밋 금지: `.env`, `.env.SAFETY_BACKUP`, `logs/`, `reports/`, `data/*.db`, `backtests/cache/`, 키/토큰
- `git add .` / `-A` 절대 금지 — 명시 파일만
- 커밋 메시지에 시크릿 노출 금지

---

## 끝

본 plan v2는 운영자 5문제 + 9발견 반영 + GitHub 중심 워크플로우 통합. M0부터 단계별 검증하며 무한 진행 (운영자 명시 승인 2026-05-26).
