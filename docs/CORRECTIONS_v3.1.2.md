# v3.1.2 정정 사항 누적 메모

> 명세서 `SPEC_v3.1.md` / `SPEC_v3.1_APPENDIX_E.md`의 **명백한 버그**를 세션 작업 중
> 발견했을 때, 사용자 승인을 거쳐 코드를 정정하고 그 내역을 누적 기록한다.
>
> **원칙**
> - 사소한 부정확(본문 추정치 등) → 본문 정정만, 코드 변경 없음
> - 명백한 버그(인자 순서, 상태 갱신 누락 등) → 코드 수정 + 인라인 주석 + 이 메모 기록
> - 모든 정정은 사용자 승인 후에만 적용. 인라인 주석은 `# v3.1.2 정정` 으로 시작.

---

## 세션 4 — DynamicPositionSizer (`sizing/dynamic_sizer.py`)

정정 3건 (사용자 승인):

| # | 위치 | 내용 |
|---|---|---|
| 1 | §8-3-2 `kelly_fraction_for_capital` | 경계값 `< 1000` → `<= 1000`. §8-3-1 책임 명세 + §8-3-3 #1 시나리오 모두 정확히 $1,000을 Quarter-Kelly로 처리하므로 보수적으로 정정. |
| 2 | §8-3-2 Kelly 음수 분기 | `_zero_result(0, kelly_raw, ...)` → `_zero_result(kelly_raw, 0, ...)`. 인자 순서가 뒤바뀌어 `kelly_raw` / `kelly_fraction_used` 필드 의미가 교차됨. |
| 3 | §8-3-3 #2 본문 | 본문 "~6%" 추정치는 cap 미반영 부정확치 → 실제 10.0%. (본문 정정만, 코드 변경 없음) |

---

## 세션 5 — RegimeDetector (`strategy/regime_detector.py`)

정정 2건 (사용자 승인). 둘 다 §8-1-2의 `regime_changed` 산출 로직 버그로,
운영 시 `main_7590.py`의 레짐 전환 감지(`if regime_state.regime_changed:` →
Telegram 알림 + `regime_history` 로깅)를 직접 망가뜨린다.

### 정정 ① — 안정성 룰 통과 시 `regime_changed`가 항상 False

**버그**: `detect()`의 안정성 룰 통과 분기는 `_apply_stability()`를 먼저 호출한다.
`_apply_stability()`는 확정 전환 시 `self._confirmed_regime`을 **새 레짐으로 갱신**한다.
그 직후 호출되는 `_make_state()`는 `prev = self._confirmed_regime`를 읽으므로
`prev == regime`이 되어 `changed = prev != regime` 이 **항상 False**가 된다.
→ 3봉 연속 조건을 만족해 실제로 레짐이 바뀌었는데도 전환 이벤트가 발생하지 않음.

**수정**: `detect()` 진입 시점에 `prev_regime_snapshot = self._confirmed_regime`을
보존하고, 안정성 룰 통과 분기에서 스냅샷 기준으로 재계산:
```python
state.prev_regime = prev_regime_snapshot
state.regime_changed = (prev_regime_snapshot != candidate_regime)
```

### 정정 ② — HIGH_VOL 분기에서 `_confirmed_regime` 미갱신 → 알림 폭격

**버그**: `_set_high_vol()`은 `_confirmed_regime`을 갱신하지 않는다. HIGH_VOL이
N회 연속되면 매 호출마다 `_make_state()`가 직전 비-HIGH_VOL 레짐을 `prev`로 읽어
`regime_changed=True`를 반복 반환한다.
→ HIGH_VOL 100봉 지속 시 Telegram 알림 100회 + `regime_history` 100회 중복 로깅.

**수정**: 진입 스냅샷 기준으로 1회만 전환으로 인식하고, `_confirmed_regime`을 갱신:
```python
state.prev_regime = prev_regime_snapshot
state.regime_changed = (prev_regime_snapshot != Regime.HIGH_VOL)
self._confirmed_regime = Regime.HIGH_VOL
```

### 검증

`tests/test_regime_detector.py` 시나리오 3 / 9-a / 9-b 가 정정 동작을 직접 검증:
- 시나리오 3: 안정성 룰 통과 → `regime_changed=True`, `prev_regime=TREND_UP` (정정 ①)
- 시나리오 9-a: HIGH_VOL 2회 연속 → 2번째 `regime_changed=False` (정정 ②)
- 시나리오 9-b: TREND_UP→HIGH_VOL→TREND_UP 사이클, 각 전환마다 `regime_changed=True` (①②)

---

## 세션 7 — SystemHealthMonitor (`ops/system_health_monitor.py`)

정정 4건 (사용자 승인). §8-6-2 코드를 1:1 이식하면서 발견.

### 정정 ① — `get_server_time()` 반환 타입 가정 오류 (운영 핵심 버그)

