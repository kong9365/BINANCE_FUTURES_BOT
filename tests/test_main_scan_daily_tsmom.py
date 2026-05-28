"""
tests/test_main_scan_daily_tsmom.py
=====================================================================
M15 — MainBot 의 1d DailyTSMOM 라이브 분기 (_scan_daily_tsmom).

검증:
  1. active_strategy="daily_tsmom" → DailyTSMOMDonchianSkill.evaluate 신호원
  2. 상승 추세 → LONG 진입 + setup_tag=1d_tsmom_donchian_long_v1 (registry 연결)
  3. 보호종목(BTCUSDT) → PairWhitelist 차단 → 진입 안 됨
  4. 평탄(횡보) → FLAT → 진입 안 됨
  5. 룩어헤드 차단 — skill 에 candles[:-1] 전달, 진입가 = 형성중 캔들 종가
  6. _execute_decision 에 decision(SignalDecision) 전달 (shadow 용)
모든 외부 의존성 mock, dry_run + tmp DB (실거래 호출 없음 — CLAUDE.md 규칙).
=====================================================================
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from audit.signal_decision import SignalDecision
from data.capital_manager import CapitalSnapshot
from db.init_db import init_db
from strategy.regime_detector import Regime
from main_7590 import MainBot


def _snapshot(*, wallet=1000.0, available=300.0, locked=0.0):
    return CapitalSnapshot(
        wallet_balance=wallet, margin_balance=wallet, available_balance=available,
        locked_margin=locked, unrealized_pnl=0.0, timestamp=0.0,
    )


def _regime(regime=Regime.TREND_UP, confidence=0.8):
    return SimpleNamespace(
        regime=regime, confidence=confidence, regime_changed=False, prev_regime=None,
        reasons=["test"], adx_4h=30.0, atr_ratio_1h=1.0, atr_ratio_4h=1.0,
        bb_width_pct=3.0, ema_slope=0.1, funding_rate=0.0001,
    )


def _make_uptrend_candles(n: int = 250) -> list:
    """강한 상승 추세 (test_skills_daily_tsmom_donchian 와 동일 — LONG 신호)."""
    candles = []
    for i in range(n):
        if i < n - 50:
            price = 100.0 + i * 0.05
        else:
            price = 100.0 + (n - 50) * 0.05 + (i - (n - 50)) * 3.0
        candles.append((price, price + 0.5, price - 0.5, price, 1000.0, i * 86400))
    return candles


def _make_flat_candles(n: int = 250) -> list:
    return [(100.0, 100.1, 99.9, 100.0, 1000.0, i * 86400) for i in range(n)]


@pytest.fixture
def db_path(tmp_path) -> str:
    p = tmp_path / "bot.db"
    init_db(p)
    return str(p)


def _make_bot(db_path, *, candles=None):
    collector = MagicMock()
    collector.client = MagicMock()
    collector.client.get_account.side_effect = Exception("permission denied")
    collector.get_candles = AsyncMock(return_value=candles or [])
    telegram = MagicMock()
    telegram.send = AsyncMock()
    bot = MainBot(
        dry_run=True, db_path=db_path, collector=collector,
        openai_client=None, telegram=telegram, active_strategy="daily_tsmom",
    )
    bot.risk_manager.check_all = AsyncMock(return_value=True)
    return bot


def _fetch_all(db_path, table):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        conn.close()


def test_active_strategy_daily_tsmom_set(db_path):
    bot = _make_bot(db_path)
    assert bot.active_strategy == "daily_tsmom"
    assert bot.daily_tsmom_skill is not None


async def test_daily_tsmom_long_enters_with_registry_setup_tag(db_path):
    """상승 추세 → LONG 진입, setup_tag=1d_tsmom_donchian_long_v1."""
    bot = _make_bot(db_path, candles=_make_uptrend_candles(250))
    await bot._scan_daily_tsmom(["SOLUSDT"], _regime(), _snapshot())

    trades = _fetch_all(db_path, "trades")
    assert len(trades) == 1
    assert trades[0]["symbol"] == "SOLUSDT"
    assert trades[0]["action"] == "LONG"
    assert trades[0]["setup_tag"] == "1d_tsmom_donchian_long_v1"


async def test_daily_tsmom_protected_symbol_blocked(db_path):
    """보호종목 BTCUSDT → 진입 차단."""
    bot = _make_bot(db_path, candles=_make_uptrend_candles(250))
    bot.executor.enter_trade = AsyncMock()
    assert "BTCUSDT" in bot.pair_wl.protected_symbols
    await bot._scan_daily_tsmom(["BTCUSDT"], _regime(), _snapshot())
    bot.executor.enter_trade.assert_not_awaited()
    assert _fetch_all(db_path, "trades") == []


async def test_daily_tsmom_no_signal_skips(db_path):
    """평탄 → FLAT → 진입 안 됨."""
    bot = _make_bot(db_path, candles=_make_flat_candles(250))
    bot.executor.enter_trade = AsyncMock()
    await bot._scan_daily_tsmom(["SOLUSDT"], _regime(), _snapshot())
    bot.executor.enter_trade.assert_not_awaited()


async def test_daily_tsmom_excludes_forming_candle(db_path):
    """룩어헤드 차단 — skill 은 candles[:-1] 받고, 진입가는 형성중 캔들 종가."""
    candles = _make_uptrend_candles(250)
    bot = _make_bot(db_path, candles=candles)

    captured = {}
    real_eval = bot.daily_tsmom_skill.evaluate

    def eval_spy(symbol, c, market_state=None):
        captured["n_candles"] = len(c)
        captured["last_passed"] = c[-1]
        return real_eval(symbol, c, market_state)

    bot.daily_tsmom_skill.evaluate = eval_spy

    exec_captured = {}
    real_exec = bot._execute_decision

    async def exec_spy(**kw):
        exec_captured.update(kw)
        return await real_exec(**kw)

    bot._execute_decision = exec_spy
    await bot._scan_daily_tsmom(["SOLUSDT"], _regime(), _snapshot())

    # skill 에 형성중 캔들 제외(249)가 전달됨
    assert captured["n_candles"] == len(candles) - 1
    assert captured["last_passed"] == candles[-2]
    # 진입가 = 마지막(형성중) 캔들 종가
    assert exec_captured.get("entry_price") == candles[-1][3]
    # SignalDecision 이 _execute_decision 으로 전달됨 (shadow 용)
    assert isinstance(exec_captured.get("decision"), SignalDecision)
    assert exec_captured["decision"].setup_id == "1d_tsmom_donchian_long_v1"
