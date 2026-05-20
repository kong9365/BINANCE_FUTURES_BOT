# 모듈 완료 검증 체크리스트 (v3.1.1)

> **v3.1 → v3.1.1 변경**: 세션 2.5 (CapitalManager + 보호 종목) 신규, 세션 1·2·9·12 일부 항목 추가.

각 세션이 끝나면 해당 모듈의 모든 체크박스가 ✅ 인지 확인. 하나라도 비어있으면 추가 작업 필요.

---

## 공통 체크리스트 (모든 모듈)

```
☑ 명세서 docs/SPEC_v3.1.md §해당섹션과 일치
☑ ★ v3.1.1: docs/SPEC_v3.1_APPENDIX_E.md의 변경 사항 반영 (해당 시)
☑ 모든 메서드에 type hint
☑ 모든 public 메서드에 한국어 docstring (Args/Returns)
☑ logger = logging.getLogger(__name__) 사용
☑ try/except로 외부 호출 보호
☑ tests/test_<module>.py 존재
☑ 해당 단위 테스트 모두 pass (전체 222개 통과)
☑ ruff/flake8 lint 오류 없음 (또는 의도적 무시 표시)
```

---

## 세션 1 — DB 스키마 (v3.1.1)

```
☑ db/schema.sql 파일 존재
☑ 명세서 §13-1의 trades 테이블 모든 컬럼
  ☑ id, timestamp, symbol, action, entry_price, exit_price
  ☑ quantity, leverage, take_profit, stop_loss
  ☑ setup_tag, pnl_usd, pnl_pct, fees_usd
  ☑ duration_seconds, exit_reason
  ☑ regime, regime_confidence, cost_guard_ev
  ☑ slippage_actual_pct, pair_tier, pnl_usd_net
  ☑ manual_intervention, sizing_kelly_raw, sizing_pct
  ☑ ★ v3.1.1: wallet_balance_at_entry
  ☑ ★ v3.1.1: available_at_entry
  ☑ ★ v3.1.1: locked_margin_at_entry
☑ shadow_decisions 테이블 (blocked_by 컬럼 포함)
☑ §13-2의 8개 신규 테이블 모두 생성
  ☑ regime_history, regime_candidates
  ☑ weekly_reports, gpt_call_log
  ☑ system_health_log, pair_whitelist_history
  ☑ macro_events_cache, backtest_runs
☑ ★ v3.1.1: capital_initial 테이블 (부록 E-7-2)
☑ ★ v3.1.1: capital_daily_snapshot 테이블 (부록 E-7-3)
☑ 모든 INDEX 생성됨
☑ db/migrations/v3_0_to_v3_1.sql 작성
☑ ★ db/migrations/v3_1_to_v3_1_1.sql 작성
☑ db/init_db.py 실행 가능 → data/bot.db 생성
☑ schema_migrations에 'v3.1', 'v3.1.1' 행 존재
☑ tests/test_db_init.py 통과 (7개)
```

---

## 세션 2 — Config (v3.1.1)

```
☑ config/settings.py 모든 dataclass 정의
  ☑ SystemConfig, RegimeConfig, CostGuardConfig
  ☑ SizingConfig, RegimeTradingParams (5개 인스턴스)
  ☑ RiskRules, WeeklyAnalystConfig, MacroEventConfig
  ☑ HealthMonitorConfig, PairWhitelistConfig, BacktestConfig
  ☑ ★ v3.1.1: CapitalManagerConfig (부록 E-6-1)
☑ 명세서 §14의 모든 기본값 정확히 일치
  ☑ max_daily_loss_pct = 0.015
  ☑ risk_per_trade_pct = 0.005
  ☑ max_concurrent_positions = 1
  ☑ max_leverage_by_tier = {1: 5, 2: 3, 3: 3, 0: 0}
  ☑ stability_streak_required = 3
☑ ★ v3.1.1: PairWhitelistConfig.protected_symbols 추가
  ☑ protected_symbols = ["BTCUSDT", "ETHUSDT", "HOLOUSDT", "LYNUSDT"]
    (LYNUSDT는 운영 중 교체 — 환경변수 PROTECTED_SYMBOLS 우선)
☑ ★ v3.1.1: CapitalManagerConfig
  ☑ cache_ttl_seconds = 10.0
  ☑ forbid_spot_access = True
☑ field(default_factory=...) 패턴 (mutable default 회피)
☑ 모든 *_CONFIG 인스턴스 export (CAPITAL_MANAGER_CONFIG 포함)
☑ config/macro_events.yaml 작성됨 (9개 이벤트)
☑ config/__init__.py에서 import 가능
☑ tests/test_settings.py 통과 (21개)
  ☑ ★ test_pair_whitelist_protected_symbols (LYNUSDT 포함 검증)
  ☑ ★ test_capital_manager_config 기본값 검증
```

