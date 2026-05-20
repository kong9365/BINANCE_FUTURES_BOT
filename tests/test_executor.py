"""
tests/test_executor.py
=====================================================================
TradeExecutor 단위 테스트 (v3.1.2 — 체결 확인 + 거래소 보호 주문 기준).

근거:
  - docs/SPEC_v3.1.md §8-8 (Layer 5), §8-8-2 (Post-Only Limit)
  - 감사 후속: C1(거래소 STOP)/C2(FILLED 확인)/H1(유령 행 방지)/H2(멱등)/
    M1(정밀 청산가)/M3(exchangeInfo 정규화)

구성:
  - DB 는 tmp_path 임시 SQLite (db.init_db.init_db 로 v3.1.2 까지 적용)
  - binance_client 는 MagicMock 주입 (실제 네트워크 호출 없음)
  - pytest asyncio_mode=auto → async def test_* 직접 사용

검증 요지:
  - live 진입은 FILLED 확인 + STOP/TP 생성 후에만 trades 기록(status='OPEN').
  - 미체결(타임아웃)/거부 → cancel + 진입 스킵 + 행 없음.
  - STOP 생성 실패 → 즉시 강제청산 + critical + 행 없음.
  - DB 기록 실패 → 보호주문 cancel + 강제청산(고아 방지).
  - exchangeInfo 조회 실패 → 주문 차단.
  - dry_run → 어떤 Binance API 도 호출 안 함 + DB INSERT.
  - 청산 → 잔존 STOP/TP cancel + 실제 체결가/수수료로 정밀 PnL 기록.
  - reconcile_closed_positions / replace_stop_order.
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
    """GTX 체결(FILLED) → STOP/TP 생성 → trades INSERT(status='OPEN')."""
    mock_binance.futures_exchange_info.return_value = _exinfo()
    mock_binance.futures_create_order.side_effect = [
        {"orderId": 111},   # entry
        {"orderId": 222},   # STOP
        {"orderId": 333},   # TP
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
    calls = mock_binance.futures_create_order.call_args_list
    entry_kwargs = calls[0].kwargs
    assert entry_kwargs["side"] == "BUY"
    assert entry_kwargs["type"] == "LIMIT"
    assert entry_kwargs["timeInForce"] == "GTX"
    assert entry_kwargs["quantity"] == 0.5
    assert entry_kwargs["price"] == 100.0
    assert entry_kwargs["newClientOrderId"].startswith("bot-")
    # STOP 주문 — reduceOnly STOP_MARKET closePosition
    stop_kwargs = calls[1].kwargs
    assert stop_kwargs["type"] == "STOP_MARKET"
    assert stop_kwargs["side"] == "SELL"
    assert stop_kwargs["stopPrice"] == 99.0
    assert stop_kwargs["closePosition"] is True
    # TP 주문
    assert calls[2].kwargs["type"] == "TAKE_PROFIT_MARKET"

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


# ── 4. STOP 생성 실패 → 강제청산 + critical ─────────────────────────

async def test_stop_order_failure_force_closes(db_path, mock_binance):
    """손절 주문 실패 → 즉시 reduceOnly 시장가 청산 + critical + 행 없음."""
    mock_binance.futures_exchange_info.return_value = _exinfo()
    mock_binance.futures_create_order.side_effect = [
        {"orderId": 111},                       # entry
        RuntimeError("stop rejected"),          # STOP 실패
        {"orderId": 999},                       # 강제청산 MARKET
    ]
    mock_binance.futures_get_order.return_value = {
        "status": "FILLED", "executedQty": "0.5", "avgPrice": "100.0",
    }

    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    result = await ex.enter_trade(_decision())

    assert result["success"] is False
    assert result["critical"] is True
    # 마지막(3번째) 주문은 reduceOnly MARKET 강제청산
    force_kwargs = mock_binance.futures_create_order.call_args_list[2].kwargs
    assert force_kwargs["type"] == "MARKET"
    assert force_kwargs["reduceOnly"] is True
    assert _fetch_trades(db_path) == []


# ── 5. DB 기록 실패 → 보호주문 cancel + 강제청산 ────────────────────

async def test_db_insert_failure_cancels_and_closes(db_path, mock_binance, monkeypatch):
    """trades INSERT 실패 → sl/tp cancel + 강제청산(고아 방지) + critical."""
    mock_binance.futures_exchange_info.return_value = _exinfo()
    mock_binance.futures_create_order.side_effect = [
        {"orderId": 111}, {"orderId": 222}, {"orderId": 333},   # entry/STOP/TP
        {"orderId": 999},                                       # 강제청산
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
    # sl(222)+tp(333) 취소
    cancelled = {c.kwargs.get("orderId") for c in
                 mock_binance.futures_cancel_order.call_args_list}
    assert 222 in cancelled and 333 in cancelled
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
    """dry_run → create/get/exchange_info 모두 미호출, DB INSERT 수행."""
    ex = TradeExecutor(mock_binance, db_path, dry_run=True)
    result = await ex.enter_trade(_decision())

    assert result["success"] is True
    mock_binance.futures_create_order.assert_not_called()
    mock_binance.futures_get_order.assert_not_called()
    mock_binance.futures_exchange_info.assert_not_called()
    mock_binance.futures_change_leverage.assert_not_called()
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
    """전량 청산 → 잔존 STOP/TP cancel + 실제 체결가로 PnL 기록 + status=CLOSED."""
    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    _seed_open_trade(ex)   # entry 100, qty 0.5, sl_order 222, tp_order 333

    mock_binance.futures_position_information.return_value = [
        {"symbol": "SOLUSDT", "positionAmt": "0.5", "entryPrice": "100.0",
         "unRealizedProfit": "0.75"},
    ]
    mock_binance.futures_create_order.return_value = {
        "avgPrice": "101.5", "orderId": 555, "executedQty": "0.5",
    }

    result = await ex.close_position("SOLUSDT", reason="take_profit")
    assert result["success"] is True

    cancelled = {c.kwargs.get("orderId") for c in
                 mock_binance.futures_cancel_order.call_args_list}
    assert 222 in cancelled and 333 in cancelled

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

async def test_replace_stop_order_cancels_old_and_creates_new(db_path, mock_binance):
    ex = TradeExecutor(mock_binance, db_path, dry_run=False)
    _seed_open_trade(ex)
    mock_binance.futures_create_order.return_value = {"orderId": 444}

    new_id = await ex.replace_stop_order("SOLUSDT", "222", "LONG", 100.5)
    assert new_id == "444"
    mock_binance.futures_cancel_order.assert_called_once()
    create_kwargs = mock_binance.futures_create_order.call_args.kwargs
    assert create_kwargs["type"] == "STOP_MARKET"
    assert create_kwargs["stopPrice"] == 100.5
    assert create_kwargs["closePosition"] is True
    # DB sl_order_id 갱신
    row = _fetch_trades(db_path)[0]
    assert row["sl_order_id"] == "444"


async def test_replace_stop_order_dry_run_noop(db_path, mock_binance):
    ex = TradeExecutor(mock_binance, db_path, dry_run=True)
    assert await ex.replace_stop_order("SOLUSDT", "222", "LONG", 100.5) is None
    mock_binance.futures_create_order.assert_not_called()
