"""
tests/test_funding_fade.py
=====================================================================
strategy/funding_fade.py — 펀딩 극단 역추세 신호 + 하네스 통합 단위 테스트.
=====================================================================
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategy.funding_fade import FundingFadeConfig, compute_funding_fade_signals
from backtesting import portfolio_backtest as pbt


def _ranging_df(n=80, funding=None):
    """비추세(횡보) + 펀딩 컬럼. 펀딩 극단 시 페이드 신호 확인용."""
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    # 톱니 횡보(ADX 낮게)
    base = 100.0 + np.where(np.arange(n) % 2 == 0, -1.0, 1.0)
    rows = {"open": base, "high": base + 0.5, "low": base - 0.5,
            "close": base, "volume": 1000.0}
    df = pd.DataFrame(rows, index=idx)
    if funding is not None:
        df["funding_rate"] = funding
    return df


_CFG = FundingFadeConfig(z_window=20, z_threshold=1.5, adx_period=5, atr_period=5)


def test_no_funding_column_no_signal():
    df = _ranging_df()
    out = compute_funding_fade_signals(df, _CFG)
    assert (out["signal"] == 0).all()
    assert "atr" in out.columns


def test_high_funding_extreme_triggers_short():
    n = 80
    fr = np.full(n, 0.0001)
    fr[70] = 0.01            # 극단 高 펀딩(롱 과밀) → SHORT 페이드 기대
    out = compute_funding_fade_signals(_ranging_df(n, fr), _CFG)
    assert (out["signal"] == -1).any()


def test_low_funding_extreme_triggers_long():
    n = 80
    fr = np.full(n, 0.0001)
    fr[70] = -0.01           # 극단 低(숏 과밀) → LONG 페이드 기대
    out = compute_funding_fade_signals(_ranging_df(n, fr), _CFG)
    assert (out["signal"] == 1).any()


def test_harness_accepts_funding_fade_signal_fn():
    """run_portfolio 가 펀딩 페이드 signal_fn 을 받아 실행한다(통합)."""
    n = 120
    fr = np.full(n, 0.0001)
    fr[60] = 0.02
    df = _ranging_df(n, fr)
    data = {"SOLUSDT": df}
    port = pbt.PortfolioConfig(atr_stop_mult=2.0, atr_target_mult=2.0, time_stop_bars=10)
    r = pbt.run_portfolio(
        data, tiers={"SOLUSDT": 2}, port_cfg=port,
        signal_fn=lambda d: compute_funding_fade_signals(d, _CFG),
    )
    assert r.total_trades >= 0          # 실행 자체가 예외 없이 완료
    assert isinstance(r.equity_curve, list)
