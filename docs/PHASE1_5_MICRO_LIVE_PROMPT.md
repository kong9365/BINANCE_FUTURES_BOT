# Phase 1.5 — Micro-live Sandbox Prompt

> **목적**: 청사진 §8.3 (Phase 1.5 OQ — Operational Qualification) 실행 prompt.
> 청사진 §10.3 Phase 1.5 진입 체크리스트 + M0~M7 인프라 활용.
> **자본**: $30~50 소액 (수익 X, 시스템 검증만).
> **기간**: 2~4주, 5~10 실거래 누적.

---

## 1. Phase 1.5 진입 조건 (체크리스트)

### 1-1. M0~M6 완료 확인
- [x] M0 사전 정렬 (`e795460`) + M0.5 동기화 (`dfaf368`)
- [x] M1 ALCOA+ + KillSwitch 어댑터 (`433be52`)
- [x] M2 Strategy Skill 3개 + Setup Registry + 7기준 (`e9293c3`)
- [x] M3 5-Agent Subagent (`9821a96`)
- [x] M4 KillSwitch 자동 + Stage 3 + 2-tier (`37e1c58`)
- [x] M5 Slack MCP + Binance MCP shadow + Outbox (`23edbe4`)
- [x] M6 Hooks + 종합 검증 (`4c0f316`)
- [x] M7 main_7590 shadow 통합 (별도 commit 예정)

### 1-2. R0_QUALIFIED 후보 결정 (운영자 작업)
운영자가 실제 데이터로 M2 7기준 백테스트 실행 → `setup_registry` 결과 검토:

```bash
# DailyTSMOMDonchianSkill 7기준 백테스트 (운영자 환경)
python -c "
from skills.daily_tsmom_donchian_skill import DailyTSMOMDonchianSkill
from registry.setup_registry import SetupRegistry, SetupMetrics
from backtesting.signal_validation_report import validate_and_persist
from db.init_db import init_db

init_db('data/bot_live.db')
registry = SetupRegistry(db_path='data/bot_live.db')
registry.register(DailyTSMOMDonchianSkill)

# 실제 백테스트로 trades 리스트 생성 (Supabase + ohlcv 의존)
# trades = run_real_backtest(...)
# report = validate_and_persist(trades=trades, setup_id=DailyTSMOMDonchianSkill.SETUP_ID, registry=registry)
# print(report)
"
```

**합격 조건 (운영자 권장 7기준)**:
- n ≥ 200
- Net PF ≥ 1.25
- Expectancy_R > 0
- avg_win/avg_loss ≥ 1.5
- MDD ≤ 25%
- Single symbol < 25%
- **Top-3 excluded PF ≥ 1.0** (ZEC 단일종목 행운 방어 — HANDOFF A2-② 핵심)

→ 모두 통과: `setup.set_status("1d_tsmom_donchian_long_v1", "R0_QUALIFIED")` (auto_update_status=True 자동)
→ 1개 borderline: `CONDITIONAL` (운영자 명시 결정)
→ 2개 이상 fail: `DISABLED` → Stage 3 옵션 A/B/C

### 1-3. 환경 준비 (운영자 작업)
- [ ] `.env` 의 `BINANCE_API_KEY` / `BINANCE_API_SECRET` (실거래 키, IP 화이트리스트 ON)
- [ ] `.env` 의 `USE_TESTNET=false` (실거래) — 또는 `USE_TESTNET=true` (Binance testnet 검증)
- [ ] `.env` 의 `SLACK_WEBHOOK_URL` + `SLACK_CHANNEL_ALERTS` (M5 알림)
- [ ] `KILLSWITCH_FILE=E:/bot_data/state/KILLSWITCH` (외장 SSD 경로, 선택)
- [ ] `ENABLE_SHADOW_AGENTS=true` (M7 5-Agent 자동 검증, 안전)
- [ ] `ENABLE_BINANCE_MCP_SHADOW=true` (선택, python-binance와 결과 비교)
- [ ] $30~50 입금 (Binance Futures USDT-M)

### 1-4. main_7590 통합 (운영자 작업, 1줄)
M7 의 `governance/shadow_runner.py` 가 *standalone*. main_7590 통합은 운영자 결정 후 1줄 추가:

```python
# main_7590.py __init__ 끝 (운영자 명시 승인 후)
from governance.shadow_runner import ShadowAgentRunner
from agents.orchestrator import AgentOrchestrator
from agents.strategy_agent import StrategyAgent
# ... (5-Agent 의존성 import)

# 의존성 생성
strategy_agent = StrategyAgent(registry=SetupRegistry(db_path))
risk_agent = RiskAgent(risk_manager=self.risk_manager)
execution_agent = ExecutionAgent()
data_agent = DataAgent()
ops_agent = OpsAgent(system_health_monitor=self.health, db_path=db_path)
audit_logger = AuditLogger(db_path=db_path)
orchestrator = AgentOrchestrator(
    strategy_agent=strategy_agent, risk_agent=risk_agent,
    execution_agent=execution_agent, data_agent=data_agent,
    ops_agent=ops_agent, audit_logger=audit_logger,
)
self.shadow_runner = ShadowAgentRunner(
    orchestrator=orchestrator, audit_logger=audit_logger,
    cfg=SHADOW_AGENT_CONFIG,
)

# _handle_signal() 끝 (또는 시작)
if self.shadow_runner.enabled:
    await self.shadow_runner.run_shadow(candidate, capital_snapshot)
```

`ENABLE_SHADOW_AGENTS=false` 면 0줄 실행 (안전).

---

