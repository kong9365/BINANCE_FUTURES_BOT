# Binance Futures Bot v3.1.1 — Claude Code Guide

## What this project is

USDT-M Perpetual 선물 단타 자동매매 봇. 자본 $1,000~$10,000 대상. 학술 데이터 기반 보수적 설계 (참고: 단타 80~97% 손실, BIS WP#1087, NYU Stern 2022).

**전체 명세서**:
- `docs/SPEC_v3.1.md` (5,160줄) — 메인 명세서, 모든 결정의 근거
- `docs/SPEC_v3.1_APPENDIX_E.md` ★ — **v3.1.1 보호 자산 정책 보완** (필독)

코드 작성 전 항상 해당 §섹션 view. 두 문서 모두 참조.

## How to work on this project

### TIER 1 — HARD RULES (위반 시 작업 실패)

1. **명세서 우선**: 모든 모듈 작업 전 `docs/SPEC_v3.1.md` + `docs/SPEC_v3.1_APPENDIX_E.md`의 해당 §섹션 먼저 view.
2. **Plan Mode 먼저**: 새 파일 작성·기존 파일 큰 수정 전, plan만 보여주고 사용자 승인 후 코드.
3. **세션 1개 = 모듈 1개**: 한 세션에서 2개 이상 모듈 동시 작성 금지. 끝나면 `/clear` 안내.
4. **보안 절대 룰**: API 키·시크릿은 코드·로그·주석 어디에도 출력 금지. `.env`만 사용.
5. **테스트 필수**: 모든 신규 모듈은 `tests/test_<module>.py` 함께 작성.
6. ★ **보호 자산 절대 룰** (v3.1.1): protected_symbols (BTCUSDT/ETHUSDT/HOLOUSDT/LYNUSDT)는 봇이 거래 못함. 명시적 차단 룰 절대 우회 금지.

### TIER 2 — 코딩 컨벤션

- Python 3.10+, `from __future__ import annotations`, type hint 필수
- 모든 데이터 클래스는 `@dataclass`
- 모든 모듈에 `logger = logging.getLogger(__name__)`
- 비동기는 `asyncio`, 동기 호출은 `asyncio.to_thread`
- DB는 sqlite3, ORM 미사용
- 시간은 모두 UTC. `datetime.now(timezone.utc)`
- 외부 호출은 try/except + fallback 명시
- ★ 자본 조회는 **반드시** `capital_manager.get_snapshot()` 통해서만 (v3.1.1)
- ★ Binance Spot API 호출 금지 (`get_account`, `get_balance` 등 spot 메서드)

### TIER 3 — 절대 금지

- ❌ **GPT 실시간 의사결정 호출** (명세서 §3-1)
- ❌ **하드코딩 API 키·DB 비밀번호**
- ❌ **명세서 §9 RISK_RULES 임의 완화** (예: max_daily_loss_pct를 1.5% 초과로)
- ❌ **수동 개입 로직 추가** (명세서 §12-2)
- ❌ **실전 환경에서 페이퍼 미통과 상태로 작동** (명세서 §11-3)
- ❌ **백테스트 룩어헤드 코드** (명세서 §10-1)
- ❌ **명세서 §13 DB 스키마 무단 변경**
- ❌ **`localStorage`, `sessionStorage` 등 브라우저 API 사용**
- ★ **protected_symbols 페어의 봇 거래 코드 작성** (부록 E-1)
- ★ **Spot 자산을 봇 자본 계산에 포함** (부록 E-1-2)
- ★ **PairWhitelist.protected_symbols 우회 (manual_unblock으로 풀기 등)** (부록 E-3)
- ★ **wallet_balance와 available_balance 혼용** (부록 E-1-3)

## Module → Spec chapter map (v3.1.1)

세션 작업 시 다음 표로 명세서 챕터 빠르게 찾기:

| Module | Path | Spec § |
|---|---|---|
| RegimeDetector | strategy/regime_detector.py | §8-1 |
| CostGuard | strategy/cost_guard.py | §8-2 |
| DynamicPositionSizer | sizing/dynamic_sizer.py | §8-3 |
| WeeklyGPTAnalyst | analytics/weekly_report.py | §8-4 |
| MacroEventAnalyzer | analytics/macro_event_analyzer.py | §8-5 |
| SystemHealthMonitor | ops/system_health_monitor.py | §8-6 |
| PairWhitelist | strategy/pair_whitelist.py | §8-7 + **부록 E-3** ★ |
| **CapitalManager** ★ | data/capital_manager.py | **부록 E-2** |
| main_7590.py | main_7590.py | §8-8 + **부록 E-5** ★ |
| RiskManager | trading/risk_manager.py | §9 + **부록 E-4** ★ |
| BacktestEngine | backtesting/backtest_engine.py | §10 |
| DB schema | db/schema.sql | §13 + **부록 E-7** ★ |
| Config | config/settings.py | §14 + **부록 E-6** ★ |

작업 권장 순서: `docs/SESSION_PROMPTS.md` 참조 (세션 0~12, 단 2.5 신설/8 스킵)

## Project structure (v3.1.1)

```
config/      Config dataclass, macro_events.yaml
strategy/    RegimeDetector, CostGuard, PairWhitelist, OIFilter, QualityGate
sizing/      DynamicPositionSizer
analytics/   WeeklyGPTAnalyst, MacroEventAnalyzer, ExpectancyAnalyzer, ShadowRecorder
ops/         SystemHealthMonitor
trading/     TradeExecutor, ExitPlanController, RiskManager
data/        BinanceDataCollector, OIScanner, CapitalManager ★
backtesting/ BacktestEngine, walk_forward, plots
db/          schema.sql, migrations
tests/       모든 단위 테스트
docs/        SPEC_v3.1.md, SPEC_v3.1_APPENDIX_E.md ★, SESSION_PROMPTS.md, MODULE_CHECKLIST.md
reports/     주간 GPT 보고서 (런타임)
backtests/   백테스트 결과 (런타임)
logs/        봇 운영 로그 (런타임)
```

## Stack

Python 3.10+, python-binance, openai, pandas, numpy, pyyaml, sqlite3, pytest, python-telegram-bot.

전체 버전은 `requirements.txt`

## Workflow per session

세션 시작할 때:
1. 작업할 모듈을 `docs/MODULE_CHECKLIST.md`에서 확인
2. 해당 §SPEC 챕터 view (★ APPENDIX_E 해당 §섹션도 함께)
3. Plan만 작성 → 사용자 승인 → 코드 작성
4. 단위 테스트 작성 → `pytest tests/test_<module>.py` 실행
5. MODULE_CHECKLIST.md 체크박스 완료 표시
6. 사용자에게 "다음 세션은 `/clear` 후 다음 모듈" 안내

## Verification commands

```bash
pytest tests/                      # 전체 테스트
pytest tests/test_capital_manager.py -v  # ★ 보호 자산 검증
pytest tests/test_pair_whitelist.py -v   # ★ protected_symbols 검증
python -c "from config.settings import PAIR_WHITELIST_CONFIG; print(PAIR_WHITELIST_CONFIG.protected_symbols)"  # ['BTCUSDT', 'ETHUSDT', 'HOLOUSDT', 'LYNUSDT']
```

## Common pitfalls (이 프로젝트 특유)

- **명세서 자체에 이미 구현 코드가 있음** — 그대로 옮기지 말고 docstring·logger 보강 + 입력 검증 추가
- **dataclass field에 mutable default 금지** — `field(default_factory=list)` 사용
- **timezone naive datetime 금지** — 모든 datetime은 UTC tz-aware
- **테스트 fixture에서 외부 호출 mock 필수** — Binance/OpenAI 실제 호출 금지
- **백테스트 코드에서 `pandas.shift(-1)` 금지** — 미래 데이터 누수
- ★ **자본 계산 시 wallet vs available 혼용 주의** — 부록 E-1-3 표 참조
- ★ **PairWhitelist의 다른 룰보다 protected_symbols가 항상 우선**
- ★ **CapitalManager.get_spot_balance() 호출 시 ValueError 발생 (의도된 가드)** — Spot 잔고 필요하면 운영자가 직접 거래소 UI에서 확인

## v3.1 → v3.1.1 변경 요약

| 항목 | v3.1 | v3.1.1 |
|---|---|---|
| 자본 조회 | `_fetch_account_balance()` (단순) | `CapitalManager.get_snapshot()` (정밀) |
| 자본 기준 분리 | 없음 | wallet/margin/available/locked 4가지 명확 |
| 사이즈 계산 기준 | `current_capital` | `available_balance` (마진 락 제외) |
| 일일 손실 기준 | 현재 자본 | `daily_start_wallet_balance` |
| MDD 기준 | 현재 자본 | `initial_wallet_balance` (최초) |
| 보호 종목 | 없음 | `protected_symbols`: BTCUSDT, ETHUSDT, HOLOUSDT, LYNUSDT |
| Spot 격리 | 묵시적 | 명시적 (API 키 권한 + 코드 가드) |
| 시작 시퀀스 | 단순 | 초기 자본 기록 + 보호 종목 알림 + API 권한 검증 |

## When in doubt

명세서 §섹션 + 부록 E 해당 §섹션 view → 그래도 모호하면 사용자에게 질문. 추측 금지.