**버그**: `_check_time_sync()` 는 `server_time_ms = self.binance.get_server_time()`
후 `server_time_ms / 1000.0` 을 수행한다. 명세서는 반환값을 raw int 로 가정했으나
**python-binance 의 `get_server_time()` 은 `{"serverTime": <ms>}` dict 를 반환**한다.
그대로 두면 `dict / 1000.0` 에서 `TypeError` → `except` 로 떨어져 시간 동기 체크가
**영구 실패**(`None` 반환)한다. 세션 5 RegimeDetector 의 '명세서 가정 vs 실제
라이브러리 반환 형식' 버그와 동일 카테고리.

**수정**: `isinstance(raw, dict)` 면 `raw["serverTime"]` 추출, 아니면 그대로 사용.

### 정정 ② — REST 이력 deque maxlen 100 → 300

**근거**: `_rest_stats()` 는 최근 60초 윈도우를 집계한다. 초당 다수 REST 호출 시
maxlen=100 은 60초 윈도우를 다 담지 못해 통계가 과소 표본이 된다. 기본값 300 으로 상향.

### 정정 ③ — `_rest_stats()` 60초 윈도우 하드코딩 → 파라미터화

**수정**: `_rest_stats(window_seconds: float = 60.0)` 로 분리. 기본값 60 이라 기존
동작은 동일, 향후 튜닝/테스트 유연성 확보.

### 정정 ④ — `HealthReport.checked_at` naive datetime → UTC tz-aware

**버그**: 명세서는 `field(default_factory=datetime.now)` (naive). `checked_at` 은
HealthReport·DB·Telegram 으로 **외부 노출되는 필드**라 CLAUDE.md TIER 2 시간 규칙
(모든 datetime UTC tz-aware) 위반.

**수정**: `default_factory=lambda: datetime.now(timezone.utc)`.

> **메모 (사용자 지시)**: `PairWhitelist._last_refresh` 는 세션 2.5 에서 naive 유지로
> 결정됨 (내부 시간차 계산 전용, 외부 미노출). 현재는 미수정. v3.1.2 최종 정리 시
> PairWhitelist 도 UTC tz-aware 로 통일 권장.

### 검증

`tests/test_system_health_monitor.py` 6개 시나리오 전부 통과 (정정 ① 은 fixture 의
`get_server_time` dict 반환으로 직접 검증). 전체 회귀 85 passed (79 + 6).

---

## 세션 10 — WeeklyGPTAnalyst (`analytics/weekly_report.py`)

정정 1건 (사용자 승인). §8-4-2 코드를 이식하면서 발견. 세션 5·7 의 'naive
datetime' 카테고리와 동일한 운영 핵심 버그.

### 정정 ① — naive `datetime.now()` 로 조회 윈도우/생성시각 산출 → UTC tz-aware

**버그**: SPEC §8-4-2 는 `datetime.now()` (naive) 로 (a) `generated_at`,
(b) `_get_regime_stats` / `_get_cost_metrics` 의 `since = datetime.now() - timedelta(days=days)`
를 산출한다. 그러나 `trades.timestamp` / `gpt_call_log.timestamp` 는 UTC ISO
문자열로 저장된다. 운영 서버가 KST 라면 `since` 가 실제보다 9시간 미래로
계산되어 **최근 9시간 거래가 주간 집계에서 누락**된다 (조회 윈도우 경계가
밀림). CLAUDE.md TIER 2 시간 규칙(모든 datetime UTC tz-aware) 위반이자,
세션 5 RegimeDetector·세션 7 SystemHealthMonitor 정정과 동일 카테고리.

**수정**: 모든 `datetime.now()` → `datetime.now(timezone.utc)`.
- `WeeklyReport.generated_at` (외부 노출 필드)
- `_get_cost_metrics` 의 `since` 윈도우
- `_save_report_file` 의 파일명 날짜
- (연계) `analytics/expectancy.py` 의 `_fetch_closed_trades` `since` 윈도우도
  동일 원칙으로 UTC tz-aware 적용 (ExpectancyAnalyzer 는 세션 10 신규 작성).

인라인 주석은 `# v3.1.2 정정` 으로 표시.

### 참고 — 정정 아닌 의도적 보강 (사용자 지시, 세션 10)

아래는 버그 수정이 아니라 사용자 지시에 의한 설계 변경이므로 코드 인라인
주석(`보강 A~E`)으로만 표시하고 본 메모에는 목록만 남긴다:
- 보강 A: `run()` / `_call_gpt()` async 화 (asyncio.to_thread 래핑)
- 보강 B: `ExpectancyAnalyzer` 생성자 주입 (SPEC 은 `run()` 내부 생성)
- 보강 C: `_save_report_db` 가 `gpt_call_log` 도 기록 (SPEC 은 `weekly_reports` 만)
- 보강 D: GPT 실패 시 `gpt_insights = "(GPT 호출 실패, 통계만 포함)"`
- 보강 E: 보고서 파일명 `weekly_YYYY-MM-DD.json` (SPEC 은 초단위 타임스탬프)

### 검증

`tests/test_expectancy.py` 3개 + `tests/test_weekly_report.py` 4개 전부 통과.
전체 회귀 106 passed (99 + 7).

---

## 세션 11 — BacktestEngine (`backtesting/`)

