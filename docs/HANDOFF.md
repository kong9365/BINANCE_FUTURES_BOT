# Binance Futures Bot — 세션 연속성 핸드오프 (컨텍스트 초기화 후 재개용)

> 최종 갱신: 2026-05-21 (실거래 투입 전 안정화 세션 완료 시점)
> 본 문서는 `C:\Users\jaeho\.claude\plans\python-binance-rosy-lollipop.md`(세션 플랜
> 파일)의 저장소 미러본이다. 컨텍스트 초기화와 무관하게 추적된다.
> (세션 0~12 구현 완료 시점의 옛 핸드오프는 git 히스토리에 보존됨.)

## Context
v3.1.2 봇의 실거래 투입 전 안정화 작업을 다회 세션에 걸쳐 진행 중. 최근 세션에서
실거래 중단급 수정→algoOrder 전환→안전장치→소액 라이브 연결검증→전략 빈도 진단→
검증 인프라 구축까지 완료. 핵심 결론: **전략(OI-급증)이 현 임계로는 사실상 무거래이며,
검증에 필요한 OI 데이터가 부족(~30일)해 실거래 GO는 HOLD.** 본 문서는 컨텍스트를
지워도 다음 세션이 즉시 따라잡도록 전체 상태·관례·남은 일·재개 프롬프트를 담는다.

---

## 1. 저장소 / 환경 (중요 — 작업 메커니즘)
- **작업본**: `C:\Users\jaeho\OneDrive\Desktop\Cursor\BINANCE_FUTURES_BOT` — **git 저장소가 아님**(여기서 코드 수정·실행).
- **git 저장소(클론)**: `C:\Users\jaeho\OneDrive\Desktop\Cursor\BINANCE_FUTURES_BOT_GITHUB` — origin = `kong9365/BINANCE_FUTURES_BOT`.
- **GitHub 반영 = "A안"**: 작업본에서 변경 파일을 클론으로 **명시 복사** → 클론에서 보안체크 → `pytest` → `git add <파일명 명시>`(`git add .`/`-A` 금지) → 커밋 → push.
  - 커밋은 git 신원 미설정이라 `git -c user.name="kong9365" -c user.email="jaehong9365@gmail.com" commit ...` 1회 오버라이드 사용.
  - 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
- **현재 GitHub HEAD = `c48cf3d`**, 전체 테스트 **332 passed**.
- **절대 커밋 금지**: `.env`, `.env.SAFETY_BACKUP`, `logs/`, `reports/`, `data/*.db`, `backtests/cache/`, 실제 키/토큰. (모두 .gitignore 확인됨.)

## 2. .env 상태 (시크릿 값은 절대 출력 금지)
- `USE_TESTNET=true` (안전 기본 — 실거래는 실행 시 커맨드라인 `USE_TESTNET=false` 오버라이드만; `.env`는 true 유지).
- `DB_PATH` **주석 처리됨**(미지정) → `USE_TESTNET=false` 시 `data/bot_live.db`, true 시 `data/bot.db` 자동 분리.
- live 키(`BINANCE_API_KEY/SECRET`) + testnet 키(`BINANCE_TESTNET_*`) + `TELEGRAM_*` + `OPENAI_API_KEY` + `CMC_API_KEY` 모두 설정됨. live/testnet 키는 별개 발급처, `collector._build_client`가 `USE_TESTNET`로 자동 선택.
- 보호종목 6: **BTCUSDT, ETHUSDT, HOLOUSDT, CFXUSDT, LYNUSDT, INJUSDT** (`config/settings.py` `_DEFAULT_PROTECTED_SYMBOLS`, env `PROTECTED_SYMBOLS` 우선).

## 3. 절대 룰 (CLAUDE.md TIER 1 + 본 세션 합의)
- 키/시크릿/Telegram 토큰을 코드·로그·메시지·보고서에 **출력 금지**. (httpx/httpcore INFO 억제 적용 — `main_7590._suppress_http_client_logs`.)
- 보호종목 6개: **신규 진입 금지 + 기존 보유분 청산/취소/수정 금지**(emergency close·reconcile에서도 제외). `close_position`이 보호종목이면 `protected_symbol_close_blocked` 반환.
- 게이트(RiskManager/CostGuard/PairWhitelist/CapitalManager) **완화 금지**.
- 실거래 실행 전 **live read-only preflight 게이트**: 비보호종목 기존 포지션/주문/algo = 0, One-way Mode, 예산 cap. 하나라도 위반 시 차단.
- 신규 거래 예산 = `LIVE_PROBE_CONFIG.live_probe_budget_usdt`(기본 $300) cap.
- **실거래 GO = HOLD** (해제 조건은 §6).

