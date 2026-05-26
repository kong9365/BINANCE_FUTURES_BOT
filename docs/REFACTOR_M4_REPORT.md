# M4 Report — KillSwitch 자동 6조건 + Stage 3 + 2-tier hierarchy

> **Milestone**: M4 — KillSwitch 자동 + Stage 3 + 2-tier
> **Date**: 2026-05-26
> **Branch**: refactor/blueprint-m0-foundation
> **Previous**: [REFACTOR_M3_REPORT.md](REFACTOR_M3_REPORT.md)
> **Plan**: [REFACTOR_PLAN_v2_BLUEPRINT.md](REFACTOR_PLAN_v2_BLUEPRINT.md) M4
> **Status**: ✅ 완료 — M5 진입 가능

## 1. 신규 6파일 + 기존 1파일 수정

### governance/ (3파일)

| 파일 | 라인 | 역할 |
|---|---|---|
| `governance/risk_hierarchy.py` | 90 | 2-tier hierarchy (warn -1.0% / hard -1.5%) + check_daily_loss/check_mdd |
| `governance/auto_trigger.py` | 120 | KillSwitch 자동 활성화 6조건 |
| `governance/stage3_rule.py` | 145 | 90d -10% / MDD -25% 백스톱 + 옵션 A/B/C 메시지 |

### tests/ (3파일, 34개 test)

| 파일 | 테스트 수 |
|---|---|
| `tests/test_risk_hierarchy_2tier.py` | 14 (default thresholds, daily ok/warn/borderline/hard/beyond, zero baseline, MDD ok/warn/hard, custom, 2-tier 시뮬레이션, config export) |
| `tests/test_kill_switch_auto.py` | 12 (6조건 각 trigger/not-trigger) |
| `tests/test_stage3_rule.py` | 7 (no trades, minor loss, PnL breach, MDD breach, old excluded, A/B/C 메시지, custom threshold) |

### config/settings.py 수정 (re-export)
- `RiskHierarchyConfig`, `RISK_HIERARCHY_CONFIG` 추가 (governance/risk_hierarchy 에서 import + re-export, 다른 *_CONFIG 와 일관성)

## 2. 운영자 결정 #v2-2 정확 반영 (2-tier hierarchy)

청사진 §7.4 의 3-tier (warn / soft / hard) 대신 **2-tier 단순화** (운영자 결정):
- `daily_warn_pct: -1.0%` → Slack 경고 (도달 가능 ✓)
- `daily_hard_pct: -1.5%` → KillSwitch (SPEC v3.1.1 유지)
- soft -2.0% **제거** (hard -1.5%에서 이미 차단되어 도달 X — 운영자 #문제2 수정)

청사진 -3.0% / -5.0% 는 *최종 백스톱 Stage 3* 로만 (별도 stage3_rule.py).

## 3. KillSwitch 자동 활성화 6조건 (청사진 §3.4.3)

| # | 조건 | 함수 | 임계 |
|---|---|---|---|
| 1 | 일일 손실 -1.5% | `check_daily_loss_hard()` | -1.5% (운영자 결정 #v2-2) |
| 2 | OpsAgent CRITICAL | `check_ops_critical()` | verdict="CRITICAL" |
| 3 | MCP 연결 끊김 5분 | `check_mcp_disconnect()` | 300초 (M5 이후 활성) |
| 4 | 데이터 stream stale > 3분 | `check_data_stale()` | 3분 |
| 5 | API 키 권한 변경 | `check_api_key_changed()` | M5 이후 활성 |
| 6 | 외장 SSD disconnect | `check_ssd_disconnect()` | BOT_DATA_DIR mount |

## 4. Stage 3 백스톱 (청사진 §7.5)

발동 조건: rolling 90d PnL ≤ -10% **또는** rolling 90d MDD > 25%

발동 시 옵션 A/B/C 메시지:
```
A. Manual semi-discretionary 전환
B. Buy-and-hold + 50d MA cash exit (Grayscale 2023)
C. 운영자 가설 청취
```

## 5. 검증 게이트

### 단위 테스트 — 34개 모두 PASS
```
$ pytest tests/test_risk_hierarchy_2tier.py tests/test_kill_switch_auto.py tests/test_stage3_rule.py -v
============================= 34 passed in 3.27s ==============================
```

### 2-tier 도달 가능성 검증 (`test_2tier_reachable_simulation`)
- -0.1% / -0.5% → OK
- **-1.0% → WARN** (도달 가능, 운영자 #문제2 수정)
- **-1.4% → WARN** (hard 미도달, 정상)
- **-1.5% → HARD** (자동 차단)
- -2.0% → HARD (정상 운영에서는 도달 X)

### 6조건 시뮬레이션 모두 통과
모든 조건 (daily_loss_hard, ops_critical, mcp_disconnect, data_stale, api_key_changed, ssd_disconnect) 의 *trigger* 와 *not_trigger* 시나리오 각각 검증.

### Stage 3 옵션 A/B/C 메시지 검증
`test_options_message_contains_abc`: `options_message` 에 "A. Manual", "B. Buy-and-hold", "C. 운영자" 모두 포함 확인.

## 6. M5 진입 조건

- [x] M4 신규 6파일 작성
- [x] 2-tier hierarchy (warn -1.0% / hard -1.5%)
- [x] KillSwitch 6조건 (운영자 결정 #v2-2 hard 임계 -1.5%)
- [x] Stage 3 백스톱 (90d -10% / MDD -25%)
- [x] 34 신규 단위 테스트 PASS
- [ ] 전체 회귀 pytest (백그라운드)
- [ ] 클론 동기화 + commit + push

## 7. M5 작업 (다음)

- mcp/binance_mcp.py (shadow opt-in)
- mcp/slack_mcp.py (알림 + Kill Switch 명령)
- mcp/outbox.py (Outbox 패턴)
- governance/slack_command_handler.py (`/bot halt` 처리)
- db/migrations/v3_2_0_to_v3_2_1.sql (slack_outbox, mcp_diff_log)
- tests 3파일
- 운영자 결정 #v1-4: Slack ON, Notion 보류, Binance MCP shadow opt-in

## 8. 청사진 § 매핑

| 모듈 | 청사진 § / 운영자 결정 |
|---|---|
| risk_hierarchy.py | §7.4 (운영자 결정 #v2-2 — 2-tier 단순화) |
| auto_trigger.py | §3.4.3 (6조건) + 운영자 결정 #v2-2 hard 임계 |
| stage3_rule.py | §7.5 (Stage 3 백스톱) |
| RiskHierarchyConfig | SPEC v3.1.1 §E-10 (신규) |