---

## 세션 2.5 ★ NEW — CapitalManager + 보호 종목 통합 (v3.1.1)

### CapitalManager
```
☑ data/capital_manager.py 작성
☑ @dataclass CapitalSnapshot
  ☑ wallet_balance, margin_balance, available_balance
  ☑ locked_margin, unrealized_pnl, timestamp
  ☑ margin_utilization_pct 프로퍼티
  ☑ to_dict() 메서드
☑ class CapitalManager
  ☑ __init__ — cache_ttl_seconds, forbid_spot_access
  ☑ get_snapshot(force_refresh) — 비동기
  ☑ _call_futures_account() — asyncio.to_thread
  ☑ _parse_account(account, ts)
  ☑ _update_daily_start(snapshot)
  ☑ get_initial_capital()
  ☑ get_daily_start_capital()
  ☑ set_initial_capital(value) — 재시작 시 복원
  ☑ get_cached_snapshot()
  ☑ get_spot_balance() → ValueError (Spot 차단)
☑ tests/test_capital_manager.py 8개 시나리오 통과
  ☑ 1. 정상 응답 → 모든 필드 파싱
  ☑ 2. 캐시 동작 (TTL)
  ☑ 3. force_refresh
  ☑ 4. 초기 자본 기록
  ☑ 5. 일일 시작 잔고 갱신
  ☑ 6. API 실패 + 캐시 → 폴백
  ☑ 7. API 실패 + 캐시 없음 → raise
  ☑ 8. get_spot_balance() → ValueError
☑ binance_client mock 사용 (실제 호출 없음)
```

### PairWhitelist 보호 종목 통합
```
☑ strategy/pair_whitelist.py에 v3.1.1 변경 적용
  ☑ __init__ protected_symbols 파라미터
  ☑ 초기 등록 시 protected_symbols → tier=0
  ☑ is_allowed() 최우선 차단 로직
  ☑ refresh() 보호 종목 검증 스킵
  ☑ manual_unblock() 보호 종목 거부 (경고 로그)
☑ tests/test_pair_whitelist.py — 기존 7 + 신규 4 = 11개 통과
  ☑ 1~7. 기존 시나리오 (§8-7-3)
  ☑ ★ 8. protected_symbols 차단
  ☑ ★ 9. refresh() 후 차단 유지
  ☑ ★ 10. manual_unblock 거부
  ☑ ★ 11. get_active() 결과에 보호 종목 미포함
☑ 통합 검증:
  ☑ pw = PairWhitelist(mock, protected_symbols=["BTCUSDT"])
  ☑ pw.is_allowed("BTCUSDT", 10000) == False
  ☑ pw.is_allowed("SOLUSDT", 10000) == True
```

---

## 세션 3 — CostGuard (변경 없음)

```
☑ (기존 v3.1 체크리스트 그대로)
☑ strategy/cost_guard.py 작성
☑ @dataclass CostGuardResult
☑ tests/test_cost_guard.py 8개 시나리오 통과
```

---

## 세션 4 — DynamicPositionSizer (변경 없음)

```
☑ (기존 v3.1 체크리스트 그대로)
☑ sizing/dynamic_sizer.py 작성
☑ tests/test_dynamic_sizer.py 7개 시나리오 통과
```