## 4. 완료된 작업 (커밋별)
| 커밋 | 내용 |
|---|---|
| `d46790b` | algoOrder 전환(보호주문 STOP/TP를 `futures_create_algo_order`로 — 2025-12-09 -4120 대응) + STOP 갱신 create-first + A-5 고아 포지션 방지 + A-4 Hedge Mode 차단 |
| `afc38df` | httpx/httpcore INFO 억제(Telegram 토큰 URL 로그 유출 차단) |
| `8096fc6` | 보호종목 INJUSDT 추가(6개) + 기본 차단 테스트 |
| `eeeaed6` | `docs/LIVE_CONNECTION_PROBE_PLAN.md` |
| `daced7c` | **Protected Existing Position Coexist Mode**(기존 보호종목 보유분과 공존, 비보호만 거래) + live probe 예산 cap |
| `2c13c11` | Telegram 알림 강화(시작/preflight/heartbeat(1h)/진입/보호주문/청산/CRITICAL) |
| `9896eea` | 보호종목 변경 알림 CRITICAL→정보성(운영자 자신의 stop은 정상) + 상장폐지(-4108) 스캔 노이즈 제거(skip-set) |
| `aabe47f` | **OIScanner 윈도우 교정**: OI 변화를 30초(스캔간격) 대신 OI 이력 룩백(기본 15m)으로 측정 + `OIScannerConfig` |
| `92b1ad3` | **BacktestEngine `strategy="oi_surge"`**(라이브 OIScanner 재현) + `backtesting/data_loader.py`(OI+OHLCV 캐시/누적) |
| `48b284a` | `backtesting/collect_oi.py` OI 누적 수집 CLI + `docs/OI_COLLECTION.md` |
| `c48cf3d` | OneDrive 경로가 스케줄 실행을 막는다는 caveat 문서화 |

## 5. 핵심 발견 (전략·검증)
- **현 기본 임계(OI≥5% / 가격≥2%)로는 사실상 영원히 무거래** — 실데이터 측정으로 입증:
  - 15분 창: 5,988관측 0신호. 15분 |OI변화| 최댓값 4.50% < 5% 임계 → 수학적으로 거의 불가.
  - 1시간 창: 0신호(기본 임계). 재보정(1h, OI≥1.5%/가격≥0.7%) 시 ~14거래/20.8일 나오나 **표본이 노이즈 수준**(임계마다 부호 뒤집힘) → 수익성 판단 불가.
- 윈도우(30초→15m)는 고쳤으나 **임계도 비현실적**이었음(2차 병목).
- **검증 불가의 근본 원인 = 데이터**: Binance OI 이력 `futures_open_interest_hist` **~30일만** 제공. 명세 §10-4(거래 ≥200건, 다년 OOS) 충족 불가.
- 기존 BacktestEngine은 추세추종 플레이스홀더였음 → `strategy="oi_surge"` 추가로 라이브 전략 백테스트 가능해짐(단 데이터가 30일뿐).
- 라이브 소액 연결검증(30분 + 14h19m): **무오류·무누출·진입 0건**(연결/DB분리/보호종목 공존/예산 cap/토큰억제 모두 정상). INJUSDT는 운영자 자신의 stop으로 청산됨(정상, 봇 무관).

## 6. 현재 라이브 계좌 상태 (참고)
- One-way Mode. 기존 보유: LYNUSDT 포지션 + ETHUSDT 주문(모두 보호종목, 봇 비접근). INJUSDT는 stop으로 청산됨.
- wallet ≈ $3,419 / available ≈ $1,886 (예산 cap $300이 신규 거래 노출 제한).
- 비보호종목 기존 항목 0. preflight 통과 가능 상태.

## 7. 미해결 / 남은 작업 (우선순위)
1. **저장소를 OneDrive 밖으로 이전** (예: `C:\bots\BINANCE_FUTURES_BOT`). 이유: OneDrive 경로라 Windows 작업 스케줄러가 OI 수집을 무인 실행 못 함(`BinanceOICollect` 작업은 등록됐으나 Result 0인데 실제 수집 안 됨; 직접 실행은 정상). **무인 OI 누적의 전제.** .env/clone/스케줄 경로 재정리 동반.
2. **OI 데이터 수개월 누적** (`python -m backtesting.collect_oi` 시간별) → 충분(거래 ≥200건 분량) 시 **oi_surge walk-forward 검증**.
3. **임계/윈도우 재보정** — 검증 데이터 확보 후. 현재 후보: 1h 창 + OI 1~2% + 가격 0.5~1%(단 노이즈, 데이터 더 필요).
4. **백테스트 방향 로직 정합** — `_evaluate_oi_surge`는 가격 모멘텀 부호로 방향 결정(라이브의 QualityGate/regime 필터 미반영) → 데이터 축적 후 정합.
5. **등록된 스케줄 작업 처리** — `BinanceOICollect`(시간별, 배치 래퍼 `run_collect_oi.bat` 사용, 미커밋·머신특정). OneDrive 이전 후 경로 갱신 또는 `schtasks /Delete /TN BinanceOICollect /F`로 정리.
6. **Testnet 전체 생애주기 실관측** (자연 STOP/TP 트리거→reconcile→DB CLOSED) — 라이브 GO 전 권장.
7. **전략 근본 판단**: OI-급증이 데이터/인프라상 검증 가능한지, 아니면 검증 가능한 다른 신호로 전환할지 결정(데이터 누적 결과 보고 판단).

