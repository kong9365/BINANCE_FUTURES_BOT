# M1 Report — ALCOA+ 기반 + KillSwitch 어댑터

> **Milestone**: M1 — ALCOA+ 기반 + KillSwitch 어댑터
> **Date**: 2026-05-26
> **Branch**: refactor/blueprint-m0-foundation
> **Previous**: [REFACTOR_M0_BASELINE.md](REFACTOR_M0_BASELINE.md)
> **Plan**: [REFACTOR_PLAN_v2_BLUEPRINT.md](REFACTOR_PLAN_v2_BLUEPRINT.md) M1
> **Status**: ✅ 완료 — M2 진입 가능

## 1. 신규 14파일 + 기존 2파일 수정

### audit/ (ALCOA+ 트레이서, 6파일)

| 파일 | 라인 | 역할 | 청사진 § |
|---|---|---|---|
| `audit/__init__.py` | 36 | 패키지 export | §3.2.1 |
| `audit/signal_decision.py` | 129 | `@dataclass SignalDecision` — ALCOA+ 9원칙 호환 | §6.1 |
| `audit/agent_review.py` | 87 | `@dataclass AgentReview` — 5-Agent 결과 (M3 사용) | §6.3 |
| `audit/audit_log.py` | 105 | `@dataclass AuditLog` + `EventType` 16종 상수 | §6.4 |
| `audit/chain.py` | 102 | `compute_payload_hash` / `compute_chain_hash` / `verify_chain` | §6.4 + §7.2 |
| `audit/audit_logger.py` | 147 | `AuditLogger.log_event()` append-only + chain 자동 | §6.4 |

### governance/ (Kill Switch + Preflight, 3파일)

| 파일 | 라인 | 역할 | 청사진 § |
|---|---|---|---|
| `governance/__init__.py` | 16 | 패키지 export | §3.4 |
| `governance/kill_switch.py` | 180 | 어댑터: `is_active() = file OR btc_is_halted` | §3.4.3 |
| `governance/preflight.py` | 109 | 시작 시 1회 검증 (KILLSWITCH, DB v3.2.0, ...) | §3.4.3 |

### db/ (스키마 + 마이그레이션, 2파일)

| 파일 | 변경 | 역할 |
|---|---|---|
| `db/migrations/v3_1_2_to_v3_2_0.sql` | 신규 200줄 | 6개 신규 테이블 + audit_log UPDATE/DELETE trigger + 7기준 컬럼 |
| `db/init_db.py` | +15줄 (멱등 등록) | v3.2.0 마이그레이션 등록 |

### tests/ (단위 테스트, 5파일, 50개 test)

| 파일 | 라인 | 테스트 수 |
|---|---|---|
| `tests/test_db_migration_v3_2_0.py` | 171 | 12 (테이블 6, trigger 2, idempotent 1, 7기준 1, 회귀 1, 등록 1) |
| `tests/test_audit_signal_decision.py` | 105 | 7 (기본 생성, confidence 검증, tz-aware, to_db_row, to_audit_payload, consistent, deterministic) |
| `tests/test_audit_chain.py` | 101 | 11 (hash 결정성, key 순서 무관, string 입력, dict/str 외 거부, 다른 → 다른, length 검증, empty 거부, chain seed, verify_chain) |
| `tests/test_audit_logger.py` | 172 | 7 (async 단일 INSERT, chain 연결, 잘못된 event_type, non-dict, empty actor, deterministic, related_ids) |
| `tests/test_kill_switch.py` | 136 | 13 (env path, false/true, activate/deactivate, 운영자 외 거부, empty 거부, 어댑터 true/false, 파일 override, corrupted) |

### main_7590.py 변경 (보호 파일, 13줄 추가)

```python
# import 1줄 추가:
from governance.kill_switch import KillSwitch

# _iter() 첫 줄에 9줄 추가:
async def _iter(self) -> None:
    ...
    # ── v3.2.0 M1: KillSwitch 어댑터 체크 (청사진 §3.4.3) ──
    if KillSwitch.is_active(
        btc_state=self.btc_risk_off_state,
        now=datetime.now(timezone.utc),
    ):
        logger.critical("[Main] KillSwitch 활성 — iteration 차단")
        return
```

