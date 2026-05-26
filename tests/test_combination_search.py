"""조합 탐색 단위 테스트."""
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.verification.combination_search import (
    btc_regime_series,
    build_composite_frames,
    backtest_mom_crash_bounce,
)


def _mini_universe() -> dict:
    idx = pd.date_range("2023-01-01", periods=250, freq="D", tz="UTC")
    rng = np.random.default_rng(0)
    out = {}
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]:
        ret = rng.normal(0.001, 0.02, len(idx))
        close = 100 * np.cumprod(1 + ret)
        out[sym] = pd.DataFrame(
            {"open": close, "high": close * 1.01, "low": close * 0.99,
             "close": close, "volume": np.full(len(idx), 1e6)},
            index=idx,
        )
    return out


def test_btc_regime_series():
    data = _mini_universe()
    reg = btc_regime_series(data["BTCUSDT"])
    assert len(reg) == 250


def test_build_composite_frames():
    data = _mini_universe()
    frames = build_composite_frames(data)
    assert len(frames) >= 2
    assert "composite_mom_breakout" in frames[0].columns


def test_crash_bounce_runs():
    data = _mini_universe()
    r = backtest_mom_crash_bounce(data, crash_pct=-0.05, hold_bars=5)
    assert r.n_trades >= 0
