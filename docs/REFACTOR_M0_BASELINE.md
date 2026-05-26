# M0 Baseline Report — BINANCE_FUTURES_BOT 청사진 리팩토링 시작

> **Milestone**: M0 — 사전 정렬 + TIER 1 #9 완화
> **Date**: 2026-05-26
> **Plan**: [REFACTOR_PLAN_v2_BLUEPRINT.md](REFACTOR_PLAN_v2_BLUEPRINT.md)
> **Branch**: refactor/blueprint-m0-foundation

## 1. 기준선 측정 (pytest)

```
pytest tests/ -q --tb=no
======================= 718 passed in 78.74s (0:01:18) ========================
```

- HANDOFF.md 2026-05-25 시점: 706 passed
- 작업본 (이 PC, 2026-05-26 실측): **718 passed** (+12 추가)
- 모든 테스트 PASS, 회귀 0

## 2. 핵심 코드 현재 값 확인

```python
from config.settings import PAIR_WHITELIST_CONFIG, RISK_RULES
print('PROTECTED:', PAIR_WHITELIST_CONFIG.protected_symbols)
# ['BTCUSDT', 'ETHUSDT', 'HOLOUSDT', 'CFXUSDT', 'LYNUSDT', 'INJUSDT']
print('daily_loss:', RISK_RULES.max_daily_loss_pct)  # 0.015 = 1.5%
print('mdd:', RISK_RULES.max_total_drawdown_pct)      # 0.15 = 15%
```

- 보호 자산: **6개 확인** (CLAUDE.md만 4개로 표기됨 → M0에서 동기화)
- daily_loss: 1.5% (SPEC v3.1.1 그대로, 운영자 결정 #v2-2 hard 임계)
- mdd: 15% (SPEC v3.1.1 그대로, 운영자 결정 #v2-2 hard 임계)

## 3. 운영자 결정 8건 (2026-05-26 확정)

| # | 항목 | 결정 |
|---|---|---|
| v1-1 | 범위 | M0~M6 (Phase 1 IQ+MV+PQ) |
| v1-2 → v2-2 | 임계값 hierarchy | **2-tier** (warn -1.0% / hard -1.5%) |
| v1-3 | 보호 자산 | **6개** + CLAUDE.md 동기화 |
| v1-4 | MCP 범위 | Slack ON, Notion 보류, Binance MCP shadow opt-in, WebSocket 유지 |
| v2-1 | 리팩토링 의도 | **청사진 전체 재실행** + HANDOFF 재검토 + TIER 1 #9 조건부 완화 |
| v2-3 | Strategy Skill | **DailyTSMOMDonchian + CandidateContext + SinglePositionRotation 3개** (OISurge 제외) |
| v2-4 | KillSwitch 통합 | 청사진 기준 + btc_risk_off 어댑터 |
| 추가 | 백테스트 7기준 | n≥200 / Net PF≥1.25 / Expectancy_R>0 / avg_win/avg_loss≥1.5 / MDD≤25% / single_symbol<25% / **상위 3종목 제거 PF≥1.0** + 응답지연<5s (M6) |

## 4. M0 작업 완료 (체크리스트)

- [x] pytest 기준선 측정 (718 passed)
- [x] [CLAUDE.md](../CLAUDE.md) 업데이트
  - 보호 자산 4 → 6 (line 22, 114, 137)
  - TIER 1 #7 (보호 자산), #8 (GitHub 워크플로우) 신규
  - TIER 1 #9 조건부 완화 명시
  - 청사진 §10.1 template 통합
  - v3.1.1 → v3.2.0 버전 표기
- [x] [SPEC_v3.1_APPENDIX_E.md](SPEC_v3.1_APPENDIX_E.md) **E-10 신규** (2-tier risk hierarchy)
- [x] [HANDOFF.md](HANDOFF.md) "v3.2.0 청사진 리팩토링 시작" 갱신 (재검토 명시)
- [x] [REFACTOR_PLAN_v2_BLUEPRINT.md](REFACTOR_PLAN_v2_BLUEPRINT.md) 신규 (영구 원본, 다른 PC 참조용)
- [x] [REFACTOR_M0_BASELINE.md](REFACTOR_M0_BASELINE.md) 신규 (본 파일)
- [ ] git branch `refactor/blueprint-m0-foundation` 생성
- [ ] git add (명시 파일) + commit + push

## 5. M0 검증 게이트 (다음 단계 진입 전)

- [x] pytest 718 passed 확인
- [x] PROTECTED_SYMBOLS 6개 출력 확인
- [x] 운영자 결정 8건 명시 (본 파일 §3)
- [x] CLAUDE.md TIER 1 #9 조건부 완화 운영자 서명 (2026-05-26 명시 GO)
- [ ] git push 완료 후 M1 진입

## 6. 변경 파일 매트릭스

| 파일 | 종류 | 변경 라인 (+/-) |
|---|---|---|
| CLAUDE.md | UPDATE | 신규 ~50줄 / 수정 ~10줄 (4→6 + TIER 1 #7~#9 추가 + 청사진 통합) |
| docs/SPEC_v3.1_APPENDIX_E.md | UPDATE | 신규 ~80줄 (E-10) / 수정 ~3줄 (부록 E 끝 매핑 update) |
| docs/HANDOFF.md | UPDATE | 신규 ~20줄 (재검토 시작) / 수정 ~5줄 |
| docs/REFACTOR_PLAN_v2_BLUEPRINT.md | NEW | 신규 ~200줄 |
| docs/REFACTOR_M0_BASELINE.md | NEW | 신규 ~80줄 |

**M0 합계**: 신규 ~430줄, 수정 ~18줄, 코드 0줄 변경.

## 7. M1 진입 조건

다음 마일스톤 (M1 — ALCOA+ 기반 + KillSwitch 어댑터) 진입 시:
1. M0 git push 완료 + GitHub 동기화 확인
2. M1 신규 14파일 작업 (db/migrations/v3_1_2_to_v3_2_0.sql 우선)
3. 운영자 명시 GO (단계별 검증 + 무한 진행 패턴)
