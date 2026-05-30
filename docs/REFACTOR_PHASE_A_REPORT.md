# REFACTOR Phase A + B7 — 안전·정합 + 미체결 계측

> **일자**: 2026-05-30 | **근거**: FINAL_REVIEW_BINANCE_FUTURES_BOT.md (§2 코드 발견 + §4-4 + §5 로드맵)
> **범위**: Phase A(A1~A5 안전·정합, 코드 전용) + Phase B-7(미체결 신호 forward-return 계측)
> **제외**: Phase C/D, 실거래 활성화, 신규 전략, B-3 체결모델 백테스트 정합(=Phase B-6, 별도) — 이번 범위 아님
> **최종 회귀**: **pytest 1056 passed** (회귀 0) | **git 커밋 미실행** (운영자 A-workflow)

---

## 0. 요약

FINAL_REVIEW 의 코드 발견 6건(A1~A5)을 **모두 현재 코드에 직접 대조 검증 후 재현 확인**하고 수정.
추가로 미체결 신호 계측(B7) 신설. 모든 변경은 **사이즈↓·차단↑ 방향만**(게이트 완화 0), 보호종목 로직 무변경.
운영자 결정 3건(D1/D2/D3) + 추가 가드 4건 반영. 항목마다 실패테스트→최소구현→모듈테스트→전체회귀 통과.

---

## 1. 항목별 (발견번호 / 수정 / 검증)

### A1 [2.2] — 사이저 risk-based notional cap
- **발견**: `risk_per_trade_pct`(0.005) 死설정 — `dynamic_sizer.calculate()`가 entry/sl 미수신, 손절거리 미반영.
- **수정**: `calculate()`에 `entry_price/stop_loss/risk_per_trade_pct` optional 추가. `risk_cap_usdt = risk_pct×capital×entry/|entry−sl|` 를 기존 Kelly/명목 cap 과 **min**(축소만, hard ceiling 이라 min_size 하한 아래로도 가능). `main_7590._execute_decision`에서 실제 계획 손절(sl)·`RISK_RULES.risk_per_trade_pct` 주입.
- **가드(추가)**: `|entry−sl| ≤ entry×1e-9`(=sl==entry) → risk-cap 생략(zero-div 폭발 방지). 세 인자 중 None 1개라도 → 비활성(기존 동작 100% 보존).
- **유닛 확인**: `config/settings.py:183 risk_per_trade_pct = 0.005` (분수) 리터럴 직접 검증 — 0.5(퍼센트) 아님.
- **verify**: 다양한 (capital,entry,sl)에서 손절 손실 ≤ 0.5%×capital (독립 달러 단언), binding/non-binding/min하한/zero-dist/backward-compat.

### A2 [2.3] — `_check_daily_trade_count` (레짐별)
- **발견**: `max_daily_trades`(레짐별 5/4/1/2/0) 미강제 — `check_all()` 7체크에 없음.
- **수정**: `_check_daily_trade_count(regime)` + `_get_today_trade_count()`(오늘 UTC 00:00 이후 trades INSERT 수). `check_all(regime=None)` 에 합류, `_execute_decision`에서 `regime_state.regime` 주입.
- **결정 D1=ⓐ**: 레짐별 한도(RANGING=1 등). `regime=None` → 전역 `RISK_RULES.max_daily_trades=5` 폴백. 미정의 레짐도 전역 폴백.
- **verify**: 전역5/레짐별/미지정폴백/한도도달차단/DB실패 fail-closed/check_all 통합.

### A3 [2.7] — close_position no_position 분기 DB 마감
- **발견**: 거래소 STOP 선체결로 포지션 소멸 시 `no_position` 분기가 `_finalize_trade_exit` 없이 success 반환 → trades 행 OPEN 고아.
- **수정**: no_position 분기에서 `_resolve_recent_fill` + `_finalize_trade_exit`(reconcile 동일 패턴)로 CLOSED 마감. 마감 실패는 삼켜 success 보존(거래소 추가 주문 0).
- **verify**: STOP 선체결(포지션0) 시 미청산 행이 CLOSED + 최근체결가로 마감.

