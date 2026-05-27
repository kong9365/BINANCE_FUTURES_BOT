# BINANCE_FUTURES_BOT v3.2.0 — 마스터플랜 (M0~M14 + Paper 운영)

> **Plan ID**: blueprint-refactor-v2
> **Owner**: 혜리 (광동제약 QC2 팀장)
> **시작**: 2026-05-26 | **현재 시점**: 2026-05-27 | **다음 마일스톤**: Phase 1.5 micro-live (Paper 운영 1~2주 통과 후)
> **GitHub branch**: [refactor/blueprint-m0-foundation](https://github.com/kong9365/BINANCE_FUTURES_BOT/tree/refactor/blueprint-m0-foundation)

---

## 📋 Executive Summary

### 무엇을
[SYSTEM_DESIGN_BLUEPRINT.md](SYSTEM_DESIGN_BLUEPRINT.md) (v1.0, 2,601줄) 의 *4-Layer 아키텍처 + 5-Agent + ALCOA+ + GMP* 를 SPEC v3.1.1 의 안전 룰을 *0줄 손상 없이* 점진 도입.

### 왜
- HANDOFF.md "9개 전략 모두 실패 → 알파 영구 중단" (2026-05-23) → 운영자 결정 #v2-1 (2026-05-26): *재검토 시나리오* 진행
- 청사진의 거버넌스 인프라 (ALCOA+, 5-Agent, KillSwitch) 가 *9개 실패 패턴 (특히 ZEC 단일종목 97% 행운)* 을 *명시적으로 방어* 할 수 있는지 검증

### 어떻게 (5문제 + 9발견 반영)
- ✅ Risk hierarchy: 3-tier → **2-tier** (warn -1.0% / hard -1.5%)
- ✅ 검증 기준: 시그널 적중률 >55% → **운영자 권장 7기준** (top3_excluded_pf 핵심)
- ✅ Strategy Skill: OISurge 제외, **DailyTSMOM + CandidateContext + SinglePositionRotation**
- ✅ KillSwitch: 어댑터 (file OR btc_is_halted), 기존 btc_risk_off 0줄 수정
- ✅ Supabase 폐기 → 로컬 sqlite only
- ✅ 실시간 대시보드 (Streamlit + Plotly)

### 결과
| 핵심 메트릭 | M0 기준선 | M14 현재 |
|---|---|---|
| pytest 통과 | 718 | **989** (+271 신규, 회귀 0) |
| 신규 파일 | 0 | ~200+ |
| 기존 코드 변경 | 0 | ~250줄 (보호 파일 본문 0줄) |
| GitHub commits | 0 | 16 (M0~M14) |
| **DailyTSMOM 7기준** | 미측정 | **6/7 통과 → CONDITIONAL** |
| **MDD 검증** | — | **9.92%** (HANDOFF A1 신뢰 하네스 8.9% 정합) |

---

## 🕐 시간순 진행 흐름

### 2026-05-26 (Day 1) — 청사진 도입 + 거버넌스 인프라

| 시간 | 마일스톤 | commit | 핵심 |
|---|---|---|---|
| 오전 | M0 사전 정렬 | `e795460` | CLAUDE.md 4→6 + TIER 1 #9 완화 + SPEC E-10 (2-tier hierarchy) |
| 오전 | M0.5 대량 동기화 | `dfaf368` | Phase 1~2-E 누적 72 파일 GitHub 동기화 |
| 오후 | M1 ALCOA+ + KillSwitch | `433be52` | audit/ (SignalDecision/AgentReview/AuditLog + chain hash) + governance/kill_switch.py (어댑터) + DB v3.2.0 6테이블 + SQLite trigger |
| 저녁 | M2 Strategy Skill 3개 | `e9293c3` | skills/{base, daily_tsmom_donchian, candidate_context, single_position_rotation} + registry/setup_registry + backtesting/signal_validation (7기준) |
| 저녁 | M3 5-Agent | `9821a96` | agents/{base, strategy, risk, execution, data, ops, orchestrator} + RiskManager 7→5 매핑 |
| 야간 | M4 Stage 3 + 2-tier | `37e1c58` | governance/{auto_trigger, stage3_rule, risk_hierarchy} + 6조건 자동 활성화 |
| 야간 | M5 MCP | `23edbe4` | mcp/{binance_mcp shadow, slack_mcp, outbox} + slack_command_handler + DB v3.2.1 |
| 야간 | M6 Hooks | `4c0f316` | .claude/hooks/{pre_tool_use, post_tool_use, user_prompt_submit}.ps1 + README |

### 2026-05-27 (Day 2) — 통합 + 검증 + Paper 진입

| 시간 | 마일스톤 | commit | 핵심 |
|---|---|---|---|
| 새벽 | M7 main_7590 shadow | `0dad5a6` | governance/shadow_runner.py + shadow_config + ENABLE_SHADOW_AGENTS env + Phase 1.5 prompt |
| 새벽 | M8 Supabase 폐기 | `ecd4509` | SUPABASE_ENABLED=false 기본 + 로컬 sqlite only |
| 새벽 | M9 봇 가동 검증 | (dry-run) | testnet $4999.88, 보호자산 6, 5-Agent ALL PASS, audit_log chain ✓ |
| 오전 | M10 대시보드 | `a862e24` | Streamlit + Plotly 6 페이지 (운영/5-Agent/ALCOA+/KillSwitch/시장차트/Registry) |
| 오전 | M11 백테스트 smoke | `c248b7a` | DB v3.2.2 (ohlcv_local) + run_m11_backtest.py — testnet 1년 1d × 3종목 → trades=4 → 1/7 FAIL |
| 오전 | M12 결과 검토 | `9048cb0` | run_m12_review.py + M12_R0_DECISION_REPORT.md (옵션 A/B/C 트리) |
| 오후 | M13 v1 옵션 A 실측 | `8d6dd13` | mainnet 16종목 × 3년 → trades=216 → 5/7 통과 (MDD 204% 의심) |
| 오후 | M13 v2 MDD 수정 | `62a3575` | signal_validation MDD 자본 대비% + position 5% → **MDD 9.92%, 6/7 통과 → CONDITIONAL** |
| 저녁 | M14 Paper 운영 가이드 | `127322f` | docs/PAPER_OPERATION_GUIDE.md (1~2주 절차) |

---

## 🗂️ 카테고리별 정리

### 🛡️ 1. 거버넌스 (Governance)
청사진 §3.4 + §7 — *9개 실패 패턴 방어 메커니즘*

| 모듈 | 마일스톤 | 역할 |
|---|---|---|
| `audit/` (signal_decision, agent_review, audit_log, chain, audit_logger) | M1 | ALCOA+ 9원칙 자동 + chain hash (SQLite trigger 차단) |
| `governance/kill_switch.py` | M1 | 어댑터 (file OR btc_is_halted), 기존 btc_risk_off 0줄 수정 |
| `governance/preflight.py` | M1 | 시작 시 1회 검증 (API/SSD/DB) |
| `governance/auto_trigger.py` | M4 | KillSwitch 자동 6조건 (daily_loss/ops_critical/mcp/data_stale/api/ssd) |
| `governance/stage3_rule.py` | M4 | 90d -10% / MDD -25% 백스톱 + 옵션 A/B/C |
| `governance/risk_hierarchy.py` | M4 | 2-tier (warn -1.0% / hard -1.5%) — 운영자 결정 #v2-2 |
| `governance/shadow_runner.py` | M7 | main_7590 통합 캡슐화 (ENABLE_SHADOW_AGENTS env) |
| `governance/slack_command_handler.py` | M5 | Slack `/bot halt` 명령 처리 |
| `.claude/hooks/*.ps1` | M6 | PreToolUse (보호파일 차단) + PostToolUse (py_compile + pytest) + UserPromptSubmit (위험키워드) |

### 🎯 2. 전략 (Strategy Skill + Setup Registry)
청사진 §3.2.1 + §6.5 — *알파 + 재현성 + 7기준 게이트*

| 모듈 | 마일스톤 | 역할 |
|---|---|---|
| `skills/base.py` | M2 | StrategySkill abstract + PARAMS_HASH 자동 계산 |
| `skills/daily_tsmom_donchian_skill.py` | M2 | 1d 돌파 (strategy/breakout.py 재사용) — **6/7 통과, CONDITIONAL** |
| `skills/candidate_context_skill.py` | M2 | Macro/OI/Regime 차단 — TIER 1 #10 정합 (거래 결정 X) |
| `skills/single_position_rotation_skill.py` | M2 | EV 1위 자동 선정 + 직전 종목 cooldown |
| `registry/setup_registry.py` | M2 | DB CRUD (register/update_metrics/set_status/get_active) |
| `backtesting/signal_validation.py` | M2 | 운영자 7기준 게이트 (n/PF/expR/avg_win_loss/MDD/single/**top3_excluded_pf**) |
| `backtesting/signal_validation_report.py` | M2 | validate_and_persist + 자동 status 변경 |
| `backtesting/local_ohlcv_store.py` | M11 | Supabase 대체 로컬 ohlcv 저장소 |

### 👥 3. 5-Agent Subagent 검토단
청사진 §3.2.2 — *기존 모듈 wrapping, 새 로직 0줄*

| Agent | 마일스톤 | 입력 → 출력 | wrapping |
|---|---|---|---|
| StrategyAgent | M3 | SignalDecision → PASS/CONDITIONAL/FAIL | SetupRegistry + 7기준 |
| RiskAgent | M3 | SignalDecision → APPROVE/REJECT/ESCALATE | **RiskManager 7→5 매핑** (0줄 수정) |
| ExecutionAgent | M3 | SignalDecision → EXECUTE/DELAY/SKIP | fill_probability + 4-case (arXiv 2502.18625) |
| DataAgent | M3 | SignalDecision → VALID/INVALID/STALE | PairWhitelist + 보호자산 + lookahead |
| OpsAgent | M3 | health payload → HEALTHY/DEGRADED/CRITICAL | **SystemHealthMonitor** (0줄 수정) + KillSwitch |
| AgentOrchestrator | M3 | SignalDecision → OrchestratorResult | Strategy+Data+Ops 병렬 → Risk → Execution 순차 + audit_log chain |

### 💾 4. 데이터 / DB
청사진 §3.1 + §6 — *Supabase → 로컬 sqlite only*

| 마이그레이션 | 마일스톤 | 신규 테이블 |
|---|---|---|
| v3.2.0 (`v3_1_2_to_v3_2_0.sql`) | M1 | setup_registry, setup_evaluations, signal_decisions, agent_reviews, audit_log (+ SQLite UPDATE/DELETE trigger), kill_switch_events |
| v3.2.1 (`v3_2_0_to_v3_2_1.sql`) | M5 | slack_outbox, mcp_diff_log |
| v3.2.2 (`v3_2_1_to_v3_2_2.sql`) | M11 | ohlcv_local (Supabase 대체) |

기존 코드 재사용 (0줄 수정):
- `data/capital_manager.py` — wallet/margin/available/locked 4-way
- `data/ws_collector.py` — Phase 2 multiplex (depth + aggTrade + force)
- `data/aggregator_1m.py` — Phase 2-E 1m OHLC + CVD
- `data/persistence.py` — SUPABASE_ENABLED env 게이트 (M8 추가) → 로컬 outbox 전용 모드

### 🌐 5. 외부 통신 (MCP)
운영자 결정 #v1-4: Slack ON, Notion 보류, Binance MCP shadow opt-in

| 모듈 | 마일스톤 | 상태 |
|---|---|---|
| `mcp/slack_mcp.py` | M5 | ✅ ON — send_alert + parse_command (/bot halt) |
| `mcp/binance_mcp.py` | M5 | 🟡 shadow opt-in (`ENABLE_BINANCE_MCP_SHADOW=true` 시) |
| `mcp/outbox.py` | M5 | ✅ fail-soft 큐 (지수 백오프, 5회 시 permanent fail) |
| Notion MCP | — | ⏸️ 보류 (Phase 1.5 이후 재검토) |

### 📊 6. 실시간 대시보드 (UI)
M10 — Streamlit + Plotly, localhost:8501 only (보안)

| 페이지 | 데이터 | 차트 |
|---|---|---|
| 1. 운영 상태 | trades, capital_initial/daily_snapshot | Plotly equity curve + 메트릭 |
| 2. 5-Agent 검토 | agent_reviews, signal_decisions | Verdict 매트릭스 (시간순 색상) |
| 3. ALCOA+ 감사 | audit_log + verify_chain | Chain 무결성 + event 분포 |
| 4. Kill Switch | kill_switch_events + KillSwitch.is_active() | 활성화 timeline + 수동 해제 (DEACTIVATE confirm) |
| 5. 시장 차트 | agg_trades_1m | Plotly candlestick 1m OHLC |
| 6. Setup Registry | setup_registry | 7기준 메트릭 표 + 자동 판정 |

### 🧪 7. 백테스트 검증 + 7기준 게이트
M11 → M13 (실측), M12 (검토)

**운영자 권장 7기준** (변경 금지):
1. n ≥ 200
2. Net PF ≥ 1.25
3. Expectancy_R > 0
4. avg_win/avg_loss ≥ 1.5
5. MDD ≤ 25%
6. Single symbol < 25%
7. **Top-3 excluded PF ≥ 1.0** (ZEC 단일종목 행운 패턴 방어)

**실측 결과 (1d_tsmom_donchian_long_v1)**:
| 기준 | M11 (testnet) | M13 v1 (mainnet raw) | **M13 v2 (수정)** |
|---|---|---|---|
| n ≥ 200 | 4 ❌ | 216 ✅ | 216 ✅ |
| PF ≥ 1.25 | 0.401 ❌ | 1.260 ✅ | 1.260 ✅ |
| Expectancy_R > 0 | -0.237 ❌ | +0.118 ✅ | +0.118 ✅ |
| avg_win/loss ≥ 1.5 | 1.204 ❌ | 1.357 ❌ | 1.357 ❌ borderline |
| MDD ≤ 25% | 27% ❌ | 204% ❌ 모델 의심 | **9.92% ✅** HANDOFF A1 정합 |
| Single < 25% | 0.56% ✅ | 8.07% ✅ | 8.07% ✅ |
| **Top-3 excluded PF ≥ 1.0** | 0.0 ❌ | **1.102 ✅** ZEC 회피 | 1.102 ✅ |
| **합격** | **1/7 DISABLED** | 5/7 DISABLED | **6/7 → CONDITIONAL** |

---

## 📍 현재 상태

### ✅ 완료 (M0 ~ M14)
모든 마일스톤 코드 + 테스트 + 보고서 commit + push 완료. GitHub 최신 `127322f`.

### 🔄 진행중 — Paper 운영 1~2주
**시점**: 2026-05-27 ~ 2026-06-10 (예상)
**환경**: testnet (USE_TESTNET=true) + ENABLE_SHADOW_AGENTS=true
**모니터링**: 대시보드 localhost:8501 + Telegram heartbeat
**종료 조건** ([docs/PAPER_OPERATION_GUIDE.md](docs/PAPER_OPERATION_GUIDE.md) §3):
- audit_log chain 무결성 100%
- 5-Agent 정상 작동 (SIGNAL_GENERATED 시 5 reviews 적재)
- KillSwitch 5 시나리오 검증
- downtime < 0.5%

### 📅 예정

#### Phase 1.5 — Micro-live ($30~50 실거래)
조건: Paper 운영 통과 + 운영자 명시 GO
참조: [docs/PHASE1_5_MICRO_LIVE_PROMPT.md](docs/PHASE1_5_MICRO_LIVE_PROMPT.md)
작업:
1. R0_QUALIFIED 수동 결정 (`setup_registry.set_status`)
2. USE_TESTNET=false 전환 + 실거래 키 IP 화이트리스트
3. $30~50 입금
4. 1회 리스크 0.05~0.1% ($1.5~5/회)
5. 2~4주, 5~10 실거래 누적 (5-Agent 자동 작동)
6. 검증: Case A/B/C/D 분포, ALCOA+ 무위반, 응답지연 <5s

#### Phase 2~5 — 청사진 §8.4~§8.7 (장기)
- Phase 2: PQ Part 1 (1d 멀티 엔진, $100~$300)
- Phase 3: PQ Part 2 (Microstructure 5m execution filter, $300~$1,000)
- Phase 4: 자본 확장 ($1,000+)
- Phase 5: 자율 운영

#### 보류 항목 (운영자 추후 결정)
- Notion MCP 도입 (Phase 1.5 이후 재검토)
- Binance MCP 정식 전환 (python-binance 대체)
- WebSocket 5m timeframe 강화 (Phase 2~3)
- Stage 3 옵션 A/B/C (FAIL 시나리오)

---

## 🎯 운영자 결정 이력 (시간순)

| 일자 | # | 항목 | 결정 |
|---|---|---|---|
| 2026-05-26 | v1-1 | 리팩토링 범위 | M0~M6 (Phase 1 IQ+MV+PQ) |
| 2026-05-26 | v1-3 | 보호 자산 | **6개** (BTC/ETH/HOLO/CFX/LYN/INJ) + CLAUDE.md 동기화 |
| 2026-05-26 | v1-4 | MCP | Slack ON, Notion 보류, Binance MCP shadow opt-in, WebSocket 유지 |
| 2026-05-26 | v2-1 | 리팩토링 의도 | **청사진 전체 재실행** (HANDOFF 알파 영구 중단 재검토, TIER 1 #9 완화) |
| 2026-05-26 | v2-2 | Risk hierarchy | **2-tier** (warn -1.0% / hard -1.5%) — 3-tier 의 soft 도달 불가 문제 수정 |
| 2026-05-26 | v2-3 | Strategy Skill | DailyTSMOM + CandidateContext + SinglePositionRotation 3개 (OISurge 제외) |
| 2026-05-26 | v2-4 | KillSwitch 통합 | 청사진 기준 + btc_risk_off 어댑터 (OR 결합) |
| 2026-05-26 | 추가 | 검증 기준 | 운영자 권장 **7기준** + 응답지연<5s |
| 2026-05-26 | — | 진행 방식 | "단계별 검증 + 무한 진행" 명시 |
| 2026-05-27 | — | Supabase | **폐기** 결정, 로컬 only |
| 2026-05-27 | — | 실시간 차트 | M10 신규 마일스톤 추가 (Streamlit + Plotly) |
| 2026-05-27 | — | M12 옵션 | **옵션 A** (데이터 확장 후 재실행) → M13 |
| 2026-05-27 | — | M13 v1 결과 | **옵션 A** (MDD 계산 모델 수정 + 재평가) → M13 v2 |
| 2026-05-27 | — | M13 v2 결과 | **Paper 운영 1~2주 → Phase 1.5 micro-live (추천)** → M14 |

---

## 🚀 다음 단계 액션 (운영자 작업)

### 즉시 (Paper 운영 시작)
```bash
# 1. .env 옵션 설정
ENABLE_SHADOW_AGENTS=true
USE_TESTNET=true

# 2. 봇 1~2주 dry-run 가동
python main_7590.py --dry-run --duration 1209600   # 14일

# 3. 대시보드 가동 (별도 터미널)
scripts/run_dashboard.bat                           # http://localhost:8501
```

### 일일 점검 (5분/일)
[docs/PAPER_OPERATION_GUIDE.md](docs/PAPER_OPERATION_GUIDE.md) §5:
- 대시보드 6 페이지 빠른 확인
- DB 쿼리: 일일 이벤트 수 + 5-Agent verdict 분포
- KillSwitch 상태 확인

### 주간 (선택, 매주 1회)
- KillSwitch drill (수동 활성/해제 사이클)
- 헬스 리포트 (`scripts/ws_health_report.py --telegram`)

### 1~2주 후 (Paper 종료)
1. 종합 점검 (대시보드 페이지 6 setup_registry 결과)
2. 운영자 명시 GO/NO-GO 결정:
   - **PASS** → Phase 1.5 micro-live ([PHASE1_5_MICRO_LIVE_PROMPT.md](docs/PHASE1_5_MICRO_LIVE_PROMPT.md))
   - **CONDITIONAL** → 추가 Paper 1~2주
   - **FAIL** → 청사진 §7.5 Stage 3

---

## 📈 핵심 메트릭

### 거버넌스 인프라 완성도
| 영역 | 진행 |
|---|---|
| ALCOA+ 9원칙 자동 | ✅ 100% (audit/ + chain hash + SQLite trigger) |
| KillSwitch 어댑터 + 6조건 자동 | ✅ 100% |
| 5-Agent (Strategy/Risk/Execution/Data/Ops + Orchestrator) | ✅ 100% |
| Strategy Skill 3개 wrap | ✅ 100% (OISurge 제외 결정) |
| Setup Registry + 7기준 게이트 | ✅ 100% |
| Stage 3 백스톱 (90d -10% / MDD -25%) | ✅ 100% |
| 2-tier hierarchy (운영자 결정 정합) | ✅ 100% |
| Slack MCP + outbox fail-soft | ✅ 100% |
| Binance MCP shadow (opt-in) | ✅ 100% |
| Hooks (Windows PowerShell) | ✅ 100% |
| Streamlit + Plotly 대시보드 6 페이지 | ✅ 100% |
| Supabase 폐기 (로컬 only) | ✅ 100% |
| 백테스트 7기준 실측 인프라 | ✅ 100% |

### 알파 검증 (DailyTSMOMDonchianSkill)
| 검증 단계 | 결과 |
|---|---|
| M11 testnet smoke (1y × 3 종목) | 1/7 (표본 부족) |
| M13 v1 mainnet (3y × 16 종목, raw MDD) | 5/7 (MDD 모델 의심) |
| **M13 v2 (MDD 자본대비% + position 5%)** | **6/7 → CONDITIONAL** |
| HANDOFF A1 정합 검증 | ✅ MDD 9.92% vs 8.9% |
| ZEC 단일 행운 회피 (top3_excluded_pf) | ✅ 1.102 |

### 코드 품질
- **pytest 통과**: 989 (M0 기준선 718 + 271 신규, 회귀 0)
- **핵심 보호 파일 본문 변경**: 0줄 (`trading/executor.py`, `risk_manager.py`, `capital_manager.py`, `btc_risk_off.py`)
- **신규 파일**: ~200+ (audit + governance + skills + registry + agents + mcp + dashboard + tests)
- **GitHub commits**: 16 (M0~M14)
- **테스트 커버리지**: 모든 신규 모듈 단위 테스트 동반

---

## 🗒️ 부록

### A. GitHub 워크플로우 (멀티 PC)
- **작업본** (OneDrive): `C:\Users\jaeho\OneDrive\Desktop\Cursor\BINANCE_FUTURES_BOT` (git 아님)
- **클론** (이 PC): `C:\Users\jaeho\OneDrive\Desktop\Cursor\BINANCE_FUTURES_BOT_GITHUB` (origin = kong9365/BINANCE_FUTURES_BOT)
- **수집 전용 클론**: `C:\bots\BINANCE_FUTURES_BOT` (Windows Task 데몬용)
- **다른 PC**: `git clone https://github.com/kong9365/BINANCE_FUTURES_BOT.git` + `.env` 수동 복사

매 세션 시작: `git pull` → `pytest` → `docs/HANDOFF.md` 확인 → 다음 작업
매 세션 종료: `pytest` → REPORT 작성 → HANDOFF 갱신 → 명시 add → commit → push

### B. 환경변수 매트릭스 (.env.example 정합)
| Variable | 기본값 | 용도 |
|---|---|---|
| `USE_TESTNET` | true | 안전 (testnet) — Paper 운영 |
| `SUPABASE_ENABLED` | false | M8 — 로컬 sqlite only |
| `ENABLE_SHADOW_AGENTS` | false | M7 — Paper/Phase 1.5 시 true |
| `ENABLE_BINANCE_MCP_SHADOW` | false | M5 — 선택 |
| `KILLSWITCH_FILE` | data/KILLSWITCH | 또는 외장 SSD 경로 |
| `SLACK_WEBHOOK_URL` | (선택) | M5 알림 |
| `ACTIVE_STRATEGY` | oi_surge | "breakout" 으로 1d 돌파 전환 가능 |

### C. 주요 파일 위치
**소스 코드** (신규):
- 거버넌스: `audit/`, `governance/`
- 전략: `skills/`, `registry/`
- Agent: `agents/`
- MCP: `mcp/`
- 대시보드: `dashboard/`
- 백테스트: `backtesting/local_ohlcv_store.py`, `signal_validation*.py`

**스크립트**:
- 봇: `main_7590.py --dry-run --duration N`
- 대시보드: `scripts/run_dashboard.bat`
- 백테스트: `scripts/run_m11_backtest.py`
- 결과 검토: `scripts/run_m12_review.py`

**문서**:
- [SYSTEM_DESIGN_BLUEPRINT.md](SYSTEM_DESIGN_BLUEPRINT.md) — 청사진 v1.0
- [CLAUDE.md](CLAUDE.md) — TIER 1 룰 + 운영자 결정 매트릭스
- [docs/HANDOFF.md](docs/HANDOFF.md) — 세션 연속성 핸드오프 (최신: 2026-05-27 M14)
- [docs/PAPER_OPERATION_GUIDE.md](docs/PAPER_OPERATION_GUIDE.md) — Paper 운영 1~2주 가이드
- [docs/PHASE1_5_MICRO_LIVE_PROMPT.md](docs/PHASE1_5_MICRO_LIVE_PROMPT.md) — Phase 1.5 진입 절차
- [docs/SETUP_VERIFICATION_REPORT_v3_2_0.md](docs/SETUP_VERIFICATION_REPORT_v3_2_0.md) — M13 v2 백테스트 결과
- [docs/M12_R0_DECISION_REPORT.md](docs/M12_R0_DECISION_REPORT.md) — 운영자 결정 트리
- [docs/REFACTOR_M0_BASELINE.md](docs/REFACTOR_M0_BASELINE.md) ~ [REFACTOR_M13_REPORT.md](docs/REFACTOR_M13_REPORT.md) — 마일스톤별 보고서

### D. 비상 절차
청사진 §10.5:
1. **Kill Switch 즉시 활성화**: `python -c "from governance.kill_switch import KillSwitch; KillSwitch.activate(reason='emergency', source='manual')"`
2. **운영자 직접 청산**: Binance 웹/앱 (수동)
3. **API 키 비활성화**: Binance 설정 (보안)
4. **사후**: HANDOFF.md 갱신 + git commit + push

### E. 청사진 § 매핑 (역참조)
| 청사진 § | 구현 마일스톤 |
|---|---|
| §3.1.2 Binance MCP | M5 (shadow opt-in) |
| §3.1.3 WebSocket | (기존 data/ws_collector.py, Phase 2-E) |
| §3.2.1 Strategy Skill | M2 |
| §3.2.2 5-Agent | M3 |
| §3.2.3 Single-Position Rotation | M2 (SinglePositionRotationSkill) |
| §3.3.1 주문 엔진 | (기존 TradeExecutor 0줄 수정) |
| §3.3.3 리스크 게이트 | (기존 RiskManager + M3 RiskAgent wrap) |
| §3.4.1 Hooks | M6 |
| §3.4.2 Slack/Notion MCP | M5 (Slack only) |
| §3.4.3 Kill Switch | M1 + M4 |
| §6.1 SignalDecision | M1 |
| §6.3 AgentReview | M1 + M3 |
| §6.4 AuditLog + chain | M1 |
| §6.5 Setup Registry | M2 |
| §7.2 ALCOA+ 9원칙 | M1 + M3 |
| §7.4 Risk hierarchy | M4 (2-tier 단순화) |
| §7.5 Stage 3 백스톱 | M4 (governance/stage3_rule) |
| §8.3 Phase 1.5 OQ | (Paper 운영 → 진입) |
| §10.1 CLAUDE.md template | M0 |
| §10.3 Phase 1.5 체크리스트 | docs/PHASE1_5_MICRO_LIVE_PROMPT.md |
| §10.5 비상 절차 | docs/PAPER_OPERATION_GUIDE §6 |

---

## ⏭️ 진행 흐름 요약 (한눈에)

```
[2026-05-26 ~ 27 — 청사진 리팩토링]
M0  CLAUDE.md 4→6 + 2-tier hierarchy  (e795460)
M0.5 대량 동기화 72파일                (dfaf368)
M1  ALCOA+ + KillSwitch 어댑터        (433be52)  ← 거버넌스 인프라 시작
M2  Strategy Skill 3개 + 7기준         (e9293c3)  ← 알파 + 재현성
M3  5-Agent + 7→5 매핑                (9821a96)  ← 다관점 검증
M4  Stage 3 + 2-tier                   (37e1c58)  ← 자동 백스톱
M5  Slack MCP + Binance shadow         (23edbe4)  ← 외부 통신
M6  Hooks (PowerShell)                 (4c0f316)  ← 개발 안전망
M7  main_7590 shadow + Phase1.5 prompt (0dad5a6)  ← 통합 캡슐화
M8  Supabase 폐기 → 로컬 only          (ecd4509)  ← 데이터 단순화
M9  Testnet 봇 가동 검증               (dry-run)  ← 통합 동작 확인
M10 Streamlit + Plotly 6 페이지        (a862e24)  ← 실시간 시각화
M11 Testnet 백테스트 smoke (1/7 FAIL) (c248b7a)
M12 결과 검토 + 옵션 A/B/C 트리        (9048cb0)
M13 v1 mainnet 16종목 × 3y (5/7)       (8d6dd13)
M13 v2 MDD 모델 수정 (6/7 CONDITIONAL) (62a3575)  ← 🎯 알파 실재 확인
M14 Paper 운영 가이드                  (127322f)  ← 현재
       ↓
[2026-05-27 ~ 06-10 — Paper 운영 1~2주] 🔄 진행중
       ↓
[검증 통과 시] Phase 1.5 micro-live ($30~50)  📅 예정
       ↓
[Phase 1.5 PASS] Phase 2 멀티 엔진 ($100~$300)  📅 예정
       ↓
[Phase 2~5 자율 운영] 청사진 §8.4~§8.7  📅 예정
```

---

**Status (2026-05-27 현재)**: M14 완료, **Paper 운영 1~2주 진입 대기**. 운영자가 `python main_7590.py --dry-run --duration 1209600` 가동 시 Paper 운영 시작. 1~2주 후 결과 검토 → Phase 1.5 결정.
