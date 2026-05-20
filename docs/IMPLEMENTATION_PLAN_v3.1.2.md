# 실거래 중단급 문제 수정 — 구현 계획 (파일별)

## Context
직전 정밀 감사 결과 **executor의 주문 실행/체결/보호주문 계층**이 실거래 안전 기준 미달로 판정됨(실거래 보류). 게이트 계층(RiskManager·CostGuard·PairWhitelist·CapitalManager)은 견고하므로 **완화하지 않는다**. 본 계획은 사용자가 제시한 필수 구현 [1]~[5] + 추가 6건을, 안전장치를 더하는 방향으로만 반영한다. 코드는 승인 후에만 수정한다.

핵심 불변식(절대 완화 금지):
- 보호 종목 차단 / RiskManager·CostGuard·CapitalManager fail-closed 안전장치 유지
- dry_run=True → 어떤 Binance 주문/조회 API도 호출하지 않음
- live → 주문 접수 ≠ 진입 성공. **FILLED 확인 후에만** trades OPEN 기록 + ExitPlan 추적

---

## 결정 사항 (이대로 진행, 다르면 알려주세요)
1. **진입은 GTX(Post-Only) 유지** + FILLED 폴링. GTX는 메이커 전용이라 타임아웃 내 미체결이 잦음 → 그 경우 cancel + `success=False`(진입 스킵). **결과적으로 신규 진입이 지금보다 자주 스킵됨** = 보수적. (명세 §8-8-2 "Post-Only 우선, 폴백 없음"과 일치)
2. **거래소 보호 주문**: 진입 체결 직후 reduceOnly `STOP_MARKET`(필수) + reduceOnly `TAKE_PROFIT_MARKET`(포함). 실패 시 즉시 시장가 청산 + Critical 결과 반환.
3. **DB 스키마**: pending 테이블 대신 **FILLED 후에만 INSERT**(유령 행 원천 차단) + 보호주문 추적용 컬럼 추가(`entry_order_id/sl_order_id/tp_order_id/trade_status`). 신규 마이그레이션 `v3_1_1_to_v3_1_2.sql` + `schema_migrations`에 `v3.1.2` 등록.
4. **정밀도**: `futures_exchange_info` 필터(LOT_SIZE/PRICE_FILTER/MIN_NOTIONAL) 캐시. **조회 실패 시 live 주문 차단**(success=False). dry_run은 오프라인 유지를 위해 단순 계산 사용(주문 미발생이라 무해).
5. **기본 보호 종목**: `BTCUSDT, ETHUSDT, HOLOUSDT, CFXUSDT, LYNUSDT` (CFX 재추가 — 사용자 지시).
6. **fill 폴링 파라미터**: 기본 타임아웃 15s / 폴링 1s (settings에 노출).
7. **거래소 STOP 동기화**: ExitPlan이 BE/트레일로 `current_sl` 이동 시 거래소 STOP cancel+replace. 거래소 STOP이 폴링 사이에 체결되면 `_manage_open_positions`가 포지션 소멸을 감지해 DB 보정 청산.

---

## 1순위 — 실거래 중단급 (Critical/High)

### A. `trading/executor.py` (대수술)
**변경 전 위험**: 진입 LIMIT GTX 1건만 전송 후 체결 확인 없이 success=True(`:147-209`), 거래소 STOP 부재, 수량 고정 3자리, 청산가 미확보 시 가짜 본전.

**변경 내용**:
1. `__init__`: 필터 캐시 상태(`_symbol_filters: dict`, TTL), fill 폴링 파라미터(`fill_timeout_s`, `fill_poll_interval_s`) 주입(기본값은 settings에서).
2. `_gen_client_order_id(symbol)` 신설 — 멱등용 `newClientOrderId`(예: `bot-{symbol}-{uuid4hex[:16]}`, ≤36자).
3. `_get_symbol_filters(symbol)` 신설 — `futures_exchange_info` 캐시 조회 → `(qty_step, min_qty, price_tick, min_notional)`. 실패 시 `None` 반환(호출부가 주문 차단). dry_run은 호출 안 함.
4. `_normalize(symbol, qty, price)` 신설 — stepSize 내림, tickSize 정렬, MIN_NOTIONAL 검증. 위반 시 차단 사유 반환.
5. `_place_post_only` → **`_place_entry_and_confirm`** 으로 교체:
   - 레버리지 설정 → GTX LIMIT 전송(`newClientOrderId`) → `orderId` 확보.
   - `futures_get_order` 폴링: `FILLED`→성공(executedQty, avgPrice/cummulativeQuoteQty 기반 실제 qty·entry 확정). `NEW/PARTIALLY_FILLED` 타임아웃→`futures_cancel_order` 후 실패. `CANCELED/EXPIRED/REJECTED`→실패. (부분체결 잔량은 cancel; 체결분이 있으면 그 수량으로 진행하되, 단순화를 위해 1차 구현은 "부분체결 시 잔량 cancel + 체결분으로 STOP/TP/기록" 처리)