> v3.1.2 정정 3건 (사용자 승인): ① kelly_fraction 경계값 `< 1000` → `<= 1000`,
> ② §8-3-3 #2 본문 "~6%"는 cap 미반영 부정확치 → 실제 10.0%,
> ③ Kelly 음수 분기 `_zero_result` 인자 순서 버그 수정. 모두 인라인 주석 명시.

---

## 세션 5 — RegimeDetector (변경 없음)

```
☑ (기존 v3.1 체크리스트 그대로)
☑ strategy/regime_detector.py 작성 (명세서 §8-1-2 1:1 + docstring/입력검증 로그 보강)
☑ class Regime 상수 5개 / @dataclass RegimeState 전체 필드
☑ 보조 지표 함수 5개 (_calc_adx, _calc_ema_slope, _calc_bb_width_pct,
   _calc_atr_ratio, _has_extreme_candle)
☑ detect() 4단계 (HIGH_VOL 즉시 → 후보 분류 → 안정성 룰 → UNCERTAIN)
☑ _calc_confidence() 레짐별 / force_set_regime() 운영자 API
☑ tests/test_regime_detector.py — 8개 시나리오 + 9-a/9-b 통합 스모크 = 10개 통과
☑ 통합 검증: up 캔들 → TREND_UP / macro_blocked → HIGH_VOL 확인
☑ 전체 스위트 72개 통과 (회귀 없음)
```

> v3.1.2 정정 2건 (사용자 승인): ① 안정성 룰 통과 시 `regime_changed`가 항상 False였던
> 버그, ② HIGH_VOL 분기에서 `_confirmed_regime` 미갱신 → 알림 폭격 버그. 둘 다
> `detect()` 진입 스냅샷 기준 재계산으로 수정, 인라인 주석 명시.
> 자세한 내용: `docs/CORRECTIONS_v3.1.2.md`

---

## 세션 6 — MacroEventAnalyzer (변경 없음)

```
☑ (기존 v3.1 체크리스트 그대로)
☑ analytics/macro_event_analyzer.py 작성
☑ tests/test_macro_event_analyzer.py 5개 시나리오 통과
☑ config/macro_events.yaml 갱신 (향후 90일 9개 이벤트, web_search 검증)
```

---

## 세션 7 — SystemHealthMonitor (변경 없음)

```
☑ (기존 v3.1 체크리스트 그대로)
☑ ops/__init__.py 작성
☑ ops/system_health_monitor.py 작성
☑ tests/test_system_health_monitor.py 6개 시나리오 통과
☑ v3.1.2 정정 4건 적용 (get_server_time dict / deque 300 / _rest_stats 파라미터화 / checked_at UTC)
☑ docs/CORRECTIONS_v3.1.2.md 세션 7 기록
☑ 전체 회귀 85개 통과 (79 + 6, 회귀 없음)
```

---

## 세션 8 ★ SKIP — PairWhitelist (세션 2.5에서 완료)

세션 2.5에서 이미 작성되었으므로 이 세션은 건너뜁니다.

---

## 세션 9 — RiskManager (v3.1.1 강화)

