# Paper 운영 1~2주 가이드 (M14, v3.2.0)

> **Status**: 운영자 결정 (2026-05-27): M13 v2 결과 = 6/7 통과 + MDD 9.92% HANDOFF 정합 → **Paper 운영 1~2주 → Phase 1.5 micro-live**
> **Setup**: `1d_tsmom_donchian_long_v1` (setup_registry status=CONDITIONAL)
> **목적**: 실제 시장 1~2주 testnet 환경에서 5-Agent shadow review + 대시보드 모니터링 → Phase 1.5 진입 결정

---

## 1. Paper 운영 환경 설정

### 1-1. `.env` 확인 + 옵션 설정
```bash
USE_TESTNET=true                          # 안전 (Paper 운영 = testnet)
ENABLE_SHADOW_AGENTS=true                 # M7 shadow_runner 활성화 (5-Agent 자동 review)
ENABLE_BINANCE_MCP_SHADOW=false           # 선택 — M5 MCP shadow diff 검증
KILLSWITCH_FILE=data/KILLSWITCH           # 또는 외장 SSD 경로
SUPABASE_ENABLED=false                    # 로컬 sqlite only
```

### 1-2. 봇 가동 (백그라운드 또는 Windows Task)
```bash
# Option A: 일회성 1~2주 dry-run
python main_7590.py --dry-run --duration 1209600    # 14일 = 1,209,600초

# Option B: Windows Task (재시작 안정성)
# scripts/run_ws_collector.bat 패턴 참조하여 BinanceFuturesBot 등록
# AtLogOn trigger + 자동 재시작
```

### 1-3. 모니터링 — 대시보드 (M10) + heartbeat (Telegram)
```bash
# 대시보드 가동 (별도 프로세스)
scripts/run_dashboard.bat                  # http://localhost:8501

# Telegram heartbeat (기존, .env TELEGRAM_BOT_TOKEN 설정 시)
# main_7590 자동 매 시간 heartbeat 전송
```

---

## 2. Paper 운영 점검 체크리스트 (매일)

### 2-1. 대시보드 페이지별 확인
| 페이지 | 확인 항목 | 정상 기준 |
|---|---|---|
| 1. 운영 상태 | 자본 / 포지션 / equity curve | testnet $4999.88 유지 (실패 X 정상) |
| 2. 5-Agent 검토 | verdict 매트릭스 + 카운트 | SIGNAL_GENERATED 마다 5 reviews (1d 봉 마감 시) |
| 3. ALCOA+ 감사 | chain 무결성 + event 분포 | Chain valid = ✅ YES |
| 4. Kill Switch | 활성 상태 + 이력 | INACTIVE (정상) |
| 5. 시장 차트 | 1m OHLC (Tier 0 6종) | aggregator_1m 적재 정상 |
| 6. Setup Registry | DailyTSMOM CONDITIONAL | status=CONDITIONAL 유지 |

### 2-2. DB 직접 쿼리 (DuckDB 또는 sqlite3)
```bash
# audit_log 일일 이벤트 분포
sqlite3 data/bot.db "SELECT event_type, COUNT(*) FROM audit_log
  WHERE ts >= datetime('now', '-1 day') GROUP BY event_type"

# 5-Agent 일일 verdict 분포
sqlite3 data/bot.db "SELECT agent, verdict, COUNT(*) FROM agent_reviews
  WHERE ts >= datetime('now', '-1 day') GROUP BY agent, verdict"

# CRITICAL/REJECT 이벤트 알림
sqlite3 data/bot.db "SELECT * FROM agent_reviews
  WHERE verdict IN ('CRITICAL', 'REJECT', 'INVALID', 'FAIL')
  ORDER BY ts DESC LIMIT 20"
```

### 2-3. KillSwitch 시나리오 검증 (선택, 매주 1회)
```bash
# 수동 활성/해제 사이클
python -c "from governance.kill_switch import KillSwitch; KillSwitch.activate(reason='paper drill', source='manual')"
# → main_7590 다음 _iter() 에서 [Main] KillSwitch 활성 — iteration 차단
python -c "from governance.kill_switch import KillSwitch; KillSwitch.deactivate(by='operator')"
```

---

## 3. Paper 운영 종료 조건 (1~2주 후)