운영자 명시 GO ("단계별 검증 + 무한 진행", 2026-05-26) 하에 진행. CLAUDE.md TIER 1 #5 보호 파일이지만 청사진 §3.4.3 "Kill Switch 코드 첫 줄부터 설계" 명시 요구사항과 정합.

## 2. 검증 게이트 (모두 통과)

### 단위 테스트 — 50개 모두 PASS
```
$ pytest tests/test_db_migration_v3_2_0.py tests/test_audit_*.py tests/test_kill_switch.py -v
============================= 50 passed in 8.62s ==============================
```

### 전체 회귀 — 768 passed (M0 718 + M1 50)
```
$ pytest tests/ -q
======================= 768 passed in 89.18s (0:01:29) ========================
```

회귀 0 — 기존 718 테스트 모두 유지.

### py_compile — 모든 M1 파일 OK
```
$ python -m py_compile main_7590.py db/init_db.py audit/*.py governance/*.py
All M1 files compile OK
```

### KillSwitch 어댑터 4시나리오 검증 (test_kill_switch.py 통합)
- 파일 없음 + btc 정상 → `is_active() = False` ✓
- 파일 생성 → `is_active() = True` ✓ (`test_is_active_true_after_activate`)
- 파일 없음 + btc halted → `is_active() = True` ✓ (`test_btc_risk_off_adapter_true`)
- 파일 활성 + btc 만료 → `is_active() = True` ✓ (`test_file_overrides_btc_risk_off`)
- 운영자만 수동 해제 → 다른 source 는 `PermissionError` ✓

### ALCOA+ chain 무결성 검증 (test_audit_logger.py)
- 동일 payload 두 번 hash → 동일 결과 (Consistent) ✓
- 3 row 연속 INSERT → `previous_log_hash` chain 검증 정상 ✓
- 잘못된 chain (WRONG hash) → `verify_chain` 가 `(False, broken_at_index)` 반환 ✓
- `audit_log UPDATE` 시도 → SQLite trigger ABORT (Accurate) ✓
- `audit_log DELETE` 시도 → SQLite trigger ABORT (Accurate) ✓

## 3. M2 진입 조건

- [x] M1 신규 14파일 작성
- [x] db/init_db.py v3.2.0 등록
- [x] main_7590.py KillSwitch 체크 추가
- [x] 50 신규 단위 테스트 PASS
- [x] 전체 회귀 768 passed
- [x] py_compile 통과
- [x] git commit + push (refactor/blueprint-m0-foundation branch)
- [ ] M2 진입 시 작업본 ↔ 클론 동기화 재확인

## 4. M2 작업 (다음)

- Strategy Skill 3개 wrap (`DailyTSMOMDonchian` + `CandidateContext` + `SinglePositionRotation`)
- Setup Registry (DB 기반 CRUD)
- 백테스트 7기준 검증 게이트 (n≥200 / Net PF≥1.25 / Expectancy_R>0 / avg_win/avg_loss≥1.5 / MDD≤25% / single_symbol<25% / **상위 3종목 제거 PF≥1.0**)
- main_7590.py shadow SignalDecision 블록 추가
- 24h dry-run shadow 검증
- 18 신규 파일 + ~5줄 main_7590 수정

## 5. 변경 통계

```
M1 commit:
17 files changed, 1823 insertions(+), 1 deletion(-)

  - 신규: audit/ 6 + governance/ 3 + db/migrations/v3_1_2_to_v3_2_0.sql + tests/ 5 = 15
  - 수정: db/init_db.py (멱등 등록), main_7590.py (KillSwitch 체크 13줄)
```

신규 코드 ≈ 1,810줄, 기존 변경 13줄 — 기존 코드 손상 없이 청사진 §6 + §3.4.3 완전 구현.

## 6. 다음 PC 인계 정보

```
git checkout refactor/blueprint-m0-foundation
git pull origin refactor/blueprint-m0-foundation
.env 운영자 수동 복사
pip install -r requirements.txt
python -m pytest tests/ -q  # 768 passed 확인
# M2 작업 시작 가능
```