6. **`_place_protective_orders(symbol, action, filled_qty, sl, tp)`** 신설 — reduceOnly STOP_MARKET(stopPrice=sl) + reduceOnly TAKE_PROFIT_MARKET(stopPrice=tp), `closePosition` 또는 quantity 기반. 각 orderId 반환.
7. `enter_trade` 흐름 재구성(live):
   - 입력검증 → 필터 정규화(실패 차단) → `_place_entry_and_confirm`(미체결 차단) → `_place_protective_orders`(실패 시 **즉시 reduceOnly 시장가 청산** + `success=False, critical=True` 반환) → **그 후** `_insert_trade`(실제 qty/entry/order_id들/status='OPEN').
   - `_insert_trade` 실패 시: 보호주문 cancel + 포지션 reduceOnly 시장가 청산(고아 방지) 후 실패 반환.
   - 반환 dict에 `filled_qty`, `entry_price_actual`, `sl_order_id`, `tp_order_id`, `critical`(bool) 추가.
8. dry_run: 어떤 API도 호출하지 않음. 체결가=entry_price, qty=정규화 생략한 계산값으로 가정, 보호주문은 로그만, `_insert_trade` 수행(기존 페이퍼 동작 유지).
9. `close_position`/`_update_trade_exit` 정밀화([4]):
   - 전량 청산: 잔존 보호주문(sl/tp) **먼저 cancel** → reduceOnly MARKET 청산 → 실제 체결가 확보(avgPrice 없으면 `futures_account_trades`로 보정) → `_finalize_trade_exit`에서 `exit_price, pnl_usd, pnl_pct, fees_usd(커미션 합), pnl_usd_net, duration_seconds, exit_reason, trade_status='CLOSED'` 기록.
   - 부분 청산: 기존 quantity 감소 유지(+ 체결가 보정은 분할 손익 계산용으로만, 행은 열림 유지).
10. **`reconcile_closed_positions(tracked_symbols)`** 신설 — live에서 추적 중인데 거래소 포지션이 사라진 심볼(=STOP/TP가 폴링 사이 체결) 감지 → `futures_account_trades`로 실제 청산가/수수료 산정 → DB 마감 + 잔존 주문 cancel. 결과 리스트 반환.

핵심 변경점마다 `# Critical 수정 (감사 C1/C2/H1/H2/M1/M3)` 주석.

### B. `trading/exit_plan.py`
**변경 전 위험**: SL/TP가 30초 폴링 소프트웨어 단독. 거래소 STOP 없음.

**변경 내용**:
1. `start_tracking`: `sl_order_id`/`tp_order_id`를 ExitPlan에 저장(executor 결과에서 전달받음).
2. BE 이동/트레일로 `current_sl` 변경 시 `await executor.replace_stop_order(symbol, plan, new_sl)` 호출(거래소 STOP cancel+replace). 실패 시 경고 + 다음 루프 재시도(소프트웨어 SL은 여전히 동작하므로 무방비 아님).
3. `_full_close` 성공 시 잔존 보호주문 cancel은 executor.close_position이 담당(중복 정리 방지).
4. 역할 주석: "거래소 STOP가 하드 플로어, ExitPlan은 분할 TP/트레일/시간스톱 + STOP 갱신 관리".

### C. `main_7590.py`
**변경 내용**:
1. `_handle_signal`(`:745-757`): `result.get("success")`일 때만 `start_tracking`(이미 그러함) — 단 `start_tracking`에 `sl_order_id/tp_order_id` 전달. `result.get("critical")`면 Critical 로그 + Telegram 경고(보호주문 실패→강제청산 알림).
2. `_manage_open_positions`(`:762-777`): live에서 `reconcile_closed_positions` 먼저 호출(거래소 STOP 체결분 DB 보정 + 청산 알림) → 그 후 ExitPlan `update_all`. ATR 맵도 가능하면 전달(현재 None).
3. 진입 성공 Telegram에 "🛡️ 손절 주문 거래소 등록 완료(sl_order_id)" 명시.
4. `_close_all_positions_safe`: 잔존 보호주문 cancel 포함되도록 close_position 경유(이미 그러함, 확인).

---

## 2순위 — 추가 수정