```
☑ trading/__init__.py 작성
☑ trading/risk_manager.py 작성
☑ @dataclass CheckResult (passed, reason)
☑ __init__ — capital_manager 의존성 주입 (★ v3.1.1)
☑ check_all() — 비동기, capital_manager.get_snapshot() 호출 (★ v3.1.1)
  ☑ DB 의존 체크는 asyncio.to_thread 래핑
  ☑ snapshot/initial/daily_start 미준비 시 즉시 차단 (안전 우선)
☑ _check_daily_loss(current, daily_start) — wallet_balance 기준 (★ v3.1.1)
☑ _check_total_drawdown(current, initial) — initial 기준 (★ v3.1.1)
☑ _check_consecutive_losses()
  ☑ 3연패 → 4h 쿨다운   (시간 기반 차단은 _check_cooldown 담당)
  ☑ 5연패 → 24h 쿨다운  (시간 기반 차단은 _check_cooldown 담당)
  ☑ 7연패 → 7d terminal (_check_consecutive_losses에서 즉시 차단)
☑ _check_monthly_drawdown — 당월 실현손익 역산 (입금 시 보수적)
☑ _check_concurrent_positions — 열린 포지션 수 vs 자본별 한도
☑ _check_min_balance(available) — available_balance 기준 (★ v3.1.1)
☑ _check_cooldown — 연패 기반 쿨다운 잔여 시간 (3→4h / 5→24h / 7→168h)
☑ get_max_concurrent_positions(capital)
  ☑ <$5k: 1, <$20k: 2, >=$20k: 3
☑ get_max_leverage(tier, regime)
☑ DB 의존성 (trades 조회) — DB 조회 실패 시 안전 차단(passed=False)
☑ tests/test_risk_manager.py — 기존 10 + 신규 4 = 14개 통과
  ☑ 1~10. 기존 시나리오 (§9)
  ☑ ★ 11. wallet_balance=$950, daily_start=$1000 → 차단
  ☑ ★ 12. wallet_balance=$840, initial=$1000 → 차단
  ☑ ★ 13. available_balance=$80 → 차단
  ☑ ★ 14. capital_manager 미준비 → 차단 (안전 우선)
☑ 임시 DB로 테스트 (tmp_path)
☑ capital_manager mock 주입
☑ 전체 회귀 99개 통과 (85 + 14, 회귀 없음)
```

> 설계 메모: §9-2는 check_all / get_max_* 시그니처만 본문 제시, 부록 E-4-2는
> _check_daily_loss / _check_total_drawdown / _check_min_balance 본문 제시.
> 나머지 DB 의존 체크 4개는 RISK_RULES 임계값 기준으로 구현 (임의 완화 0건).
> 3·5연패는 _check_cooldown(시간 기반), 7연패 terminal은 _check_consecutive_losses가
> 담당하도록 분리 — check_all 평가 순서상 _check_consecutive_losses가 먼저 차단.
> 명세서 정정 발견 0건 → CORRECTIONS_v3.1.2.md 추가 없음.

---

## 세션 10 — WeeklyGPTAnalyst + ExpectancyAnalyzer

```
☑ analytics/__init__.py 생성
☑ analytics/expectancy.py 작성 (ExpectancyAnalyzer — §8-4 의존성)
  ☑ @dataclass Stats (trade_count, win_rate, avg_R, expectancy_R, total_pnl_usdt
     + setup_tag 선택적 필드 — 충돌 ① 처리)
  ☑ overall / by_setup / by_regime
  ☑ get_win_rate(return_count) / get_avg_R
  ☑ R-multiple 계산 (LONG/SHORT, stop 누락 시 R 집계 제외)
  ☑ 빈 결과 → _default_stats(), DB 실패 → logger.error + default
☑ analytics/weekly_report.py 작성 (WeeklyGPTAnalyst — §8-4-2 1:1 + 보강)
  ☑ @dataclass WeeklyReport (SPEC §8-4-2 필드 그대로)
  ☑ __init__ — openai_client + expectancy_analyzer 주입, GPT 가격 파라미터화
  ☑ async run(days=7) — 보강 A·B
  ☑ async _call_gpt — asyncio.to_thread 래핑, 비용 계산, 폴백
  ☑ _build_prompt — SPEC §8-4-2 프롬프트 1:1
  ☑ _get_cost_metrics — 슬리피지/GPT 비용 집계
  ☑ _save_report_file — weekly_YYYY-MM-DD.json (보강 E)
  ☑ _save_report_db — weekly_reports + gpt_call_log (보강 C)
  ☑ ★ v3.1.2 정정 ① — naive datetime → UTC tz-aware
☑ tests/test_expectancy.py — 3개 시나리오 통과
  ☑ 1. 10건 (승6/패4) → win_rate=0.6, avg_R=0.8, total_pnl=40
  ☑ 2. 0건 → default Stats
  ☑ 3. by_setup 분리 (TREND_PULLBACK 5 / RANGING_MR 3)
☑ tests/test_weekly_report.py — 4개 시나리오 통과
  ☑ 1. 정상 호출 → JSON 파일 + weekly_reports 1행 + gpt_call_log 1행
  ☑ 2. GPT 실패 (APITimeoutError) → 폴백 보고서, DB행 생성, cost=0
  ☑ 3. trades 빈 DB → trade_count=0, GPT는 호출됨
  ☑ 4. 비용 메트릭 정상 집계 (슬리피지 평균 / GPT 비용 합산)
☑ 임시 DB(tmp_path) + 임시 report_dir 사용, openai_client mock 주입
☑ 전체 회귀 106개 통과 (99 + 7, 회귀 없음)
☑ docs/CORRECTIONS_v3.1.2.md 세션 10 정정 ① 기록
```