## 8. 검증 명령 (재개 시 상태 확인)
```bash
# (클론에서) 동기화·테스트 상태
cd <repo>; git log --oneline -3            # HEAD c48cf3d 또는 이후
python -m compileall .                     # 에러 0
pytest tests/                              # 332 passed (또는 그 이상)
# OI 신호 빈도/임계 재측정(read-only, 라이브 OI 이력)
#   → 이전 결과: 기본 임계 0신호; 1h+1.5%/0.7%만 노이즈 수준 양(+)
# OI 누적 수집 1회(직접 실행은 OneDrive에서도 정상)
python -m backtesting.collect_oi --limit 500
# oi_surge 백테스트(누적 데이터로)
#   backtesting.data_loader.load_universe + BacktestEngine(strategy="oi_surge")
```
- 보호종목 확인: `python -c "from dotenv import load_dotenv;load_dotenv();from config.settings import PAIR_WHITELIST_CONFIG as p;print(sorted(p.protected_symbols))"` → 6개.
- 실거래 실행은 **금지(HOLD)**. live read-only 점검만(주문 없음).

## 9. 핵심 파일 맵
- `data/oi_scanner.py` — OIScanner(OI 이력 룩백, `oi_lookback_period`/`oi_lookback_count`).
- `data/collector.py` — `get_open_interest_history` + 상장폐지 skip-set(`_delisted_symbols`).
- `trading/executor.py` — algoOrder 보호주문, A-5 `_resolve_fill_timeout`/`_position_state`, A-4 `_ensure_one_way_mode`, 보호종목 `close_position` 가드, preflight 헬퍼.
- `main_7590.py` — `_live_account_preflight`, `_close_all_positions_safe`(보호 제외), Telegram 알림, `_check_protected_unchanged`(정보성), 예산 cap, `_suppress_http_client_logs`.
- `config/settings.py` — `TradeExecutorConfig`/`LiveProbeConfig`/`OIScannerConfig` + 보호종목.
- `backtesting/backtest_engine.py` — `strategy="oi_surge"` `_evaluate_oi_surge`(룩어헤드 차단).
- `backtesting/data_loader.py` / `backtesting/collect_oi.py` — OI+OHLCV 수집/누적.
- `docs/OI_COLLECTION.md` / `docs/LIVE_CONNECTION_PROBE_PLAN.md` — 운영 가이드.

## 10. 다음 세션 재개 프롬프트 (복사용)
> 아래를 새 세션 첫 메시지로 사용. (CLAUDE.md는 자동 로드됨.)

```
이 프로젝트(Binance Futures Bot, C:\Users\jaeho\OneDrive\Desktop\Cursor\BINANCE_FUTURES_BOT)의
이전 세션을 이어서 진행한다. 먼저 C:\Users\jaeho\.claude\plans\python-binance-rosy-lollipop.md
(세션 연속성 핸드오프)와 docs/HANDOFF.md, docs/OI_COLLECTION.md를 읽고 현재 상태를 따라잡아라.

핵심 컨텍스트: GitHub main HEAD는 c48cf3d 이후, pytest 332+ passed, 실거래 GO는 HOLD.
전략(OI-급증)이 현 임계로 사실상 무거래이고, 검증에 필요한 OI 데이터(~30일)가 부족한 상태.
작업 메커니즘은 핸드오프 §1의 "A안"(클론 복사→테스트→명시 add→commit override→push),
보안·보호종목·HOLD 룰은 §3을 절대 준수.

지금 할 일: [여기에 — 예: "(1) 저장소를 OneDrive 밖으로 이전 계획" / "(2) OI 누적 현황 점검 후
oi_surge 백테스트" / "(3) 임계 재보정 설계" 중 택1].
실거래 실행/주문은 금지(read-only 점검만), 코드 변경은 plan 승인 후.
```
