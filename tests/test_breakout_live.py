"""
tests/test_breakout_live.py
=====================================================================
P2 — MainBot 돌파 전략 라이브 통합 (mock 환경, dry_run).

검증:
  1. 기본 활성 전략은 "oi_surge" (기존 동작 불변)
  2. active_strategy="breakout" + 상승 돌파 → _scan_breakout 이 진입(trades 기록,
     setup_tag=breakout_long), 게이트(_execute_decision) 공유
  3. 보호종목(BTCUSDT) → PairWhitelist 차단 → 진입 안 됨
  4. 신호 없음(횡보) → 진입 안 됨
  5. 룩어헤드 차단 — 마지막(형성중) 캔들 제외하고 평가
모든 외부 의존성 mock, executor dry_run + tmp DB (실거래 호출 없음 — CLAUDE.md 규칙).
=====================================================================
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from data.capital_manager import CapitalSnapshot
from db.init_db import init_db
from strategy.breakout import BreakoutConfig
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


def _candle(o, h, low, c, ts=0):
    return (o, h, low, c, 1000.0, ts)


def _uptrend_candles(n=33, slope=2.0):
    """상승 추세 캔들 — closed[:-1] 가 돌파 LONG 신호를 내도록."""
    out = []
    for i in range(n):
        c = 100.0 + i * slope
        out.append(_candle(c, c + 0.5, c - 0.5, c, i))
    return out


def _flat_candles(n=33):
    out = []
    for i in range(n):
        c = 100.0 + (1.0 if i % 2 else -1.0)
        out.append(_candle(c, c + 0.5, c - 0.5, c, i))
    return out


@pytest.fixture
def db_path(tmp_path) -> str:
    p = tmp_path / "bot.db"
    init_db(p)
    return str(p)


def _make_bot(db_path, *, active_strategy="breakout", candles=None):
    collector = MagicMock()
    collector.client = MagicMock()
    collector.client.get_account.side_effect = Exception("permission denied")
    collector.get_candles = AsyncMock(return_value=candles or [])
    telegram = MagicMock()
    telegram.send = AsyncMock()
    bot = MainBot(
        dry_run=True, db_path=db_path, collector=collector,
        openai_client=None, telegram=telegram, active_strategy=active_strategy,
    )
    # 작은 지표 설정(테스트 데이터 소형화) — 라이브 기본은 ema200
    bot.breakout_cfg = BreakoutConfig(
        donchian_entry=10, adx_period=5, ema_period=20, atr_period=5,
        atr_stop_mult=2.0, atr_target_mult=4.0,
    )
    # 리스크 게이트는 통과 고정(capital_manager DB 의존성 우회 — test_sol 패턴)
    bot.risk_manager.check_all = AsyncMock(return_value=True)
    return bot


def _fetch_all(db_path, table):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        conn.close()


def test_default_active_strategy_is_oi_surge(db_path, monkeypatch):
    monkeypatch.delenv("ACTIVE_STRATEGY", raising=False)
    bot = _make_bot(db_path, active_strategy=None)
    assert bot.active_strategy == "oi_surge"      # 기존 동작 불변


def test_resolve_active_strategy_daily_tsmom(monkeypatch):
    """M15: ACTIVE_STRATEGY=daily_tsmom 가 화이트리스트에 추가됨 (oi_surge 폴백 아님)."""
    from config.settings import _resolve_active_strategy

    monkeypatch.setenv("ACTIVE_STRATEGY", "daily_tsmom")
    assert _resolve_active_strategy() == "daily_tsmom"
    monkeypatch.setenv("ACTIVE_STRATEGY", "breakout")
    assert _resolve_active_strategy() == "breakout"
    monkeypatch.setenv("ACTIVE_STRATEGY", "bogus_strategy")
    assert _resolve_active_strategy() == "oi_surge"      # 무효값 → 안전 폴백


def test_daily_tsmom_config_fields():
    """M15: StrategyConfig 에 daily_tsmom_interval/limit 추가."""
    from config.settings import STRATEGY_CONFIG

    assert STRATEGY_CONFIG.daily_tsmom_interval == "1d"
    assert STRATEGY_CONFIG.daily_tsmom_limit >= 201      # ema200 + 형성중 1개 여유


async def test_breakout_long_enters_and_records(db_path):
    bot = _make_bot(db_path, candles=_uptrend_candles())
    await bot._scan_breakout(["SOLUSDT"], _regime(), _snapshot())

    trades = _fetch_all(db_path, "trades")
    assert len(trades) == 1
    assert trades[0]["symbol"] == "SOLUSDT"
    assert trades[0]["action"] == "LONG"
    assert trades[0]["setup_tag"] == "breakout_long"
    sent = [c.args[0] for c in bot.telegram.send.call_args_list]
    assert any("✅ 진입" in m and "SOLUSDT" in m for m in sent)


async def test_breakout_protected_symbol_blocked(db_path):
    bot = _make_bot(db_path, candles=_uptrend_candles())
    bot.executor.enter_trade = AsyncMock()
    assert "BTCUSDT" in bot.pair_wl.protected_symbols
    await bot._scan_breakout(["BTCUSDT"], _regime(), _snapshot())
    bot.executor.enter_trade.assert_not_awaited()
    assert _fetch_all(db_path, "trades") == []


async def test_breakout_no_signal_skips(db_path):
    bot = _make_bot(db_path, candles=_flat_candles())     # 횡보 → ADX 낮음 → 무신호
    bot.executor.enter_trade = AsyncMock()
    await bot._scan_breakout(["SOLUSDT"], _regime(), _snapshot())
    bot.executor.enter_trade.assert_not_awaited()


async def test_breakout_excludes_forming_candle(db_path):
    """마지막(형성중) 캔들을 제외하고 평가한다 — 진입가는 그 형성중 캔들의 종가."""
    candles = _uptrend_candles()
    bot = _make_bot(db_path, candles=candles)
    captured = {}
    real = bot._execute_decision

    async def spy(**kw):
        captured.update(kw)
        return await real(**kw)

    bot._execute_decision = spy
    await bot._scan_breakout(["SOLUSDT"], _regime(), _snapshot())
    # 진입가 = 마지막(형성중) 캔들 종가
    assert captured.get("entry_price") == candles[-1][3]
    assert captured.get("action") == "LONG"
