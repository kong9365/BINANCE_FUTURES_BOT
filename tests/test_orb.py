"""
tests/test_orb.py
=====================================================================
strategy/orb.py — 세션-앵커 ORB 신호 단위 테스트.
앵커 봉/돌파/일별 첫 신호/윈도/룩어헤드 안전성 검증.
=====================================================================
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategy.orb import ORBConfig, compute_orb_signals
from backtesting import portfolio_backtest as pbt


_CFG = ORBConfig(anchor_hour=14, signal_window_bars=6, atr_period=5)


def _bars(date_str, hours, opens=None, highs=None, lows=None, closes=None):
    """주어진 날짜·시각 리스트로 OHLCV df 구성(1h UTC)."""
    idx = pd.DatetimeIndex([pd.Timestamp(f"{date_str} {h:02d}:00", tz="UTC")
                            for h in hours])
    n = len(idx)
    df = pd.DataFrame({
        "open": opens or [100.0] * n,
        "high": highs or [101.0] * n,
        "low":  lows or [99.0] * n,
        "close": closes or [100.0] * n,
        "volume": [1000.0] * n,
    }, index=idx)
    return df


def test_no_datetime_index_no_signal():
    df = pd.DataFrame({"open":[100]*30,"high":[101]*30,"low":[99]*30,
                       "close":[100]*30,"volume":[1]*30})
    out = compute_orb_signals(df, _CFG)
    assert (out["signal"] == 0).all()
    assert "atr" in out.columns


def test_long_breakout_at_first_breach():
    """앵커 14:00(h=105, l=95) → 15:00 close=110 → LONG 신호."""
    days = []
    for d in range(8):                              # ATR 워밍업용 며칠
        date = f"2026-01-{d+1:02d}"
        # 12,13,14(앵커),15,16,17,18,19 — 8봉
        # 안정 시계열 + ATR 워밍업
        days.append(_bars(date, [12,13,14,15,16,17,18,19],
            opens=[100,100,100,100,100,100,100,100],
            highs=[101,101,101,101,101,101,101,101],
            lows= [99,99,99,99,99,99,99,99],
            closes=[100,100,100,100,100,100,100,100]))
    # 마지막 날에 돌파 시나리오
    last_date = "2026-01-09"
    breakout = _bars(last_date, [12,13,14,15,16,17,18,19],
        opens=[100,100,100,108,100,100,100,100],
        highs=[101,101,105,112,100,100,100,100],   # 14:00 앵커 h=105
        lows= [99,99,95,108,99,99,99,99],          # 14:00 앵커 l=95
        closes=[100,100,100,110,100,100,100,100])  # 15:00 close=110 > 105 → LONG
    days.append(breakout)
    df = pd.concat(days)
    out = compute_orb_signals(df, _CFG)
    # 15:00 last_date 위치에서 +1
    ts = pd.Timestamp(f"{last_date} 15:00", tz="UTC")
    assert out.loc[ts, "signal"] == 1


def test_short_breakdown_at_first_breach():
    days = []
    for d in range(8):
        date = f"2026-02-{d+1:02d}"
        days.append(_bars(date, [12,13,14,15,16,17,18,19]))
    last_date = "2026-02-09"
    breakdown = _bars(last_date, [12,13,14,15,16,17,18,19],
        opens=[100]*8,
        highs=[101,101,105,92,99,99,99,99],
        lows= [99,99,95,88,89,89,89,89],
        closes=[100,100,100,90,99,99,99,99])       # 15:00 close=90 < 95 → SHORT
    days.append(breakdown)
    df = pd.concat(days)
    out = compute_orb_signals(df, _CFG)
    ts = pd.Timestamp(f"{last_date} 15:00", tz="UTC")
    assert out.loc[ts, "signal"] == -1


def test_only_first_breach_per_day_fires():
    days = []
    for d in range(8):
        date = f"2026-03-{d+1:02d}"
        days.append(_bars(date, [12,13,14,15,16,17,18,19]))
    last_date = "2026-03-09"
    multi = _bars(last_date, [12,13,14,15,16,17,18,19],
        opens=[100]*8,
        highs=[101,101,105,112,115,115,115,115],
        lows= [99,99,95,108,110,110,110,110],
        closes=[100,100,100,110,113,115,115,115])  # 15,16 모두 breach → 첫 봉만
    days.append(multi)
    out = compute_orb_signals(pd.concat(days), _CFG)
    sigs_day = out.loc[f"{last_date} 15:00":f"{last_date} 23:00", "signal"]
    assert (sigs_day == 1).sum() == 1               # 단 1회만


def test_no_breakout_no_signal():
    days = []
    for d in range(8):
        date = f"2026-04-{d+1:02d}"
        days.append(_bars(date, [12,13,14,15,16,17,18,19]))
    out = compute_orb_signals(pd.concat(days), _CFG)
    assert (out["signal"] == 0).all()


def test_signal_window_excludes_late_breakout():
    """앵커 14h, window 6h → 14+6=20h 까지만. 22:00 늦은 돌파는 0."""
    days = []
    for d in range(8):
        date = f"2026-05-{d+1:02d}"
        days.append(_bars(date, [12,13,14,15,16,17,18,19,22],
            opens=[100]*9, highs=[101,101,105,101,101,101,101,101,120],
            lows=[99]*9, closes=[100]*8 + [115]))  # 22:00 늦은 돌파
    out = compute_orb_signals(pd.concat(days), _CFG)
    # 22:00 위치들은 모두 0
    late = out[out.index.hour == 22]["signal"]
    assert (late == 0).all()


def test_harness_integration_orb():
    """run_portfolio 가 ORB signal_fn 으로 정상 실행된다(스모크)."""
    days = []
    for d in range(15):                             # 충분한 워밍업+신호 기회
        date = f"2026-06-{d+1:02d}"
        # day 10에 돌파 시나리오 1회
        if d == 10:
            highs = [101,101,105,112,101,101,101,101]
            closes = [100,100,100,110,101,101,101,101]
        else:
            highs = [101]*8; closes = [100]*8
        days.append(_bars(date, [12,13,14,15,16,17,18,19],
            opens=[100]*8, highs=highs, lows=[99]*8, closes=closes))
    df = pd.concat(days)
    port = pbt.PortfolioConfig(atr_stop_mult=1.5, atr_target_mult=3.0, time_stop_bars=8)
    r = pbt.run_portfolio({"SOLUSDT": df}, tiers={"SOLUSDT": 3},
                          port_cfg=port,
                          signal_fn=lambda d: compute_orb_signals(d, _CFG))
    assert r.total_trades >= 1
