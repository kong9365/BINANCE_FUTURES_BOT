"""
tests/test_lcr_event_labeler.py
=====================================================================
analytics/lcr_event_labeler.py — LCR 셋업 프록시 라벨링 단위 테스트.
=====================================================================
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.lcr_event_labeler import (
    LCRLabelConfig, label_events, compute_forward_returns,
    summarize, per_symbol_decomposition, concentration_share,
)


_CFG = LCRLabelConfig(vol_avg_lookback=10, vol_ratio_threshold=3.0,
                      price_drop_threshold=-0.02, price_rise_threshold=0.02,
                      oi_drop_threshold=-0.03,
                      btc_coincident_threshold=-0.012)


def _df(n=30, idx_start="2026-01-01"):
    idx = pd.date_range(idx_start, periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
        "close": [100.0] * n, "volume": [1000.0] * n,
    }, index=idx)


def test_no_data_returns_empty():
    out = label_events(_df(n=5), oi=None, btc_returns=None, cfg=_CFG)
    assert out.empty


def test_setup_a_detected_on_flush():
    df = _df(n=30)
    # 봉 20: OI -5%, vol 5x, price -3% → 셋업 A
    df.loc[df.index[20], "close"] = 100.0 * 0.97       # -3% from prev close 100
    df.loc[df.index[20], "volume"] = 5000.0            # 5x baseline 1000
    oi = pd.Series([1000.0] * 30, index=df.index)
    oi.iloc[20] = 950.0                                 # -5%
    events = label_events(df, oi, btc_returns=None, cfg=_CFG)
    assert len(events) == 1
    assert events["setup"].iloc[0] == "A"


def test_setup_b_detected_on_squeeze():
    df = _df(n=30)
    df.loc[df.index[20], "close"] = 100.0 * 1.03       # +3%
    df.loc[df.index[20], "volume"] = 5000.0
    oi = pd.Series([1000.0] * 30, index=df.index)
    oi.iloc[20] = 950.0                                 # -5%
    events = label_events(df, oi, btc_returns=None, cfg=_CFG)
    assert len(events) == 1
    assert events["setup"].iloc[0] == "B"


def test_no_event_without_vol_spike():
    df = _df(n=30)
    df.loc[df.index[20], "close"] = 100.0 * 0.97       # 가격 충족
    # vol 평소대로(스파이크 없음)
    oi = pd.Series([1000.0] * 30, index=df.index)
    oi.iloc[20] = 950.0
    events = label_events(df, oi, btc_returns=None, cfg=_CFG)
    assert events.empty


def test_btc_coincident_flag():
    df = _df(n=30)
    df.loc[df.index[20], "close"] = 100.0 * 0.97
    df.loc[df.index[20], "volume"] = 5000.0
    oi = pd.Series([1000.0] * 30, index=df.index)
    oi.iloc[20] = 950.0
    btc = pd.Series([0.0] * 30, index=df.index)
    btc.iloc[20] = -0.02     # BTC -2% (< -1.2%)
    events = label_events(df, oi, btc, cfg=_CFG)
    assert events["btc_coincident"].iloc[0] == True


def test_forward_return_uses_next_bar_open_lookahead_safe():
    df = _df(n=30)
    df.loc[df.index[20], "close"] = 100.0 * 0.97
    df.loc[df.index[20], "volume"] = 5000.0
    # 봉 21 시가=100(진입), h=1 → 봉 21 종가=110(exit) → fwd_1h = 10%
    df.loc[df.index[21], "open"] = 100.0
    df.loc[df.index[21], "close"] = 110.0
    oi = pd.Series([1000.0] * 30, index=df.index)
    oi.iloc[20] = 950.0
    events = label_events(df, oi, None, cfg=_CFG)
    fwd = compute_forward_returns(events, df, horizons_bars=(1,))
    assert abs(fwd["fwd_1h"].iloc[0] - 0.10) < 1e-9


def test_summary_and_decomp():
    rows = pd.DataFrame({
        "symbol": ["A","A","B","B","C"],
        "fwd_1h": [0.01, -0.005, 0.02, 0.03, -0.04],
    })
    s = summarize(rows, "fwd_1h")
    assert s["n"] == 5
    assert abs(s["mean"] - rows["fwd_1h"].mean()) < 1e-9
    decomp = per_symbol_decomposition(rows, "fwd_1h")
    assert set(decomp) == {"A","B","C"}
    assert decomp["B"]["n"] == 2


def test_concentration_share():
    rows = pd.DataFrame({
        "symbol": ["A","A","B","B"],
        "fwd_1h": [0.10, 0.05, 0.005, 0.001],
    })
    # A 합계 0.15, B 합계 0.006 → 절댓값 합 0.156, max=0.15 → 0.962
    s = concentration_share(rows, "fwd_1h")
    assert 0.90 < s < 1.0