정정 2건 (사용자 승인). §10-2 / §10-5 코드 골격을 이식하면서 발견. 두 건
모두 운영 버그가 아니라 **골격 구조 조정** 카테고리이며, 세션 11 설계 지시와의
정합을 위한 것이다. 인라인 주석은 `# 세션 11 정정` 으로 표시.

### 정정 ① — `BacktestConfig.start_date` / `end_date` 에 기본값 부여

**원문**: SPEC §10-2 `BacktestConfig` 는 `start_date: datetime` /
`end_date: datetime` 을 default 없는 required 필드로 정의한다.

**문제**: 세션 11 지시("@dataclass BacktestConfig 모든 필드 명세서 동일" +
테스트 fixture 는 "BacktestConfig 기본값 사용")를 동시에 만족하려면
`BacktestConfig()` 가 인자 없이 생성 가능해야 한다.

**수정**: `start_date` / `end_date` 에 §10-3 예시 기간(2022-01-01 ~
2026-04-30, UTC tz-aware)을 `field(default_factory=...)` 로 부여. `run()` 은
`candles_by_pair` 만 사용하므로 무해하며, 두 필드는 `walk_forward` 윈도우
생성에만 쓰인다.

### 정정 ② — `_get_candles_until` 시그니처 + `walk_forward` 모듈 분리

**원문**: SPEC §10-5 골격은 `_get_candles_until(candles, tf, ts)` 시그니처와
`walk_forward` / `_generate_windows` 를 모두 `BacktestEngine` 메서드로 둔다.

**수정**:
- `_get_candles_until(symbol, tf, ts)` 로 조정 — 세션 11 설계 지시 시그니처.
  `candles_by_pair` 를 `run()` 진입 시 인스턴스 상태(`self._candles_by_pair`)로
  저장하고 `symbol` 키로 조회한다.
- `walk_forward` / `_generate_windows` / `_optimize_in_sample` 을
  `backtesting/walk_forward.py` 모듈 함수로 분리 — 순환 import 회피 + 단일
  책임 원칙. `BacktestEngine` 은 단일 구간 백테스트만 책임진다.

### 참고 — 정정 아닌 설계 결정 (룩어헤드 차단, 사용자 승인 plan)

§10-5 골격은 `_evaluate_signal` 본문을 비워 둠 → 보수적 추세추종으로 구현
(TREND_UP→LONG, TREND_DOWN→SHORT, 그 외 무거래). 룩어헤드 5단계 차단,
한 봉 내 TP·SL 동시 충족 시 SL 우선(보수적), 펀딩비 보유봉별 반영은 plan
단계에서 사용자 승인. v3.2 에서 실전 OIScanner+QualityGate 와의 일치화 검토
예정 (세션 11 진행에는 영향 없음).

### 검증

`tests/test_backtest_engine.py` 5개(시나리오 1~3 + 룩어헤드 4-a/4-b) 전부 통과.
전체 회귀 111 passed (106 + 5).

---

## 운영 중 발견 — main_7590.py `_iter()` health 체크 교착 (사용자 승인)

정정 1건. 세션 12 이후 운영(테스트넷/실거래) 중 "⚠️ 시스템 이상: WS kline 미수신"
Telegram 알림이 **무한 반복**되는 증상으로 발견.

### 정정 ① — kline 신선도 갱신이 health 게이트 *뒤* 에 있어 교착

**버그**: WebSocket 은 스텁이므로 `_iter()` 는 REST 폴링(캔들 fetch) 성공을 kline
신선도로 인정한다(세션 12 버그 #3 수정). 그러나 신선도 갱신
(`health.record_ws_kline_received()`)이 **Layer 1+2 데이터 fetch 안**에 있고,
그 앞 **Layer 0 health 체크**가 실패하면 `_iter()` 는 `return` 으로 조기 종료한다.
→ 신선도 갱신 코드에 영영 도달하지 못함 → 다음 루프 health 체크도 실패 →
**무한 교착**. `start()` 의 7-b 프라이밍은 1회차만 살려주므로, 캔들 fetch 가
한 번이라도 느리거나 실패해 60초(`ws_kline_max_age_s`) 갭이 생기면 그 순간부터
영구 "시스템 이상" 상태에 빠진다 (루프 주기 30초 < 임계 60초라 정상 시엔 미발생).

**수정**: `_iter()` 시작부, Layer 0 health 체크 *이전* 에 캔들 1개짜리 가벼운
프라이밍 폴링을 추가해 신선도를 먼저 갱신한다 (`start()` 7-b 와 동일 패턴).
프라이밍 실패는 삼키고 루프는 계속 진행한다. 인라인 주석은 `_iter()` 본문에 명시.

### 검증

`tests/test_main_integration.py` 3개 추가:
- health 체크 실패 상태여도 `_iter()` 가 그 전에 `record_ws_kline_received()` 호출 (교착 방지)
- 프라이밍 캔들이 빈 결과면 신선도 미갱신
- 프라이밍 캔들 fetch 예외 시에도 `_iter()` 중단 없음

전체 회귀 234 passed (231 + 3).