### 3-1. 정상 종료 (Phase 1.5 진입)
- audit_log chain 무결성 100% (verify_chain → valid=True)
- Kill Switch 5 시나리오 검증 통과 (수동 + btc_risk_off + OpsAgent CRITICAL + SSD + 자동 차단)
- 5-Agent 모든 review 정상 작동 (SIGNAL_GENERATED 시 5 reviews 적재)
- downtime < 0.5% (Telegram heartbeat 누락 < 1시간)
- 운영자 명시 GO → [docs/PHASE1_5_MICRO_LIVE_PROMPT.md](PHASE1_5_MICRO_LIVE_PROMPT.md) 진입

### 3-2. 비정상 종료 (Phase 1.5 보류)
- audit_log chain 깨짐 (broken_at_index != None) → SQLite trigger 또는 운영자 실수 의심
- 5-Agent review 누락 (orchestrator 오류)
- KillSwitch 자동 트리거 발생 (false-positive 확인)
- 봇 crash (Telegram heartbeat 1시간+ 누락) → 디버깅 + 재가동

---

## 4. Paper → Phase 1.5 진입 결정 트리

```
[Paper 운영 1~2주 종료]
   ↓
[종합 점검: 대시보드 + DB + heartbeat]
   ↓
   ├─ 모든 검증 통과 → 운영자 명시 GO
   │   ↓
   │   [PHASE1_5_MICRO_LIVE_PROMPT 절차]
   │   1. $30~50 입금 (실거래 키 권한 확인)
   │   2. R0_QUALIFIED 수동 결정 (set_status)
   │   3. USE_TESTNET=false (실거래 전환)
   │   4. 1회 리스크 0.05~0.1% ($1.5~5/회)
   │   5. 5~10 실거래 누적 (2~4주)
   │   ↓
   │   [Phase 1.5 PASS → Phase 2 진입]
   │
   ├─ 일부 검증 fail → 추가 Paper 운영 1~2주
   │   ↓ (개선 후)
   │   [재평가 → 진입 결정]
   │
   └─ 결정적 fail → 청사진 §7.5 Stage 3 옵션 A/B/C
```

---

## 5. 운영자 일일 명령 모음

```bash
# 1. 봇 상태 확인 (10초)
curl -s http://localhost:8501/_stcore/health  # 대시보드 healthy?
sqlite3 data/bot.db "SELECT COUNT(*) FROM audit_log WHERE ts >= datetime('now', '-1 day')"  # 일일 이벤트

# 2. KillSwitch 확인 (5초)
python -c "from governance.kill_switch import KillSwitch; print('active=', KillSwitch.is_active())"

# 3. 봇 로그 tail (실시간)
tail -f logs/main.log  # 또는 별도 로그 디렉토리

# 4. Setup Registry CONDITIONAL 유지 확인 (10초)
python -c "
from registry.setup_registry import SetupRegistry
r = SetupRegistry(db_path='data/bot.db')
s = r.get_setup('1d_tsmom_donchian_long_v1')
print('status:', s['status'])
print('PF:', s['last_metric_pf'], 'top3_excl:', s['last_metric_top3_excluded_pf'])
"
```

---

## 6. 비상 절차 (청사진 §10.5)

### Kill Switch 즉시 활성화
```bash
python -c "from governance.kill_switch import KillSwitch; KillSwitch.activate(reason='emergency', source='manual')"
```

### 대시보드에서 (M10 페이지 4)
- http://localhost:8501/kill_switch
- "DEACTIVATE" 입력 후 해제 (운영자만)

### 봇 강제 종료
```bash
# Windows
Get-Process python | Where-Object {$_.MainWindowTitle -like "*main_7590*"} | Stop-Process

# Linux/Mac
pkill -f "python main_7590.py"
```

---

## 7. 1~2주 후 다음 단계 보고서

Paper 운영 종료 시 자동 생성 (운영자 직접 실행):
```bash
python scripts/run_m12_review.py    # setup_registry 최신 상태
# + 대시보드 페이지 6 (Setup Registry) 캡처
# + audit_log chain 무결성 (verify_chain) 결과
```

→ `docs/PAPER_OPERATION_RESULT.md` 자동 생성 권장 (M15 작업)
→ 운영자 명시 결정: Phase 1.5 진입 / 추가 Paper / Stage 3