### D. `config/settings.py`
1. `protected_symbols` 기본값 → `BTCUSDT,ETHUSDT,HOLOUSDT,CFXUSDT,LYNUSDT`.
2. **빈 PROTECTED_SYMBOLS 폴백**(추가1): env가 빈 문자열/공백/콤마만이면 파싱 결과가 `[]` → 하드코딩 기본 리스트로 폴백 + `logger.warning`. (현재 `:258-264`는 `[]` 그대로 통과 — H3 위험)
3. `TradeExecutorConfig`(신설 dataclass): `fill_timeout_s=15.0`, `fill_poll_interval_s=1.0`, `exchange_info_ttl_s=3600`, `place_take_profit=True`. export `TRADE_EXECUTOR_CONFIG`.

### E. `data/collector.py` (추가3·4)
1. `_build_client`(`:118-131`): use_testnet=True인데 testnet 키 없을 때 **live 키 폴백 제거** → live 분기와 동일하게 `RuntimeError`.

### F. `README.md` (추가4)
1. testnet 안내(`:84-87`)를 `BINANCE_TESTNET_API_KEY/SECRET` + `USE_TESTNET=true`로 정정. `BINANCE_API_KEY/SECRET`는 live 전용으로 명시. HANDOFF.md:43과 일치.

### G. `trading/risk_manager.py` (추가5)
1. `_check_daily_loss`(`:166-168`): `daily_start<=0` → **passed=False(차단)**.
2. `_check_total_drawdown`(`:189-191`): `initial<=0` → **passed=False(차단)**.
3. `_check_monthly_drawdown`(`:281-283`): `month_start<=0` → **passed=False(차단)**.
주석: `# 감사 M2 — 비정상 베이스라인 fail-closed`. (None/DB오류 fail-closed는 이미 정상 — 유지)

### H. `analytics/macro_event_analyzer.py` (추가6)
1. `__init__`에 `block_on_missing_calendar: bool = False` 추가 + `self.calendar_ok: bool` 상태.
2. `load_calendar`: 파일 없음/형식 오류 시 `calendar_ok=False`.
3. `is_blocked`: `block_on_missing_calendar and not calendar_ok` → `True`(차단) + 경고.
4. `main_7590.py`에서 **live(not dry_run)일 때만** `block_on_missing_calendar=True`로 생성(페이퍼/테스트는 경고만, 차단 안 함).

### I. DB 마이그레이션 (결정3)
1. 신규 `db/migrations/v3_1_1_to_v3_1_2.sql`:
   - `ALTER TABLE trades ADD COLUMN entry_order_id TEXT;`
   - `ADD COLUMN sl_order_id TEXT;` / `ADD COLUMN tp_order_id TEXT;`
   - `ADD COLUMN trade_status TEXT DEFAULT 'OPEN';`
   - `INSERT INTO schema_migrations VALUES ('v3.1.2', datetime('now'));`
2. `db/init_db.py`: `MIGRATION_V3_1_2` 상수 + `'v3.1.2' not in applied`일 때 적용 블록 추가(기존 패턴 동일, 멱등).

---

## 테스트 (필수, [9] 반영)

### `tests/test_executor.py` (대폭 수정 — 현재 위험 동작을 정상으로 보는 구조 교체)
- 제거/교체: `test_live_entry_places_post_only_and_records`(접수=성공 가정), `test_quantity_computation`(고정 3자리), `test_order_failure_no_fallback...`(체결 개념 없음).
- 신규:
  - GTX 전송 후 `futures_get_order`=FILLED → 성공 + executedQty/avgPrice로 qty·entry 확정 + STOP/TP 주문 생성 검증 + trades 1행(status='OPEN', sl_order_id 기록).
  - NEW 지속 → 타임아웃 → `futures_cancel_order` 호출 + success=False + **trades 행 없음**.
  - REJECTED/EXPIRED/CANCELED → success=False + 행 없음.
  - 보호주문(STOP) 생성 실패 → 즉시 reduceOnly 시장가 청산 호출 + success=False + critical=True + 행 없음.
  - `_insert_trade` 실패 → 보호주문 cancel + 포지션 청산(고아 방지).
  - exchange_info 필터로 stepSize/minNotional 정규화; 필터 조회 실패 → live 주문 차단.
  - dry_run → 어떤 binance API도 호출 안 함(get_order/create_order/exchange_info 모두 assert_not_called) + DB INSERT 수행.
  - close 전량 → 잔존 STOP/TP cancel + 시장가 청산 + avgPrice=0일 때 `futures_account_trades`로 체결가 보정 + pnl_usd/pnl_pct/fees_usd/pnl_usd_net/duration_seconds/exit_reason 기록.
  - `reconcile_closed_positions` → 추적 중 포지션 소멸 감지 → DB 마감.
- mock: `futures_create_order`(orderId 반환), `futures_get_order`(상태 시퀀스), `futures_cancel_order`, `futures_exchange_info`, `futures_account_trades`, `futures_position_information`.