> 보강 5건 (사용자 지시, 인라인 주석 `보강 A~E`): A) run/_call_gpt async화,
> B) expectancy_analyzer 생성자 주입, C) gpt_call_log 기록 추가,
> D) GPT 실패 시 폴백 insights 문구 고정, E) 파일명 weekly_YYYY-MM-DD.json
> (같은 날 재실행 시 덮어쓰기 — 인라인 주석 명시). GPT 가격은 하드코딩 대신
> 생성자 파라미터로 노출 (config 미수정).
> 정정 1건: v3.1.2 정정 ① (naive datetime → UTC tz-aware, 세션 5·7과 동일 카테고리).

---

## 세션 11 — BacktestEngine (완료)

```
☑ backtesting/__init__.py 작성
☑ backtesting/backtest_engine.py 작성 (룩어헤드 5단계 차단, §10-2·§10-5)
☑ backtesting/walk_forward.py 작성 (§10-3, Window/_generate_windows/walk_forward)
☑ backtesting/plots.py 작성 (equity/drawdown/monthly heatmap, Agg 백엔드)
☑ tests/test_backtest_engine.py 통과 (시나리오 1~3 + 룩어헤드 4-a/4-b = 5개)
☑ 전체 회귀 111 passed (106 + 5)
☑ 정정 2건 — docs/CORRECTIONS_v3.1.2.md 세션 11 참조
  ① BacktestConfig.start_date/end_date 기본값 부여
  ② _get_candles_until(symbol,tf,ts) 시그니처 + walk_forward.py 모듈 분리
```

---

## 세션 12 — main_7590.py 통합 (v3.1.1 강화)

