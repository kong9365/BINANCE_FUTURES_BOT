"""
tests/test_executor.py
=====================================================================
TradeExecutor 단위 테스트 (v3.1.2 — 체결 확인 + algoOrder 보호 주문 기준).

근거:
  - docs/SPEC_v3.1.md §8-8 (Layer 5), §8-8-2 (Post-Only Limit)
  - 감사 후속: C1(거래소 STOP)/C2(FILLED 확인)/H1(유령 행 방지)/H2(멱등)/
    M1(정밀 청산가)/M3(exchangeInfo 정규화)
  - 2025-12-09 Binance Algo Service 전환 — 보호 주문은 POST /fapi/v1/algoOrder.
    진입 LIMIT 만 기존 futures_create_order 그대로 유지(요구 [1]/[2]).

구성:
  - DB 는 tmp_path 임시 SQLite (db.init_db.init_db 로 v3.1.2 까지 적용)
  - binance_client 는 MagicMock 주입 (실제 네트워크 호출 없음)
  - pytest asyncio_mode=auto → async def test_* 직접 사용

검증 요지:
  - live 진입은 FILLED 확인 + algo STOP/TP 생성 후에만 trades 기록(status='OPEN').
  - 미체결(타임아웃)/거부 → cancel + 진입 스킵 + 행 없음.
  - STOP 생성 실패 → 즉시 강제청산 + critical + 행 없음.
  - DB 기록 실패 → 보호주문 cancel + 강제청산(고아 방지).
  - exchangeInfo 조회 실패 → 주문 차단.
  - dry_run → 어떤 Binance API 도 호출 안 함 + DB INSERT.
  - 청산 → 잔존 algo STOP/TP cancel + 실제 체결가/수수료로 정밀 PnL 기록.
  - reconcile_closed_positions / replace_stop_order (둘 다 algo 경로).
  - algoOrder payload 가드: quantity / reduceOnly 절대 미전송, triggerPrice 사용.
=====================================================================
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

from db.init_db import init_db
from trading.executor import TradeExecutor

import pytest  # noqa: F401  (asyncio_mode=auto 가 async test 를 수집)


# ── fixtures / helpers ──────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path) -> str:
    p = tmp_path / "bot.db"
    init_db(p)
    return str(p)


@pytest.fixture
def mock_binance() -> MagicMock:
    return MagicMock()


def _decision(**overrides) -> dict:
    d = {
        "symbol": "SOLUSDT",
        "action": "LONG",
        "entry_price": 100.0,
        "tp": 102.0,
        "sl": 99.0,
        "setup_tag": "oi_surge_long",
        "size_usdt": 50.0,
        "leverage": 3,
        "regime": "TREND_UP",
        "regime_confidence": 0.8,
        "cost_guard_ev": 1.5,
        "pair_tier": 2,
        "wallet_balance_at_entry": 1000.0,
        "available_at_entry": 970.0,
        "locked_margin_at_entry": 30.0,
    }
    d.update(overrides)
    return d


def _exinfo(symbol="SOLUSDT") -> dict:
    return {"symbols": [{"symbol": symbol, "filters": [
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"},
    ]}]}


def _fetch_trades(db_path: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM trades").fetchall()
    finally:
        conn.close()


def _seed_open_trade(ex: TradeExecutor, **overrides) -> int:
    """live 청산/정합 테스트용 — 미청산 trades 행을 직접 시드한다."""
    d = _decision(**overrides)
    return ex._insert_trade(
        d, quantity=d["size_usdt"] / d["entry_price"], leverage=d["leverage"],
        entry_price=d["entry_price"], entry_order_id="111",
        sl_order_id="222", tp_order_id="333",
    )


# ── 1~2. live 진입 (FILLED 확인 + 보호주문) ─────────────────────────

async def test_live_entry_fills_places_protective_and_records(db_path, mock_binance):
    """GTX 체결(FILLED) → algo STOP/TP 생성 → trades INSERT(status='OPEN')."""
    mock_binance.futures_exchange_info.return_value = _exinfo()
    # 진입 LIMIT 만 futures_create_order (기존 유지)
    mock_binance.futures_create_order.return_value = {"orderId": 111}
    # 보호 주문은 algo 엔드포인트 (2025-12-09 전환)
    mock_binance.futures_create_algo_order.side_effect = [
        {"algoId": 222, "clientAlgoId": "bot-sl-SOLUSDT-aaa"},
        {"algoId": 333, "clientAlgoId": "bot-tp-SOLUSDT-bbb"},
    ]
    mock_binance.futures_get_order.return_value = {
        "status": "FILLED", "executedQty": "0.5", "avgPrice": "100.0",
    }

    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    result = await ex.enter_trade(_decision())

    assert result["success"] is True
    assert result["sl_order_id"] == "222"
    assert result["tp_order_id"] == "333"
    assert result["critical"] is False

    mock_binance.futures_change_leverage.assert_called_once_with(
        symbol="SOLUSDT", leverage=3
    )
    # 진입 LIMIT — futures_create_order 1회 (기존 메서드)
    assert mock_binance.futures_create_order.call_count == 1
    entry_kwargs = mock_binance.futures_create_order.call_args.kwargs
    assert entry_kwargs["side"] == "BUY"
    assert entry_kwargs["type"] == "LIMIT"
    assert entry_kwargs["timeInForce"] == "GTX"
    assert entry_kwargs["quantity"] == 0.5
    assert entry_kwargs["price"] == 100.0
    assert entry_kwargs["newClientOrderId"].startswith("bot-")
    # STOP/TP — algo 엔드포인트 2회
    assert mock_binance.futures_create_algo_order.call_count == 2
    algo_calls = mock_binance.futures_create_algo_order.call_args_list
    sl_kwargs = algo_calls[0].kwargs
    assert sl_kwargs["algoType"] == "CONDITIONAL"
    assert sl_kwargs["type"] == "STOP_MARKET"
    assert sl_kwargs["side"] == "SELL"
    assert sl_kwargs["triggerPrice"] == 99.0
    assert sl_kwargs["closePosition"] == "true"
    assert sl_kwargs["workingType"] == "MARK_PRICE"
    assert sl_kwargs["priceProtect"] == "true"           # 소문자 (공식 명세)
    assert sl_kwargs["clientAlgoId"].startswith("bot-sl-")
    # 절대 가드 — closePosition=true 경로에 quantity/reduceOnly 금지
    assert "quantity" not in sl_kwargs
    assert "reduceOnly" not in sl_kwargs
    tp_kwargs = algo_calls[1].kwargs
    assert tp_kwargs["type"] == "TAKE_PROFIT_MARKET"
    assert tp_kwargs["triggerPrice"] == 102.0
    assert tp_kwargs["clientAlgoId"].startswith("bot-tp-")
    assert "quantity" not in tp_kwargs
    assert "reduceOnly" not in tp_kwargs

    rows = _fetch_trades(db_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["trade_status"] == "OPEN"
    assert row["sl_order_id"] == "222"
    assert row["tp_order_id"] == "333"
    assert row["entry_order_id"] == entry_kwargs["newClientOrderId"]
    assert row["quantity"] == 0.5
    assert row["entry_price"] == 100.0
    assert row["wallet_balance_at_entry"] == 1000.0


# ── 3. 미체결 타임아웃 → cancel + 진입 스킵 ─────────────────────────

async def test_entry_timeout_cancels_and_no_record(db_path, mock_binance):
    """NEW 지속 → 타임아웃 → futures_cancel_order + success=False + 행 없음."""
    mock_binance.futures_exchange_info.return_value = _exinfo()
    mock_binance.futures_create_order.return_value = {"orderId": 111}
    mock_binance.futures_get_order.return_value = {"status": "NEW", "executedQty": "0"}

    ex = TradeExecutor(mock_binance, db_path, dry_run=False,
                       fill_timeout_s=0.05, fill_poll_interval_s=0.01)
    result = await ex.enter_trade(_decision())

    assert result["success"] is False
    assert "timeout" in result["reason"]
    mock_binance.futures_cancel_order.assert_called_once()
    assert _fetch_trades(db_path) == []


async def test_entry_rejected_no_record(db_path, mock_binance):
    """REJECTED → success=False + 행 없음 (cancel 불필요)."""
    mock_binance.futures_exchange_info.return_value = _exinfo()
    mock_binance.futures_create_order.return_value = {"orderId": 111}
    mock_binance.futures_get_order.return_value = {"status": "REJECTED"}

    ex = TradeExecutor(mock_binance, db_path, dry_run=False,
                       fill_timeout_s=0.05, fill_poll_interval_s=0.01)
    result = await ex.enter_trade(_decision())

    assert result["success"] is False
    assert "REJECTED" in result["reason"]
    mock_binance.futures_cancel_order.assert_not_called()
    assert _fetch_trades(db_path) == []


# ── A-5. 조회 실패/타임아웃 시 고아 포지션 방지 ─────────────────────

async def test_await_fill_orphan_force_close(db_path, mock_binance):
    """조회 계속 실패 + 실제 포지션 존재 → _force_close + critical=True + 행 없음."""
    mock_binance.futures_exchange_info.return_value = _exinfo()
    mock_binance.futures_create_order.return_value = {"orderId": 111}
    # 주문 상태 조회 계속 실패 (네트워크/레이트리밋)
    mock_binance.futures_get_order.side_effect = RuntimeError("net down")
    # cancel 도 실패 (이미 체결됐거나 네트워크)
    mock_binance.futures_cancel_order.side_effect = RuntimeError("cancel fail")
    # 실제로는 포지션이 생성돼 있음
    mock_binance.futures_position_information.return_value = [
        {"symbol": "SOLUSDT", "positionAmt": "0.5", "entryPrice": "100.0",
         "unRealizedProfit": "0.0"},
    ]

    ex = TradeExecutor(mock_binance, db_path, dry_run=False,
                       fill_timeout_s=0.03, fill_poll_interval_s=0.01)
    result = await ex.enter_trade(_decision())

    assert result["success"] is False
    assert result["critical"] is True
    assert "orphan_position_force_closed" in result["reason"]
    # 강제청산 MARKET reduceOnly 호출됨 (entry LIMIT 외 추가 MARKET)
    market_calls = [c for c in mock_binance.futures_create_order.call_args_list
                    if c.kwargs.get("type") == "MARKET"]
    assert market_calls, "강제청산 MARKET 주문이 없음"
    assert market_calls[-1].kwargs["reduceOnly"] is True
    assert market_calls[-1].kwargs["side"] == "SELL"     # LONG → SELL 청산
    # 보호주문 algo 는 생성되지 않음 (filled=False)
    mock_binance.futures_create_algo_order.assert_not_called()
    # 보호주문 없는 OPEN trade 를 남기지 않음
    assert _fetch_trades(db_path) == []


async def test_await_fill_no_orphan(db_path, mock_binance):
    """조회 실패하지만 실제 포지션 없음 → filled=False, critical=False, 행 없음."""
    mock_binance.futures_exchange_info.return_value = _exinfo()
    mock_binance.futures_create_order.return_value = {"orderId": 111}
    mock_binance.futures_get_order.side_effect = RuntimeError("net down")
    mock_binance.futures_position_information.return_value = []   # 포지션 없음

    ex = TradeExecutor(mock_binance, db_path, dry_run=False,
                       fill_timeout_s=0.03, fill_poll_interval_s=0.01)
    result = await ex.enter_trade(_decision())

    assert result["success"] is False
    assert result["critical"] is False
    assert "no_position" in result["reason"]
    # 강제청산 없음
    market_calls = [c for c in mock_binance.futures_create_order.call_args_list
                    if c.kwargs.get("type") == "MARKET"]
    assert not market_calls
    assert _fetch_trades(db_path) == []


async def test_await_fill_position_check_also_fails_critical(db_path, mock_binance):
    """조회 실패 + 포지션 조회도 실패 → critical=True (fail-closed)."""
    mock_binance.futures_exchange_info.return_value = _exinfo()
    mock_binance.futures_create_order.return_value = {"orderId": 111}
    mock_binance.futures_get_order.side_effect = RuntimeError("net down")
    mock_binance.futures_position_information.side_effect = RuntimeError("pos down")

    ex = TradeExecutor(mock_binance, db_path, dry_run=False,
                       fill_timeout_s=0.03, fill_poll_interval_s=0.01)
    result = await ex.enter_trade(_decision())

    assert result["success"] is False
    assert result["critical"] is True
    assert "position_check_failed" in result["reason"]
    assert _fetch_trades(db_path) == []


async def test_await_fill_final_requery_recovers_filled(db_path, mock_binance):
    """최초 조회 실패 → 최종 재조회에서 FILLED → 정상 진입(보호주문 생성 + 기록)."""
    mock_binance.futures_exchange_info.return_value = _exinfo()
    mock_binance.futures_create_order.return_value = {"orderId": 111}
    # 1회차(루프) 실패, 2회차(최종 재조회) FILLED
    mock_binance.futures_get_order.side_effect = [
        RuntimeError("net blip"),
        {"status": "FILLED", "executedQty": "0.5", "avgPrice": "100.0"},
    ]
    mock_binance.futures_create_algo_order.side_effect = [
        {"algoId": 222}, {"algoId": 333},
    ]

    ex = TradeExecutor(mock_binance, db_path, dry_run=False,
                       fill_timeout_s=0.0, fill_poll_interval_s=0.01)
    result = await ex.enter_trade(_decision())

    assert result["success"] is True
    assert result["critical"] is False
    # 정상 진입 → 보호주문 algo 2건 생성
    assert mock_binance.futures_create_algo_order.call_count == 2
    rows = _fetch_trades(db_path)
    assert len(rows) == 1
    assert rows[0]["trade_status"] == "OPEN"
    assert rows[0]["sl_order_id"] == "222"


# ── A-4. Hedge Mode 차단 ─────────────────────────────────────────────

async def test_hedge_mode_blocks_entry(db_path, mock_binance):
    """dualSidePosition=True → 진입 차단(critical), 어떤 주문도 미전송."""
    mock_binance.futures_get_position_mode.return_value = {"dualSidePosition": True}

    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    result = await ex.enter_trade(_decision())

    assert result["success"] is False
    assert result["critical"] is True
    assert "hedge_mode_blocked" in result["reason"]
    # 진입 LIMIT·보호 algo·정규화(exchangeInfo) 모두 호출 안 됨
    mock_binance.futures_create_order.assert_not_called()
    mock_binance.futures_create_algo_order.assert_not_called()
    mock_binance.futures_exchange_info.assert_not_called()
    assert _fetch_trades(db_path) == []


async def test_one_way_mode_allows_entry(db_path, mock_binance):
    """dualSidePosition=False → 기존 정상 진입 시나리오 통과."""
    mock_binance.futures_get_position_mode.return_value = {"dualSidePosition": False}
    mock_binance.futures_exchange_info.return_value = _exinfo()
    mock_binance.futures_create_order.return_value = {"orderId": 111}
    mock_binance.futures_create_algo_order.side_effect = [
        {"algoId": 222}, {"algoId": 333},
    ]
    mock_binance.futures_get_order.return_value = {
        "status": "FILLED", "executedQty": "0.5", "avgPrice": "100.0",
    }

    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    result = await ex.enter_trade(_decision())

    assert result["success"] is True
    mock_binance.futures_get_position_mode.assert_called_once()
    rows = _fetch_trades(db_path)
    assert len(rows) == 1
    assert rows[0]["trade_status"] == "OPEN"


async def test_position_mode_check_failure_fail_closed(db_path, mock_binance):
    """position mode 조회 실패 → live fail-closed(critical), 주문 미전송."""
    mock_binance.futures_get_position_mode.side_effect = RuntimeError("net down")

    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    result = await ex.enter_trade(_decision())

    assert result["success"] is False
    assert result["critical"] is True
    assert "position_mode_check_failed" in result["reason"]
    mock_binance.futures_create_order.assert_not_called()
    mock_binance.futures_create_algo_order.assert_not_called()
    assert _fetch_trades(db_path) == []


async def test_dry_run_does_not_check_position_mode(db_path, mock_binance):
    """dry_run → futures_get_position_mode 미호출 + 기존 페이퍼 기록 정상."""
    ex = TradeExecutor(mock_binance, db_path, dry_run=True)
    result = await ex.enter_trade(_decision())

    assert result["success"] is True
    mock_binance.futures_get_position_mode.assert_not_called()
    rows = _fetch_trades(db_path)
    assert len(rows) == 1
    assert rows[0]["trade_status"] == "OPEN"


async def test_position_mode_checked_once_and_cached(db_path, mock_binance):
    """요구 7 — position mode 는 첫 진입에 1회만 조회하고 캐시한다."""
    mock_binance.futures_get_position_mode.return_value = {"dualSidePosition": False}
    mock_binance.futures_exchange_info.return_value = _exinfo()
    mock_binance.futures_create_order.return_value = {"orderId": 111}
    mock_binance.futures_create_algo_order.side_effect = [
        {"algoId": 222}, {"algoId": 333}, {"algoId": 224}, {"algoId": 335},
    ]
    mock_binance.futures_get_order.return_value = {
        "status": "FILLED", "executedQty": "0.5", "avgPrice": "100.0",
    }

    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    await ex.enter_trade(_decision(symbol="SOLUSDT"))
    await ex.enter_trade(_decision(symbol="SOLUSDT"))

    # 2회 진입에도 position mode 조회는 1회만 (캐시)
    mock_binance.futures_get_position_mode.assert_called_once()


# ── 4. STOP 생성 실패 → 강제청산 + critical ─────────────────────────

async def test_stop_order_failure_force_closes(db_path, mock_binance):
    """algo STOP 생성 실패 → 즉시 reduceOnly 시장가 청산 + critical + 행 없음."""
    mock_binance.futures_exchange_info.return_value = _exinfo()
    # 진입 LIMIT, 그리고 강제청산 MARKET 도 futures_create_order
    mock_binance.futures_create_order.side_effect = [
        {"orderId": 111},                       # entry LIMIT
        {"orderId": 999},                       # 강제청산 MARKET
    ]
    # algo STOP 생성에서 실패
    mock_binance.futures_create_algo_order.side_effect = RuntimeError("algo stop rejected")
    mock_binance.futures_get_order.return_value = {
        "status": "FILLED", "executedQty": "0.5", "avgPrice": "100.0",
    }

    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    result = await ex.enter_trade(_decision())

    assert result["success"] is False
    assert result["critical"] is True
    # algoOrder 가 시도됐다 (STOP)
    assert mock_binance.futures_create_algo_order.call_count >= 1
    # 강제청산 — 마지막 futures_create_order 호출이 reduceOnly MARKET
    force_kwargs = mock_binance.futures_create_order.call_args_list[-1].kwargs
    assert force_kwargs["type"] == "MARKET"
    assert force_kwargs["reduceOnly"] is True
    assert _fetch_trades(db_path) == []


# ── 5. DB 기록 실패 → 보호주문 cancel + 강제청산 ────────────────────

async def test_db_insert_failure_cancels_and_closes(db_path, mock_binance, monkeypatch):
    """trades INSERT 실패 → algo sl/tp cancel + 강제청산(고아 방지) + critical."""
    mock_binance.futures_exchange_info.return_value = _exinfo()
    # 진입 LIMIT + 강제청산 MARKET 은 futures_create_order
    mock_binance.futures_create_order.side_effect = [
        {"orderId": 111},   # entry LIMIT
        {"orderId": 999},   # 강제청산 MARKET
    ]
    # STOP/TP 는 algo
    mock_binance.futures_create_algo_order.side_effect = [
        {"algoId": 222, "clientAlgoId": "bot-sl-SOLUSDT-aaa"},
        {"algoId": 333, "clientAlgoId": "bot-tp-SOLUSDT-bbb"},
    ]
    mock_binance.futures_get_order.return_value = {
        "status": "FILLED", "executedQty": "0.5", "avgPrice": "100.0",
    }

    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    monkeypatch.setattr(ex, "_insert_trade",
                        MagicMock(side_effect=Exception("db boom")))

    result = await ex.enter_trade(_decision())

    assert result["success"] is False
    assert result["critical"] is True
    # sl(222)+tp(333) 취소 — algo 엔드포인트 사용
    cancelled = {c.kwargs.get("algoId") for c in
                 mock_binance.futures_cancel_algo_order.call_args_list}
    assert 222 in cancelled and 333 in cancelled
    # 진입 LIMIT 의 futures_cancel_order 는 호출되지 않음 (체결됐기 때문)
    mock_binance.futures_cancel_order.assert_not_called()
    # 강제청산 MARKET 호출됨
    assert any(c.kwargs.get("type") == "MARKET"
               for c in mock_binance.futures_create_order.call_args_list)


# ── 6. exchangeInfo 조회 실패 → 주문 차단 ───────────────────────────

async def test_exchange_info_failure_blocks_order(db_path, mock_binance):
    """심볼 필터 조회 실패 → 주문 전송 없이 차단."""
    mock_binance.futures_exchange_info.side_effect = RuntimeError("net down")

    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    result = await ex.enter_trade(_decision())

    assert result["success"] is False
    assert "필터" in result["reason"]
    mock_binance.futures_create_order.assert_not_called()
    assert _fetch_trades(db_path) == []


# ── 7. dry_run → 어떤 Binance API 도 호출 안 함 ─────────────────────

async def test_dry_run_no_binance_calls_but_records(db_path, mock_binance):
    """dry_run → create/get/exchange_info/algo 모두 미호출, DB INSERT 수행."""
    ex = TradeExecutor(mock_binance, db_path, dry_run=True)
    result = await ex.enter_trade(_decision())

    assert result["success"] is True
    mock_binance.futures_create_order.assert_not_called()
    mock_binance.futures_get_order.assert_not_called()
    mock_binance.futures_exchange_info.assert_not_called()
    mock_binance.futures_change_leverage.assert_not_called()
    # 요구 [11] — dry_run 에서 algoOrder API 도 절대 호출 금지
    mock_binance.futures_create_algo_order.assert_not_called()
    mock_binance.futures_cancel_algo_order.assert_not_called()
    rows = _fetch_trades(db_path)
    assert len(rows) == 1
    assert rows[0]["quantity"] == 0.5
    assert rows[0]["trade_status"] == "OPEN"


# ── 8~9. 입력 검증 ──────────────────────────────────────────────────

async def test_missing_required_key_fails(db_path, mock_binance):
    ex = TradeExecutor(mock_binance, db_path, dry_run=True)
    bad = _decision()
    del bad["size_usdt"]
    result = await ex.enter_trade(bad)
    assert result["success"] is False
    assert "필수 키" in result["reason"]
    assert _fetch_trades(db_path) == []


async def test_invalid_action_fails(db_path, mock_binance):
    ex = TradeExecutor(mock_binance, db_path, dry_run=True)
    result = await ex.enter_trade(_decision(action="HOLD"))
    assert result["success"] is False
    assert _fetch_trades(db_path) == []


# ── 10~11. get_open_positions ───────────────────────────────────────

async def test_get_open_positions_live_filters_zero(db_path, mock_binance):
    mock_binance.futures_position_information.return_value = [
        {"symbol": "SOLUSDT", "positionAmt": "1.5", "entryPrice": "100.0",
         "unRealizedProfit": "5.0"},
        {"symbol": "BNBUSDT", "positionAmt": "0", "entryPrice": "0.0",
         "unRealizedProfit": "0.0"},
        {"symbol": "XRPUSDT", "positionAmt": "-20", "entryPrice": "2.0",
         "unRealizedProfit": "-1.0"},
    ]
    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    positions = await ex.get_open_positions()
    assert {p.symbol for p in positions} == {"SOLUSDT", "XRPUSDT"}
    sol = next(p for p in positions if p.symbol == "SOLUSDT")
    xrp = next(p for p in positions if p.symbol == "XRPUSDT")
    assert sol.side == "LONG" and sol.position_amt == 1.5
    assert xrp.side == "SHORT" and xrp.position_amt == -20.0


async def test_get_open_positions_dry_run_reads_db(db_path, mock_binance):
    ex = TradeExecutor(mock_binance, db_path, dry_run=True)
    await ex.enter_trade(_decision(symbol="SOLUSDT", action="LONG"))
    await ex.enter_trade(_decision(symbol="XRPUSDT", action="SHORT"))
    positions = await ex.get_open_positions()
    assert {p.symbol for p in positions} == {"SOLUSDT", "XRPUSDT"}
    mock_binance.futures_position_information.assert_not_called()


# ── 12. 청산 (정밀 PnL 기록) ────────────────────────────────────────

async def test_close_full_live_cancels_protective_and_records_pnl(db_path, mock_binance):
    """전량 청산 → 잔존 algo STOP/TP cancel + 실제 체결가로 PnL 기록 + status=CLOSED."""
    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    _seed_open_trade(ex)   # entry 100, qty 0.5, sl_order "222", tp_order "333"

    mock_binance.futures_position_information.return_value = [
        {"symbol": "SOLUSDT", "positionAmt": "0.5", "entryPrice": "100.0",
         "unRealizedProfit": "0.75"},
    ]
    mock_binance.futures_create_order.return_value = {
        "avgPrice": "101.5", "orderId": 555, "executedQty": "0.5",
    }

    result = await ex.close_position("SOLUSDT", reason="take_profit")
    assert result["success"] is True

    # 보호 주문 cancel 은 algo 엔드포인트 사용 ("222"/"333" 둘 다 숫자 → algoId 라우팅)
    cancelled = {c.kwargs.get("algoId") for c in
                 mock_binance.futures_cancel_algo_order.call_args_list}
    assert 222 in cancelled and 333 in cancelled
    # 진입 LIMIT 의 futures_cancel_order 는 호출되지 않음
    mock_binance.futures_cancel_order.assert_not_called()

    row = _fetch_trades(db_path)[0]
    assert row["exit_price"] == 101.5
    assert row["exit_reason"] == "take_profit"
    assert row["trade_status"] == "CLOSED"
    assert abs(row["pnl_usd"] - 0.75) < 1e-9          # (101.5-100)*0.5
    assert row["pnl_usd_net"] < row["pnl_usd"]         # 수수료 차감
    assert row["duration_seconds"] is not None


async def test_close_full_live_zero_avgprice_uses_account_trades(db_path, mock_binance):
    """시장가 avgPrice=0 → futures_account_trades 로 체결가 보정 (M1)."""
    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    _seed_open_trade(ex)

    mock_binance.futures_position_information.return_value = [
        {"symbol": "SOLUSDT", "positionAmt": "0.5", "entryPrice": "100.0",
         "unRealizedProfit": "0.5"},
    ]
    mock_binance.futures_create_order.return_value = {
        "avgPrice": "0", "orderId": 555, "executedQty": "0.5",
    }
    mock_binance.futures_account_trades.return_value = [
        {"orderId": 555, "price": "101.0", "qty": "0.5", "commission": "0.02"},
    ]

    result = await ex.close_position("SOLUSDT", reason="stop_loss")
    assert result["success"] is True
    row = _fetch_trades(db_path)[0]
    assert row["exit_price"] == 101.0
    assert abs(row["pnl_usd"] - 0.5) < 1e-9
    assert row["trade_status"] == "CLOSED"


async def test_close_position_live_no_position(db_path, mock_binance):
    mock_binance.futures_position_information.return_value = []
    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    result = await ex.close_position("SOLUSDT", reason="system_critical")
    assert result["success"] is True
    assert result["reason"] == "no_position"


# ── 13. 부분 청산 (분할 TP) ─────────────────────────────────────────

async def test_close_position_partial_dry_run_reduces_quantity(db_path, mock_binance):
    ex = TradeExecutor(mock_binance, db_path, dry_run=True)
    await ex.enter_trade(_decision(symbol="SOLUSDT", size_usdt=100.0, entry_price=100.0))
    result = await ex.close_position("SOLUSDT", reason="partial_tp", portion=0.5)
    assert result["success"] is True
    row = _fetch_trades(db_path)[0]
    assert row["quantity"] == 0.5
    assert row["exit_price"] is None


async def test_close_position_invalid_portion_fails(db_path, mock_binance):
    ex = TradeExecutor(mock_binance, db_path, dry_run=True)
    assert (await ex.close_position("SOLUSDT", portion=0.0))["success"] is False
    assert (await ex.close_position("SOLUSDT", portion=1.5))["success"] is False


# ── 14. reconcile_closed_positions ──────────────────────────────────

async def test_reconcile_detects_vanished_position(db_path, mock_binance):
    """추적 중인데 거래소 포지션 소멸 → DB 마감 + 잔존 주문 cancel."""
    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    _seed_open_trade(ex)

    mock_binance.futures_position_information.return_value = []   # 포지션 사라짐
    mock_binance.futures_account_trades.return_value = [
        {"orderId": 555, "price": "98.9", "qty": "0.5", "commission": "0.01"},
    ]

    closed = await ex.reconcile_closed_positions(["SOLUSDT"])
    assert len(closed) == 1
    assert closed[0]["symbol"] == "SOLUSDT"
    row = _fetch_trades(db_path)[0]
    assert row["trade_status"] == "CLOSED"
    assert row["exit_price"] == 98.9
    assert row["exit_reason"] == "exchange_stop_or_tp"


async def test_reconcile_noop_when_position_alive(db_path, mock_binance):
    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    _seed_open_trade(ex)
    mock_binance.futures_position_information.return_value = [
        {"symbol": "SOLUSDT", "positionAmt": "0.5", "entryPrice": "100.0",
         "unRealizedProfit": "0.0"},
    ]
    closed = await ex.reconcile_closed_positions(["SOLUSDT"])
    assert closed == []
    assert _fetch_trades(db_path)[0]["trade_status"] == "OPEN"


async def test_reconcile_dry_run_noop(db_path, mock_binance):
    ex = TradeExecutor(mock_binance, db_path, dry_run=True)
    assert await ex.reconcile_closed_positions(["SOLUSDT"]) == []


# ── 15. replace_stop_order (BE/트레일) ──────────────────────────────

async def test_replace_stop_create_first_success(db_path, mock_binance):
    """A-3 — 새 STOP create 가 cancel 보다 먼저 호출되어야 한다.

    무방비 구간 없음 — 새 STOP 등록 성공 후에만 기존 STOP 취소.
    """
    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    _seed_open_trade(ex)
    # 호출 순서 추적 (한 MagicMock 의 mock_calls 가 호출 순서 보존)
    mock_binance.futures_create_algo_order.return_value = {
        "algoId": 444, "clientAlgoId": "bot-sl-SOLUSDT-ccc",
    }

    new_id = await ex.replace_stop_order("SOLUSDT", "222", "LONG", 100.5)
    assert new_id == "444"

    # create 가 cancel 보다 먼저 호출됐는지 — 순서 검증
    method_order = [
        c[0] for c in mock_binance.mock_calls
        if c[0] in ("futures_create_algo_order", "futures_cancel_algo_order")
    ]
    assert method_order == [
        "futures_create_algo_order", "futures_cancel_algo_order",
    ], f"create-first 순서 위반: {method_order}"

    # 새 STOP payload 검증
    create_kwargs = mock_binance.futures_create_algo_order.call_args.kwargs
    assert create_kwargs["algoType"] == "CONDITIONAL"
    assert create_kwargs["type"] == "STOP_MARKET"
    assert create_kwargs["triggerPrice"] == 100.5
    assert create_kwargs["closePosition"] == "true"
    assert create_kwargs["priceProtect"] == "true"
    assert "quantity" not in create_kwargs
    assert "reduceOnly" not in create_kwargs

    # 기존 STOP cancel — algo 엔드포인트 ("222" → algoId=222)
    cancel_kwargs = mock_binance.futures_cancel_algo_order.call_args.kwargs
    assert cancel_kwargs["algoId"] == 222
    # 진입 LIMIT 의 futures_cancel_order 는 호출되지 않음
    mock_binance.futures_cancel_order.assert_not_called()

    # DB sl_order_id 갱신
    row = _fetch_trades(db_path)[0]
    assert row["sl_order_id"] == "444"


async def test_replace_stop_create_first_fail_keeps_old(db_path, mock_binance):
    """A-3 요구 7 — 새 STOP 생성 실패 시 기존 STOP 은 절대 cancel 하지 않는다."""
    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    _seed_open_trade(ex)
    # 새 STOP 생성에서 예외 발생
    mock_binance.futures_create_algo_order.side_effect = RuntimeError(
        "exchange rejected"
    )

    new_id = await ex.replace_stop_order("SOLUSDT", "222", "LONG", 100.5)

    # 새 ID 없음
    assert new_id is None
    # 기존 STOP cancel 절대 호출 금지 — 거래소 하드 손절 유지
    mock_binance.futures_cancel_algo_order.assert_not_called()
    mock_binance.futures_cancel_order.assert_not_called()
    # DB sl_order_id 그대로
    row = _fetch_trades(db_path)[0]
    assert row["sl_order_id"] == "222"


async def test_replace_stop_old_cancel_failure_keeps_new(db_path, mock_binance):
    """A-3 요구 6 — 기존 STOP cancel 실패는 warning, 새 STOP 은 유지."""
    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    _seed_open_trade(ex)
    mock_binance.futures_create_algo_order.return_value = {"algoId": 444}
    # 기존 STOP cancel 에서 예외 — 거래소가 이미 정리했거나 네트워크 실패
    mock_binance.futures_cancel_algo_order.side_effect = RuntimeError(
        "algo order not found"
    )

    new_id = await ex.replace_stop_order("SOLUSDT", "222", "LONG", 100.5)

    # 새 STOP 은 살아있다
    assert new_id == "444"
    # DB 는 새 ID 로 갱신됐다 (cancel 실패와 무관)
    row = _fetch_trades(db_path)[0]
    assert row["sl_order_id"] == "444"
    # cancel 은 시도됐다 (best-effort)
    mock_binance.futures_cancel_algo_order.assert_called_once()


async def test_replace_stop_uses_algo_cancel(db_path, mock_binance):
    """A-3 요구 9 — 보호주문 cancel 은 반드시 futures_cancel_algo_order 사용."""
    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    _seed_open_trade(ex)
    mock_binance.futures_create_algo_order.return_value = {"algoId": 444}

    await ex.replace_stop_order("SOLUSDT", "222", "LONG", 100.5)

    # algo cancel 호출됨
    mock_binance.futures_cancel_algo_order.assert_called_once()
    # 진입 LIMIT 의 futures_cancel_order 는 보호주문 경로에서 절대 사용 금지
    mock_binance.futures_cancel_order.assert_not_called()


async def test_replace_stop_order_dry_run_noop(db_path, mock_binance):
    """A-3 요구 8 / 기존 요구 [11] — dry_run → algo API 호출 없음, 반환 None."""
    ex = TradeExecutor(mock_binance, db_path, dry_run=True)
    assert await ex.replace_stop_order("SOLUSDT", "222", "LONG", 100.5) is None
    mock_binance.futures_create_order.assert_not_called()
    mock_binance.futures_create_algo_order.assert_not_called()
    mock_binance.futures_cancel_algo_order.assert_not_called()


# ── 16. algoOrder payload 가드/형식 (2025-12-09 전환) ───────────────

def _algo_kwargs_for(symbol: str, action: str, sl: float = 99.0, tp: float = 102.0,
                    working_type: str = "MARK_PRICE", price_protect: bool = True):
    """헬퍼 — TradeExecutor 인스턴스를 한 번 만들고 algoOrder payload 두 개를 반환."""
    from unittest.mock import MagicMock
    binance = MagicMock()
    ex = TradeExecutor(
        binance, db_path=":memory:", dry_run=False,
        working_type=working_type, price_protect=price_protect,
    )
    close_side = "SELL" if action == "LONG" else "BUY"
    sl_payload = ex._build_algo_payload(
        symbol=symbol, side=close_side, order_type="STOP_MARKET",
        trigger_price=sl, client_algo_id="bot-sl-X-1",
    )
    tp_payload = ex._build_algo_payload(
        symbol=symbol, side=close_side, order_type="TAKE_PROFIT_MARKET",
        trigger_price=tp, client_algo_id="bot-tp-X-1",
    )
    return sl_payload, tp_payload


def test_algo_payload_omits_quantity_and_reduce_only():
    """요구 [2]·[3] — closePosition="true" 경로에 quantity / reduceOnly 절대 금지."""
    sl, tp = _algo_kwargs_for("SOLUSDT", "LONG")
    for payload in (sl, tp):
        assert "quantity" not in payload
        assert "reduceOnly" not in payload


def test_algo_payload_uses_trigger_price_not_stop_price():
    """algoOrder 명세는 stopPrice 가 아니라 triggerPrice 키를 사용."""
    sl, tp = _algo_kwargs_for("SOLUSDT", "LONG", sl=99.0, tp=102.0)
    assert "stopPrice" not in sl and "stopPrice" not in tp
    assert sl["triggerPrice"] == 99.0
    assert tp["triggerPrice"] == 102.0


def test_algo_payload_close_position_is_string_true():
    """algoOrder 의 closePosition 은 문자열 "true" (bool True 가 아님)."""
    sl, tp = _algo_kwargs_for("SOLUSDT", "LONG")
    assert sl["closePosition"] == "true"
    assert tp["closePosition"] == "true"


def test_algo_payload_long_close_side_is_sell():
    """요구 [4] — LONG 보호주문은 SELL."""
    sl, tp = _algo_kwargs_for("SOLUSDT", "LONG")
    assert sl["side"] == "SELL"
    assert tp["side"] == "SELL"


def test_algo_payload_short_close_side_is_buy():
    """요구 [4] — SHORT 보호주문은 BUY."""
    sl, tp = _algo_kwargs_for("XRPUSDT", "SHORT")
    assert sl["side"] == "BUY"
    assert tp["side"] == "BUY"


def test_algo_payload_default_working_type_mark_price():
    """요구 [5] — workingType 기본 MARK_PRICE (wick stop hunt 방지)."""
    sl, tp = _algo_kwargs_for("SOLUSDT", "LONG")
    assert sl["workingType"] == "MARK_PRICE"
    assert tp["workingType"] == "MARK_PRICE"


def test_algo_payload_working_type_override_via_config():
    """요구 [5] — workingType 은 config(생성자 인자)로 변경 가능."""
    sl, tp = _algo_kwargs_for("SOLUSDT", "LONG", working_type="CONTRACT_PRICE")
    assert sl["workingType"] == "CONTRACT_PRICE"
    assert tp["workingType"] == "CONTRACT_PRICE"


def test_algo_payload_default_price_protect_true_string():
    """요구 [6] — priceProtect 기본 "true" (소문자, Binance 공식 명세)."""
    sl, _tp = _algo_kwargs_for("SOLUSDT", "LONG")
    assert sl["priceProtect"] == "true"


def test_algo_payload_price_protect_override_false():
    """요구 [6] — priceProtect=False → "false" (소문자)."""
    sl, _tp = _algo_kwargs_for("SOLUSDT", "LONG", price_protect=False)
    assert sl["priceProtect"] == "false"


def test_algo_payload_price_protect_lowercase_true_false():
    """A-3 요구 [10] — Binance 공식 priceProtect 문자열은 소문자 "true"/"false".

    python-binance docstring 이 "TRUE"/"FALSE" 로 잘못 표기돼 있어도 본 봇은
    공식 문서를 따라 소문자로 송신한다.
    """
    sl_true, _ = _algo_kwargs_for("SOLUSDT", "LONG", price_protect=True)
    sl_false, _ = _algo_kwargs_for("SOLUSDT", "LONG", price_protect=False)
    assert sl_true["priceProtect"] == "true"
    assert sl_false["priceProtect"] == "false"
    # closePosition 도 소문자 일관성 확인
    assert sl_true["closePosition"] == "true"
    assert sl_false["closePosition"] == "true"


def test_algo_payload_algotype_conditional_and_client_id():
    """algoType=CONDITIONAL 고정 + clientAlgoId 포함(멱등)."""
    sl, tp = _algo_kwargs_for("SOLUSDT", "LONG")
    for payload in (sl, tp):
        assert payload["algoType"] == "CONDITIONAL"
        assert "clientAlgoId" in payload
        assert isinstance(payload["clientAlgoId"], str)
        assert len(payload["clientAlgoId"]) <= 36


# ── 17. 보호 주문 경로에 구 엔드포인트 사용 금지 회귀 방지 ───────────

async def test_protective_orders_never_use_legacy_create_order(db_path, mock_binance):
    """진입 + 보호주문 시나리오에서 futures_create_order(type=STOP_MARKET/...) 0건.

    -4120 STOP_ORDER_SWITCH_ALGO 회귀 방지 가드.
    """
    mock_binance.futures_exchange_info.return_value = _exinfo()
    mock_binance.futures_create_order.return_value = {"orderId": 111}
    mock_binance.futures_create_algo_order.side_effect = [
        {"algoId": 222}, {"algoId": 333},
    ]
    mock_binance.futures_get_order.return_value = {
        "status": "FILLED", "executedQty": "0.5", "avgPrice": "100.0",
    }

    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    await ex.enter_trade(_decision())

    # 진입 LIMIT 만 1회, STOP_MARKET/TAKE_PROFIT_MARKET 으로 호출된 적 없음
    for call in mock_binance.futures_create_order.call_args_list:
        order_type = call.kwargs.get("type")
        assert order_type not in ("STOP_MARKET", "TAKE_PROFIT_MARKET",
                                  "STOP", "TAKE_PROFIT", "TRAILING_STOP_MARKET"), (
            f"보호주문이 구 엔드포인트로 호출됨 (-4120 회귀): {order_type}"
        )


# ── 18. _cancel_algo_order_safe — algoId(숫자) vs clientAlgoId(문자) 분기 ──

async def test_cancel_algo_uses_algoid_for_numeric_string():
    """DB 에 숫자 문자열 "222" 저장 → cancel 시 algoId(int) 라우팅."""
    from unittest.mock import MagicMock
    binance = MagicMock()
    ex = TradeExecutor(binance, db_path=":memory:", dry_run=False)
    await ex._cancel_algo_order_safe("SOLUSDT", "222")
    binance.futures_cancel_algo_order.assert_called_once_with(
        symbol="SOLUSDT", algoId=222,
    )
    # int 캐스팅이 들어간 구 cancel 은 호출 안 됨 (요구 [10])
    binance.futures_cancel_order.assert_not_called()


async def test_cancel_algo_uses_client_algo_id_for_alphanumeric():
    """DB 에 "bot-sl-..." 같은 문자열 저장 → cancel 시 clientAlgoId 라우팅."""
    from unittest.mock import MagicMock
    binance = MagicMock()
    ex = TradeExecutor(binance, db_path=":memory:", dry_run=False)
    await ex._cancel_algo_order_safe("SOLUSDT", "bot-sl-SOLUSDT-abc123")
    binance.futures_cancel_algo_order.assert_called_once_with(
        symbol="SOLUSDT", clientAlgoId="bot-sl-SOLUSDT-abc123",
    )


async def test_cancel_algo_safe_swallows_error():
    """algo cancel 실패는 best-effort — 예외 삼키고 진행."""
    from unittest.mock import MagicMock
    binance = MagicMock()
    binance.futures_cancel_algo_order.side_effect = RuntimeError("not found")
    ex = TradeExecutor(binance, db_path=":memory:", dry_run=False)
    # 예외 없이 반환
    await ex._cancel_algo_order_safe("SOLUSDT", "222")


async def test_cancel_algo_safe_noop_on_none():
    """algo_id=None 은 no-op (호출조차 없음)."""
    from unittest.mock import MagicMock
    binance = MagicMock()
    ex = TradeExecutor(binance, db_path=":memory:", dry_run=False)
    await ex._cancel_algo_order_safe("SOLUSDT", None)
    binance.futures_cancel_algo_order.assert_not_called()


# ── 19. _extract_algo_id — algoId 우선, clientAlgoId 폴백 ────────────

def test_extract_algo_id_prefers_algoid_over_client():
    resp = {"algoId": 12345, "clientAlgoId": "bot-sl-X-1"}
    assert TradeExecutor._extract_algo_id(resp) == "12345"


def test_extract_algo_id_falls_back_to_client_algo_id():
    resp = {"clientAlgoId": "bot-sl-X-1"}
    assert TradeExecutor._extract_algo_id(resp) == "bot-sl-X-1"


def test_extract_algo_id_returns_none_on_garbage():
    assert TradeExecutor._extract_algo_id(None) is None
    assert TradeExecutor._extract_algo_id({}) is None
    assert TradeExecutor._extract_algo_id("not a dict") is None