### A4 [2.4/2.5] — 진입 maker 수수료 + 청산가 미상 보수적 손실
- **발견(2.4)**: 진입은 GTX(post-only)=maker 인데 `_finalize_trade_exit`가 taker(0.00045)로 오계산.
- **발견(2.5)**: 청산가 미상 시 entry 중립(PnL 0=breakeven)으로 손실 은폐.
- **수정**: `_DEFAULT_MAKER_FEE=0.00018` 신설 → 진입 maker, 청산 taker(reduceOnly MARKET). 청산가 미상 → `trades.stop_loss`로 보수적 손실 + `exit_reason`에 `_stop_fallback` 합성 플래그.
- **결정 D3=maker율**: 실측 commission 캡처는 스키마 변경이라 범위 외(향후 개선).
- **verify**: maker 진입 수수료 반영(실측/추정 2케이스), 청산가 미상→STOP가 손실(breakeven 아님)+플래그.

### A4b [추가 가드] — 합성 청산 통계 처리 (운영자 결정 2026-05-30)
- **문제**: `_stop_fallback`(보수적 합성 손실)을 엣지 통계에 넣으면 새 편향(실제 TP였을 손익을 손실로 오기록).
- **수정**: `analytics/expectancy._fetch_closed_trades`에서 `_stop_fallback` **제외**(win-rate/expectancy/avg_R — 사이징 입력 편향 방지).
- **정정(2026-05-30)**: 연패(`risk_manager._get_loss_streak`)는 **안전 게이트**이므로 합성 손실을 **포함**(과차단=보수적 방향). 단 별도 로직 추가 없이 — `_stop_fallback` 행은 이미 pnl<0이라 자연 포함. 중립폴백(stop 없음)은 H1/C2(진입은 STOP 성공 후에만 기록)상 도달 불가라 별도 처리 없음(Simplicity: 불가능 시나리오 핸들링 금지).
- **verify**: expectancy 합성 제외(+정상 대조), loss-streak 합성 포함(streak==3, +정상 대조).

### A5 [2.10] — 안전 기본값 (LIVE_TRADING_ENABLED opt-in)
- **발견**: collector `use_testnet=False` 기본 + `--dry-run` 기본 False → 무플래그·무env = mainnet 실주문.
- **수정 (D2=ⓑ 진입점+executor 2중)**:
  - 진입점: `main_7590._resolve_effective_dry_run` — mainnet & not dry_run & `LIVE_TRADING_ENABLED`≠true → dry_run **강제**(경고). testnet·명시 opt-in 불변, 실거래 비활성화는 안 함(안전쪽만).
  - executor: `_live_trading_authorized()` 독립 검사(env 직접) — live 진입 직전 미인가면 거부(main 게이트 우회 대비 최후 보루).
- **가드(추가)**: 양성 테스트(LIVE_TRADING_ENABLED=true+mainnet+not dry_run → live 허용) 포함 — 가드가 정당한 실거래를 막지 않음 확인. test_executor autouse 픽스처로 기존 live 테스트 인가.
- **verify**: 음성(무플래그→실주문X)+양성(opt-in→허용)+dry_run 대조+executor 차단(실주문 0,행 0).

### B7 [4-4] — 미체결 신호 영속화 + forward-return 스키마
- **발견**: post-only 미체결/스킵(`_await_fill` 타임아웃) 신호가 폐기됨 — B-3(체결 역선택) 검증 데이터 손실.
- **수정**: DB v3.2.3 마이그레이션 `unfilled_signals`(ts/symbol/action/setup_tag/signal_price/reason + `fwd_return_1/3/6bar` nullable). `executor._record_unfilled_signal(decision,reason)` 훅을 enter_trade 미체결 분기에 추가.
- **가드(추가)**: 훅은 **거래 경로와 완전 격리** — 내부 try/except 로 어떤 실패도 삼킴(로그만). 계측이 매매를 깨면 안 됨(CLAUDE.md §4).
- **범위**: 스키마/적재 훅만. forward-return 채움·분석 리포트는 범위 외.
- **verify**: live-mock 미체결→unfilled_signals 1행, 정상체결→trades만(구분 조회), DB실패 격리(예외 미전파). 마이그레이션 적용/컬럼/멱등.