```
☑ main_7590.py 작성
☑ MainBot.__init__
  ☑ ★ v3.1.1: CapitalManager 초기화 (cache_ttl, forbid_spot_access)
  ☑ ★ v3.1.1: PairWhitelist(protected_symbols=...) 주입
  ☑ ★ v3.1.1: RiskManager(capital_manager=...) 주입
  ☑ ★ _current_capital 멤버 제거 (capital_manager로 일원화)
☑ start() 시퀀스 (★ v3.1.1):
  ☑ 초기 자본 조회 + 로그 ([Start] 초기 자본: wallet/available/locked_margin)
  ☑ ★ 초기 자본 영속화 (capital_initial 테이블 복원/기록 — 부록 E-7-2)
  ☑ 보호 종목 Telegram 알림
  ☑ _verify_api_key_permissions() 실행
☑ _main_loop / _iter
  ☑ Layer 0: SystemHealthMonitor.check()
  ☑ ★ capital_snapshot = await capital_manager.get_snapshot()
  ☑ Layer 2: regime_state + macro_blocked + 레짐 전환 시 regime_history 기록
  ☑ ★ ExitPlanController 기존 포지션 자동 관리 (_manage_open_positions)
  ☑ Layer 3+4: 신호 + 게이트
  ☑ Layer 5: 실행
  ☑ Layer 6: 기록
☑ _handle_signal(candidate, regime_state, capital_snapshot)
  ☑ pair_wl.is_allowed() — protected_symbols 차단 작동
  ☑ ★ sizer.calculate(capital=capital_snapshot.available_balance)
  ☑ ★ size_usdt > available 차단 추가 검증
  ☑ 각 차단 단계 shadow.record_blocked / 진입 시 shadow.record
☑ _maybe_run_weekly() — 일요일 23 UTC
☑ _maybe_refresh_pairs() — 일 1회
☑ _close_all_positions_safe()
☑ ★ _verify_api_key_permissions() — Spot 권한 감지 시 경고

☑ data/__init__.py / strategy/__init__.py — 패키지 초기화 (import 복구)
☑ data/collector.py — BinanceDataCollector (REST + WS 콜백 훅)
☑ data/oi_scanner.py — OIScanner
☑ strategy/oi_filter.py — OIFilter
☑ strategy/quality_gate.py — QualityGate
☑ trading/executor.py — TradeExecutor
  ☑ ★ enter_trade(decision) — wallet/available/locked_margin_at_entry INSERT
  ☑ close_position(symbol, reason, portion) — 전량/분할 청산
☑ trading/exit_plan.py — ExitPlanController (분할 TP / ATR 트레일 / BE / 시간 스톱)
☑ analytics/shadow_mode.py — ShadowRecorder

☑ WebSocket 이벤트 핸들러:
  ☑ _on_ws_kline → record_ws_kline_received
  ☑ _on_ws_user_data → record_ws_user_received
  ☑ _on_rest_call_complete → record_rest_call

☑ SIGTERM 핸들러 — signal.signal(SIGTERM, _handle_termination_signal) 로
  SIGTERM → KeyboardInterrupt 변환 → _amain finally(shutdown) → main except
  경로로 graceful 종료. 운영 배포(Linux: kill/systemd/docker stop) 정상 대응.
  Windows 네이티브는 SIGTERM 즉시 종료 제약이 있으나 등록은 안전 (Ctrl+C +
  --duration 으로 보완).

☑ tests/test_main_integration.py — 시나리오 (8개 통과):
  ☑ 봇 시작 → 보호 종목 Telegram 알림 검증 (BTC/ETH/HOLO/CFX)
  ☑ ★ 봇 시작 → capital_initial 테이블 기록 + 재시작 복원
  ☑ BTCUSDT / LYNUSDT 시그널 → is_allowed=False, 진입 안 됨
  ☑ SOLUSDT 시그널 → 모든 게이트 통과 → 가상 진입 + exit_plan 추적 + shadow 기록
  ☑ available_balance < size_usdt → 마진 부족 추가 검증으로 차단
  ☑ C_DANGER 신호 → shadow.record_blocked
☑ Testnet dry-run (75~100초) — 유효 Testnet 키로 실행 검증 완료:
  ☑ ERROR 로그 없음, traceback 없음, exit 0
  ☑ "🛡️ 보호 종목 활성" Telegram 알림 수신 (200 OK)
  ☑ 초기 자본 $5000 조회 / OIScanner 스캔 / CMC 시총 필터링 동작
  ☑ duration 경과 시 graceful shutdown
  ※ wallet vs available 차이는 포지션 보유 시에만 발생 — 장시간 페이퍼에서 확인 (운영자)
```

---

## 최종 통합 검증 (v3.1.1)

13개 세션 모두 완료 후:

```
☑ `pytest tests/ -v` 모든 테스트 통과 — 222개 통과
  ☑ v3.1 기존 케이스 + 세션 12 신규 (collector 23 / oi_scanner 10 / oi_filter 12 /
     quality_gate 11 / executor 15 / exit_plan 15 / shadow 7 / main_integration 8 /
     cmc_client 10)
  ☑ ★ v3.1.1 신규 테스트 (CapitalManager 8 + PairWhitelist 11 + RiskManager 14)
☑ `python main_7590.py --dry-run --duration N` 정상 종료 (exit 0, ERROR 없음)
□ Testnet 환경 1시간 페이퍼 — 운영자 환경에서 실행 (장시간):
  □ 로그에 ERROR 없음
  □ DB에 trades 또는 shadow_decisions 행
  □ regime_history 1건 이상
  □ ★ capital_daily_snapshot에 오늘 날짜 행
  □ ★ capital_initial에 최초 자본 기록 (☑ dry-run 에서 기록·복원 확인됨)
  □ Telegram 알림 정상 (☑ dry-run 에서 200 OK 확인)
  □ ★ 보호 종목 알림 수신 (☑ dry-run 에서 확인)
□ docs/SPEC_v3.1.md §15 Phase 1 완료 조건 충족 — 운영자 판단
□ ★ docs/SPEC_v3.1_APPENDIX_E.md §E-9-2 자체 점검 체크리스트 — 운영자 수동 검증
□ Git commit + tag v3.1.1 — 미실행 (프로젝트가 git 저장소 아님)
```

---

## ★ v3.1.1 보호 종목 정책 자체 점검 (별도 검증)

> ☑ = 코드/테스트/dry-run 으로 검증 완료 · □ = 운영자가 실거래/Spot 환경에서 수동 검증 필요

```
□ Binance API 키 권한 확인 (운영자 — Binance UI)
  □ Futures only (Spot 비활성화)
  □ Withdrawals 비활성화
  □ Universal Transfer 비활성화

봇 시작 시 자동 검증
  ☑ "🛡️ 보호 종목 활성: BTCUSDT, ETHUSDT, HOLOUSDT, LYNUSDT" 알림 (dry-run 확인)
  ☑ API 키 권한 검증 통과 (Spot 권한 없음 확인 — dry-run 로그 [Security])
  ☑ 초기 자본 로그 출력 ([Start] 초기 자본: wallet/available/locked_margin)

실제 충돌 시나리오 검증
  □ 운영자가 Spot에서 BTC 추가 매수 → 봇 자본 표시 변화 없음 (운영자 수동)
  □ 운영자가 Spot에서 ETH 일부 매도 → 봇 자본 표시 변화 없음 (운영자 수동)
  ☑ OIScanner가 BTCUSDT 신호 반환 → _handle_signal에서 is_allowed=False 차단
     (tests/test_main_integration.py 검증)
  ☑ 봇이 SOLUSDT 진입 → wallet_balance_at_entry 기록 확인
     (tests/test_main_integration.py 검증)

자본 분리 검증
  □ wallet_balance 일일 시작 vs 현재 차이 = 오늘 봇 손익만 반영 (운영자 — 장시간)
  ☑ Spot 가격 변동이 자본 계산에 영향 없음
     (CapitalManager 는 futures_account 만 조회 / get_spot_balance() → ValueError)

마진 락 검증
  □ 봇이 포지션 1개 진입 → locked_margin > 0 (운영자 — 실포지션 필요)
  ☑ 새 신호 시 available_balance 기준으로 사이즈 계산 (_handle_signal 구현 + 테스트)
  ☑ 사이즈 > available 시 차단 로그 확인 (tests/test_main_integration.py 검증)
```

Phase 0 (백테스트) 진행 가능 여부는 위 모든 항목 통과 후 결정.

---

## 남은 작업 요약 (2026-05-14 기준)

**코드/구현: 13개 세션 전부 완료, 222개 테스트 통과, dry-run 검증 완료.**

운영자/환경 작업만 남음:
1. Binance 실거래 API 키 권한 확인 (Futures only / Withdrawals·Universal Transfer 비활성화)
2. 운영 PC에서 Testnet 장시간 페이퍼 (1시간+) — 실포지션 진입/청산/마진 락 확인
3. Spot 충돌 시나리오 수동 점검 (운영자가 Spot에서 BTC/ETH 거래 → 봇 자본 무영향 확인)
4. (선택) Git 저장소 초기화 + commit + tag v3.1.1
5. (선택) 파일 로깅/로테이션, 실 WebSocket 연결 — 운영 품질 보강 (현재 콘솔 로그 + REST 폴링)
