"""
tests/test_breakout.py
=====================================================================
strategy/breakout.py + BacktestEngine(strategy="breakout") 단위 테스트.

검증:
  지표:
    1. ema — 상수열 → 상수
    2. atr — 알려진 TR 평균
    3. adx — 강한 추세 高 / 횡보 低 (방향성)
    4. donchian — 직전 N봉(마지막 제외) 고/저
  신호:
    5. evaluate_breakout — 상승 돌파 → LONG
    6. evaluate_breakout — 횡보(ADX 낮음) → None
    7. evaluate_breakout — 하락 돌파 → SHORT
    8. evaluate_breakout — 봉 부족 → None
  엔진 통합:
    9. strategy="breakout" 백테스트 → breakout_long 거래 ≥1, 룩어헤드 없음(설계상)
=====================================================================
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from backtesting.backtest_engine import BacktestConfig, BacktestEngine, Signal
from strategy import breakout as bo


def _c(o, h, low, close, ts=0):
    return (o, h, low, close, 1000.0, ts)


# ── 지표 ──────────────────────────────────────────────────────────────
def test_ema_constant_series():
    assert bo.ema([5.0] * 10, 3) == 5.0
    assert bo.ema([1.0, 2.0], 3) is None      # 부족


def test_atr_known_value():
    highs = [10, 11, 12]
    lows = [8, 9, 10]
    closes = [9, 10, 11]
    assert bo.atr(highs, lows, closes, 2) == 2.0


def test_adx_high_for_trend_low_for_chop():
    # 강한 단조 상승 → ADX 높음
    n = 40
    highs = [100 + i for i in range(n)]
    lows = [99 + i for i in range(n)]
    closes = [99.5 + i for i in range(n)]
    trend_adx = bo.adx(highs, lows, closes, 5)
    # 톱니 횡보 → ADX 낮음
    chop_h = [101 if i % 2 else 100 for i in range(n)]
    chop_l = [99 if i % 2 else 98 for i in range(n)]
    chop_c = [100 if i % 2 else 99 for i in range(n)]
    chop_adx = bo.adx(chop_h, chop_l, chop_c, 5)
    assert trend_adx > 25.0
    assert chop_adx < trend_adx
    assert chop_adx < 25.0


def test_donchian_excludes_last_bar():
    highs = [1, 2, 3, 4, 5]
    lows = [1, 1, 1, 1, 0]
    up, low = bo.donchian(highs, lows, 3)
    assert up == 4         # max(highs[-4:-1]) = max(2,3,4)
    assert low == 1        # min(lows[-4:-1]) = min(1,1,1)
    assert bo.donchian([1, 2], [1, 1], 3) is None


# ── 신호 ──────────────────────────────────────────────────────────────
_CFG = bo.BreakoutConfig(donchian_entry=10, adx_period=5, ema_period=20,
                         atr_period=5)


def _uptrend(n=30, slope=2.0):
    out = []
    for i in range(n):
        c = 100.0 + i * slope
        out.append(_c(c, c + 0.5, c - 0.5, c, i))
    return out


def test_evaluate_breakout_long():
    sig = bo.evaluate_breakout(_uptrend(), _CFG)
    assert sig is not None and sig.action == "LONG"
    assert sig.atr > 0 and sig.adx >= _CFG.adx_trend_min


def test_evaluate_breakout_ranging_none():
    rng = []
    for i in range(40):
        c = 100.0 + (1.0 if i % 2 else -1.0)
        rng.append(_c(c, c + 0.5, c - 0.5, c, i))
    assert bo.evaluate_breakout(rng, _CFG) is None     # ADX 낮음 → 무거래


def test_evaluate_breakout_short():
    down = []
    for i in range(30):
        c = 200.0 - i * 2.0
        down.append(_c(c, c + 0.5, c - 0.5, c, i))
    sig = bo.evaluate_breakout(down, _CFG)
    assert sig is not None and sig.action == "SHORT"


def test_evaluate_breakout_insufficient_bars():
    assert bo.evaluate_breakout(_uptrend(n=5), _CFG) is None


# ── 엔진 통합 ──────────────────────────────────────────────────────────
def _uptrend_df(n=90):
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    rows = []
    price = 100.0
    for _ in range(n):
        o = price
        c = price * 1.003                      # +0.3%/봉, 완만하지만 지속(HIGH_VOL 아님)
        rows.append({"open": o, "high": max(o, c) * 1.001,
                     "low": min(o, c) * 0.999, "close": c, "volume": 1000.0})
        price = c
    return pd.DataFrame(rows, index=idx)


def test_engine_breakout_generates_trades():
    cfg = BacktestConfig(
        pairs=["TESTUSDT"], strategy="breakout",
        breakout_donchian=10, breakout_adx_min=20.0, breakout_ema=20,
    )
    result = BacktestEngine(cfg).run({"TESTUSDT": _uptrend_df()})
    assert result.total_trades >= 1
    assert any(tag.startswith("breakout_") for tag in result.setup_stats)


def _sig(entry_ts, atr=1.0):
    return Signal(
        symbol="X", action="LONG", setup_tag="breakout_long", regime="TREND_UP",
        confidence=0.7, entry_ts=entry_ts, entry_price=100.0,
        tp_price=200.0, sl_price=98.0, atr=atr, pair_tier=2, size_usdt=100.0,
    )


def test_simulate_trade_trail_exit():
    """breakout_trail_exit=True → ATR 샹들리에 트레일로 청산(TRAIL)."""
    idx = pd.date_range("2026-01-01", periods=5, freq="1h", tz="UTC")
    df = pd.DataFrame([
        {"open": 99, "high": 99, "low": 99, "close": 99, "volume": 1},      # 진입 전
        {"open": 100, "high": 101, "low": 99.5, "close": 100.5, "volume": 1},  # 진입봉
        {"open": 100.5, "high": 105, "low": 100, "close": 104, "volume": 1},   # 상승(트레일 ratchet)
        {"open": 104, "high": 110, "low": 103, "close": 109, "volume": 1},
        {"open": 109, "high": 109, "low": 104, "close": 105, "volume": 1},     # 되돌림 → 트레일 청산
    ], index=idx)
    cfg = BacktestConfig(strategy="breakout", breakout_trail_exit=True,
                         breakout_trail_atr_mult=3.0, time_stop_bars=999,
                         apply_funding=False)
    eng = BacktestEngine(cfg)
    eng._candles_by_pair = {"X": df}
    eng._bar_seconds = 3600.0
    trade = eng._simulate_trade(_sig(idx[1]))
    assert trade is not None
    assert trade.exit_reason == "TRAIL"


def test_simulate_trade_trail_off_uses_fixed_tp_sl():
    """trail off(기본) → 고정 TP/SL 동작 불변(회귀 가드)."""
    idx = pd.date_range("2026-01-01", periods=4, freq="1h", tz="UTC")
    df = pd.DataFrame([
        {"open": 99, "high": 99, "low": 99, "close": 99, "volume": 1},
        {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},   # 진입
        {"open": 100, "high": 100, "low": 97, "close": 97.5, "volume": 1},  # SL(98) 히트
        {"open": 97, "high": 98, "low": 96, "close": 97, "volume": 1},
    ], index=idx)
    cfg = BacktestConfig(strategy="breakout", breakout_trail_exit=False,
                         time_stop_bars=999, apply_funding=False)
    eng = BacktestEngine(cfg)
    eng._candles_by_pair = {"X": df}
    eng._bar_seconds = 3600.0
    trade = eng._simulate_trade(_sig(idx[1]))
    assert trade.exit_reason == "SL"