---

## 2. 회귀 추이 (항목마다 전체 pytest, 회귀 0)

| 단계 | 모듈 | 전체 |
|---|---|---|
| A1 | dynamic_sizer 17 | 1024 |
| A2 | risk_manager 25 | 1031 |
| A3 | executor 56 | 1032 |
| A4+A4b | 93 | 1041 |
| A5 | 69 | 1050 |
| B7 | 71 | 1056 |
| **A4b 정정** | risk+expectancy 33 | **1056** |

> **환경 메모**: 프로젝트가 OneDrive 폴더라 pytest full suite 가 `.pyc`(__pycache__) 쓰기에서 OneDrive 동기화 락에 막혀 멈춤(I/O wait). → **`python -B`(PYTHONDONTWRITEBYTECODE) + OneDrive 일시중지**로 ~3분 복귀. 테스트 정확도엔 무영향. 향후 회귀는 `-B` 권장.

---

## 3. 변경 파일

**소스 (수정)**: `sizing/dynamic_sizer.py`(A1) · `trading/risk_manager.py`(A2,A4b) · `trading/executor.py`(A3,A4,A5,B7) · `analytics/expectancy.py`(A4b) · `main_7590.py`(A1,A2,A5) · `db/init_db.py`(B7)
**소스 (신규)**: `db/migrations/v3_2_2_to_v3_2_3.sql`(B7)
**테스트 (수정)**: `tests/test_dynamic_sizer.py` · `tests/test_risk_manager.py` · `tests/test_executor.py` · `tests/test_expectancy.py`
**테스트 (신규)**: `tests/test_main_safety.py`(A5) · `tests/test_init_db_v3_2_3.py`(B7)

### ⚠️ M15-fix 분리 (Phase A 커밋 제외 — 별도 처리)
`main_7590.py` 최상단 **load_dotenv 블록**(dotenv import 직후) + `tests/test_main_dotenv_order.py` + `docs/HANDOFF.md` M15-fix 항목 = M15-fix 번들. Phase A 편집과 물리적 분리 → `git add -p`로 load_dotenv hunk 제외, `git diff --cached | grep load_dotenv` 로 최종 확인.

---

## 4. 운영자 결정 이력

| 일자 | 항목 | 결정 |
|---|---|---|
| 2026-05-30 | D1 (A2 한도) | ⓐ 레짐별 (regime=None→전역5 폴백) |
| 2026-05-30 | D2 (A5 가드) | ⓑ 진입점 + executor 2중 방어 |
| 2026-05-30 | D3 (A4 진입수수료) | maker율(0.00018), 실측 캡처는 범위 외 |
| 2026-05-30 | A4b 정정 | expectancy 제외 유지 / loss-streak 합성 포함(보수적). 중립폴백은 도달불가(H1/C2)라 별도 로직 없음 |

---

## 5. 다음 단계 (Phase B/C 인계)

이번 범위 **밖**으로 남긴 항목 (FINAL_REVIEW §5 로드맵):
- **B-6 (최중요, B-3)**: 백테스트 체결모델 현실화 — post-only 체결확률·미체결 스킵·슬리피지. *라이브와 백테스트가 같은 게임을 하게* 만드는 핵심. (B7이 그 결정 증거 계측 인프라.)
- **B-8**: 테스트넷 end-to-end 1주기 (자연 STOP/TP→reconcile→CLOSED 무오류).
- **Phase C**: 현실 체결모델 재백테스트 → oi_surge/breakout/daily_tsmom 엣지 재판정. CostGuard default_win_rate 손익분기 아래로(2.1/B-4).
- **Phase D**: 실거래 GO 조건(전부 충족 시에만) — 현재 HOLD.

> B7 의 `unfilled_signals`에 forward-return(+1/+3/+6bar)을 채우는 분석 훅은 Phase B 에서 작성하면, 미체결(달려서 승자) vs 체결(반전 패자) 비교로 B-3 를 소표본 증명 가능.