### `tests/test_main_integration.py`
- `test_sol_signal_passes_all_gates_and_enters`: dry_run 경로라 유지되되, start_tracking에 sl/tp order_id 인자 추가 반영. 진입 알림에 손절 등록 문구 확인은 live 전용이라 dry_run은 생략.
- 신규: live 모드에서 보호주문 실패→강제청산 시 Critical Telegram 발송(executor mock으로).
- 신규: `reconcile_closed_positions`가 `_manage_open_positions`에서 호출되는지(거래소 STOP 체결 시 청산 알림).

### `tests/test_risk_manager.py`
- 신규: `daily_start=0`/`initial=0`/`month_start<=0` → check 차단(passed=False).
- 신규([4] 연동): 실제 pnl_usd_net 음수 거래로 연패 계산이 작동(close_position 정밀 기록 후 _get_loss_streak)하는지.

### `tests/test_settings.py`
- `protected_symbols` 기본값 `[BTC,ETH,HOLO,CFX,LYN]`로 수정.
- 빈 `PROTECTED_SYMBOLS` → 기본값 폴백 검증(신규).

### `tests/test_collector.py`
- testnet 키 없을 때 live 폴백 대신 `RuntimeError` 기대로 수정(기존 `test_testnet_falls_back_to_live_keys` 의미 반전).

### `tests/test_macro_event_analyzer.py`
- `block_on_missing_calendar=True` + 파일 없음 → `is_blocked()` True 검증(신규).

### `tests/test_db_init.py`
- `v3.1.2` 마이그레이션 적용 + 신규 컬럼 존재 검증(신규).

---

## 변경 후 안전장치 요약
- 진입 접수≠성공: **FILLED 확인 후에만** 기록·추적 → 유령 행/미체결 추적 제거(C2/H1).
- 거래소 reduceOnly STOP_MARKET 상시 존재 → 프로세스/네트워크 장애에도 하드 손절(C1).
- 보호주문 실패 시 즉시 강제청산 + Critical 알림(요구 7).
- newClientOrderId 멱등 + 타임아웃 cancel → 중복/유실 주문 방지(H2).
- exchangeInfo 정규화, 실패 시 주문 차단(M3, [5]).
- 청산 시 실제 체결가/수수료로 PnL 정확 기록 → 리스크 게이트 입력 신뢰(M1, [4]).
- 빈 PROTECTED_SYMBOLS 폴백(H3) / testnet→live 키 폴백 제거(H4) / 비정상 베이스라인 fail-closed(M2) / live 거시파일 부재 차단(M5).
- 게이트 계층(Risk/Cost/PairWL/Capital) 완화 0건.

## 검증 명령 (수정 후)
```
python -m compileall .
pytest tests/test_executor.py -v
pytest tests/test_main_integration.py -v
pytest tests/test_risk_manager.py -v
pytest tests/ -v
python main_7590.py --dry-run --duration 100
```
> 주의: 이 감사/개발 환경은 Binance에 SSL 미연결 → `--dry-run` 실연결과 실주문 STOP 등록 확인은 **운영 PC/Testnet**에서만 가능. 본 환경에서는 단위테스트(mock)로만 검증.

## 수정 대상 파일 목록
1순위: `trading/executor.py`, `trading/exit_plan.py`, `main_7590.py`
2순위: `config/settings.py`, `data/collector.py`, `README.md`, `trading/risk_manager.py`, `analytics/macro_event_analyzer.py`
마이그레이션: `db/migrations/v3_1_1_to_v3_1_2.sql`, `db/init_db.py`
테스트: `tests/test_executor.py`, `tests/test_main_integration.py`, `tests/test_risk_manager.py`, `tests/test_settings.py`, `tests/test_collector.py`, `tests/test_macro_event_analyzer.py`, `tests/test_db_init.py`
문서(선택): `docs/SPEC_v3.1.md` §8-8-2 보호주문 명세 보강, `docs/CORRECTIONS_v3.1.2.md` 기록

## 아직 남을 위험 (이번 범위 밖)
- 실 WebSocket 미구현(REST 30초 폴링) → 거래소 STOP가 폴링 갭을 메우지만 시세 갱신 지연은 잔존.
- 부분체결 정교화는 1차 구현에서 단순화(잔량 cancel) — 추후 정밀화 여지.
- 실거래 가능 여부는 **운영 PC에서 Testnet 실주문으로 STOP 등록·체결·reconcile 확인 후** 최종 판단.

## 작업 진행 방식
세션 1개=핵심 모듈 단위로 진행 권장(executor → exit_plan/main → settings/collector/risk/macro → migration → tests). 승인 후 1순위(executor + exit_plan + main + 관련 테스트)부터 착수.