## 2. 검증 항목 (청사진 §8.3)

### 2-1. 실거래 체결 (post-only)
- [ ] post_only=True LIMIT 주문 정상 (TradeExecutor)
- [ ] 메이커 체결률 실측 (arXiv 2502.18625 caveat — 1/3 비용 가정 약화)
- [ ] Case A 분포 (즉시 체결)
- [ ] Case B 분포 (5분 미체결 → skip)
- [ ] Case C 분포 (5분 미체결 → taker fallback)
- [ ] Case D 분포 (adverse selection)

### 2-2. ALCOA+ 9원칙 (M1 + M3)
- [ ] 모든 SignalDecision DB 적재 (signal_id, params_hash, raw_data_hash)
- [ ] 5-Agent reviews 모두 audit_log chain 연결
- [ ] audit_log UPDATE/DELETE 차단 (SQLite trigger)
- [ ] DuckDB 쿼리 가능

### 2-3. KillSwitch (M1 + M4)
**시나리오 5건 (모두 검증)**:
- [ ] Slack `/bot halt` → 60초 이내 차단
- [ ] btc_risk_off halted (BTC 1h -1.2%) → 어댑터 통합 작동
- [ ] OpsAgent CRITICAL (SystemHealthMonitor) → 자동 활성화
- [ ] 외장 SSD unmount → 자동 활성화 (M4 6조건)
- [ ] 일일 손실 -1.5% → 자동 KillSwitch (운영자 결정 #v2-2)

### 2-4. Slack 알림 (M5)
- [ ] 진입 알림 (`send_trade_entry`)
- [ ] KillSwitch 활성화 알림 (`send_kill_switch_alert`)
- [ ] Slack down 시 outbox 백로그 + 복구 자동 flush

### 2-5. 응답지연 (운영자 7기준 #8)
- [ ] `signal_decisions.ts_signal_generated` ↔ `trades.timestamp` diff < 5초 (95th percentile)
- [ ] DuckDB 쿼리:
  ```sql
  SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY (
    julianday(t.timestamp) - julianday(s.ts_signal_generated)
  ) * 86400000) as p95_ms
  FROM trades t JOIN signal_decisions s ON s.signal_id = t.signal_id
  WHERE t.timestamp >= datetime('now', '-7 days');
  ```

---

## 3. Phase 1.5 PASS 기준

다음 모두 만족 시 Phase 2 진입 가능 (청사진 §8.4 PQ Part 1):

- [ ] 5~10건 실거래 누적
- [ ] downtime < 0.5%
- [ ] ALCOA+ 무위반 (audit_log chain 100% 무결성)
- [ ] KillSwitch 5 시나리오 모두 검증
- [ ] Slack 알림 정상
- [ ] outbox 누락 0
- [ ] 운영자 7기준 + 응답지연 모두 합격

---

## 4. Phase 1.5 FAIL 시 (운영자 결정)

청사진 §7.5 Stage 3 옵션 A/B/C:
- **A**: Manual semi-discretionary 전환 (자동매매 archetype 포기)
- **B**: Buy-and-hold + 50d MA cash exit (Grayscale 2023)
- **C**: 운영자 가설 청취 (다른 archetype)

또는 단순 *재검증* (조정 후 Phase 1.5 재시작).

---

## 5. 운영자 일일 점검 체크리스트

```bash
# 매일 09:00 KST (또는 22:00 KST)
# 1. 헬스 리포트
python scripts/ws_health_report.py --window-hours 24 --telegram

# 2. audit_log chain 무결성
python -c "
import sqlite3
from audit.chain import verify_chain
conn = sqlite3.connect('data/bot_live.db')
rows = conn.execute(
    'SELECT log_id, payload_hash, previous_log_hash FROM audit_log ORDER BY ts ASC, log_id ASC LIMIT 1000'
).fetchall()
rows_d = [{'log_id': r[0], 'payload_hash': r[1], 'previous_log_hash': r[2]} for r in rows]
valid, broken_at = verify_chain(rows_d)
print(f'Chain valid={valid} broken_at={broken_at}')
"

# 3. KillSwitch 상태
python -c "from governance.kill_switch import KillSwitch; print('active=', KillSwitch.is_active(), 'status=', KillSwitch.get_status())"

# 4. setup_registry 활성 후보
python -c "
from registry.setup_registry import SetupRegistry
r = SetupRegistry(db_path='data/bot_live.db')
for s in r.get_active_setups():
    print(s)
"

# 5. outbox pending
python -c "
from mcp.outbox import Outbox
o = Outbox(db_path='data/bot_live.db')
print('outbox_pending=', o.count_pending())
"
```

---

## 6. Phase 2 진입 시점 (운영자 명시 GO)

청사진 §8.4:
- Phase 1.5 PASS + 90일 안정 운영 + ROI ≥ 5%
- 추가 PASS 후보 통합 시작 (CandidateContextSkill + Single-Position Rotation 활성)
- 자본 $100~$300 단계 확장

---

## 7. 비상 절차 (청사진 §10.5)

운영 중 이상 발견 시:
1. **즉시**: Slack `/bot halt` 명령 (60초 이내 차단)
2. **운영자 직접 청산**: Binance 웹/앱 (수동)
3. **API 키 비활성화**: Binance 설정 (보안)
4. **사후**: docs/HANDOFF.md 갱신 + git commit + push

---

## 끝

본 prompt 는 M0~M7 완료 후 *별도 세션*에서 진행. 청사진 §8.3 Phase 1.5 OQ 정합.
운영자 명시 GO 필수.
