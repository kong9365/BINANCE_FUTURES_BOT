# M5 Report — Slack MCP + Binance MCP shadow + Outbox (Notion 보류)

> **Milestone**: M5 — MCP 통합
> **Date**: 2026-05-26
> **Branch**: refactor/blueprint-m0-foundation
> **Previous**: [REFACTOR_M4_REPORT.md](REFACTOR_M4_REPORT.md)
> **Plan**: [REFACTOR_PLAN_v2_BLUEPRINT.md](REFACTOR_PLAN_v2_BLUEPRINT.md) M5
> **Status**: ✅ 완료 — M6 진입 가능

## 1. 운영자 결정 #v1-4 정확 반영

- **Slack MCP**: ON (알림 + KillSwitch 명령)
- **Notion MCP**: 보류 (Phase 1.5 prompt 이후 재검토)
- **Binance MCP**: shadow opt-in (`ENABLE_BINANCE_MCP_SHADOW` env flag, 기본 OFF)
- **WebSocket**: 기존 [data/ws_collector.py](../data/ws_collector.py) 유지

## 2. 신규 9파일

### mcp/ (4파일)

| 파일 | 라인 | 역할 |
|---|---|---|
| `mcp/__init__.py` | 14 | 패키지 export |
| `mcp/outbox.py` | 145 | Outbox 패턴 (enqueue/get_pending/mark_sent/mark_failed/count_pending) |
| `mcp/slack_mcp.py` | 130 | SlackMCPClient (send_alert/send_trade_entry/send_kill_switch_alert/parse_command) |
| `mcp/binance_mcp.py` | 135 | shadow client (ENABLE_BINANCE_MCP_SHADOW env, diff 1% 임계) |

### governance/ (1파일)
- `governance/slack_command_handler.py` (43줄) — `/bot halt`, `킬스위치` → KillSwitch 활성화

### db/migrations/ (1파일) + init_db 수정
- `db/migrations/v3_2_0_to_v3_2_1.sql` (35줄) — slack_outbox + mcp_diff_log
- `db/init_db.py`: v3.2.1 마이그레이션 등록 (+10줄)

### tests/ (4파일, 30개 test)

| 파일 | 테스트 수 |
|---|---|
| `tests/test_outbox.py` | 7 |
| `tests/test_slack_mcp.py` | 9 |
| `tests/test_binance_mcp_shadow.py` | 7 |
| `tests/test_db_migration_v3_2_1.py` | 7 |

## 3. 검증 게이트

### 단위 테스트 — 30 PASS
```
$ pytest tests/test_outbox.py tests/test_slack_mcp.py tests/test_binance_mcp_shadow.py tests/test_db_migration_v3_2_1.py
============================= 30 passed in 12.71s =============================
```

### 핵심 시나리오

**Outbox fail-soft (test_outbox.py)**:
- Slack send 성공 → 즉시 sent, outbox 0
- Slack send 실패 → outbox pending, retry_count++
- 5회 실패 → failed_permanent (운영자 알림 필요)

**Slack 명령 파싱 (test_slack_mcp.py)**:
- `/bot halt`, `킬스위치`, `halt` → command="halt"
- `/bot status`, `status` → command="status"
- 기타 → None

**Binance MCP shadow (test_binance_mcp_shadow.py)**:
- env OFF → matched=True (MCP 호출 X)
- env ON + 동일 응답 → matched=True (diff 0%)
- env ON + 5% diff → matched=False + mcp_diff_log INSERT
- MCP 호출 실패 → matched=False + error 기록

**DB 마이그레이션 (test_db_migration_v3_2_1.py)**:
- v3.2.1 등록 + slack_outbox/mcp_diff_log 테이블 생성
- v3.2.0 (M1) 6개 테이블 그대로 작동
- 멱등 재실행

## 4. M6 진입 조건

- [x] M5 신규 9파일 작성
- [x] Slack MCP fail-soft + 명령 파싱
- [x] Binance MCP shadow opt-in (env flag)
- [x] db v3.2.1 마이그레이션 (slack_outbox + mcp_diff_log)
- [x] 30 신규 단위 테스트 PASS
- [ ] 전체 회귀 (백그라운드)
- [ ] 클론 동기화 + commit + push

## 5. M6 작업 (다음, 최종)

- `.claude/hooks/pre_tool_use.sh` (보호 파일 차단)
- `.claude/hooks/post_tool_use.sh` (py_compile + pytest 자동)
- `.claude/hooks/user_prompt_submit.sh` (위험 키워드)
- `.claude/hooks/README.md`
- `.claude/settings.local.json` 업데이트
- M6 종합 검증: End-to-end shadow workflow, ALCOA+ 9원칙 매트릭스, Kill Switch 시나리오 6건, Hooks 작동, 회귀
- `docs/REFACTOR_M6_FINAL_REPORT.md` (Phase 1.5 prompt 진입 권장 여부)

## 6. 청사진 § 매핑

| 모듈 | 청사진 § |
|---|---|
| outbox.py | §7.2 (ALCOA+ Contemporaneous + Available) |
| slack_mcp.py | §3.4.2 (Slack 알림 + Kill Switch 명령) |
| binance_mcp.py | §3.1.2 (Binance Futures MCP, shadow opt-in) |
| slack_command_handler.py | §3.4.3 (수동 Kill Switch) + §3.4.2 |
| v3_2_0_to_v3_2_1.sql | §3.4.2 outbox + §3.1.2 shadow |
