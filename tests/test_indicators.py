"""
tests/test_indicators.py
=====================================================================
strategy/indicators.py — VWMA, Bollinger Bands, RSI 단위 테스트.
=====================================================================
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from strategy.indicators import bollinger_bands, rsi, vwma


def _ts_series(values, name="close"):
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([start + timedelta(hours=i) for i in range(len(values))])
    return pd.Series(values, index=idx, name=name)


# ── VWMA ──────────────────────────────────────────────────────────
def test_vwma_uniform_volume_equals_sma():
    """volume 동일하면 VWMA = SMA(close, period)."""
    close = _ts_series(np.arange(1, 21, dtype=float))
    vol = _ts_series([1.0] * 20)
    v = vwma(close, vol, period=5)
    sma = close.rolling(5).mean()
    pd.testing.assert_series_equal(
        v.dropna().reset_index(drop=True),
        sma.dropna().reset_index(drop=True),
        check_names=False,
    )


def test_vwma_high_volume_at_one_bar_dominates():
    """한 봉에 volume 100배 → VWMA 가 그 봉 price 쪽으로 크게 이동."""
    close = _ts_series([10.0, 10.0, 10.0, 10.0, 100.0])
    vol = _ts_series([1.0, 1.0, 1.0, 1.0, 1000.0])
    v = vwma(close, vol, period=5)
    # 마지막 VWMA = (10×1+10×1+10×1+10×1+100×1000)/(1+1+1+1+1000) = 100040/1004 ≈ 99.64
    assert abs(v.iloc[-1] - 99.64) < 1.0


def test_vwma_zero_volume_doesnt_crash():
    close = _ts_series([10.0] * 5)
    vol = _ts_series([0.0] * 5)
    v = vwma(close, vol, period=5)
    # 모든 vol 0 → sum=0 → NaN
    assert pd.isna(v.iloc[-1])


def test_vwma_first_period_minus_one_bars_nan():
    close = _ts_series([10.0] * 10)
    vol = _ts_series([1.0] * 10)
    v = vwma(close, vol, period=5)
    assert v.iloc[:4].isna().all()
    assert not pd.isna(v.iloc[4])


def test_vwma_length_mismatch_raises():
    with pytest.raises(ValueError):
        vwma(_ts_series([1.0, 2.0]), _ts_series([1.0, 2.0, 3.0]), period=2)


def test_vwma_invalid_period_raises():
    with pytest.raises(ValueError):
        vwma(_ts_series([1.0, 2.0]), _ts_series([1.0, 2.0]), period=0)


# ── Bollinger Bands ───────────────────────────────────────────────
def test_bollinger_basic_structure():
    close = _ts_series(np.arange(1, 51, dtype=float))
    mid, upper, lower, pctb, bw = bollinger_bands(close, period=20, num_std=2.0)
    # mid 는 SMA(20)
    assert abs(mid.iloc[19] - 10.5) < 0.001  # mean(1..20) = 10.5
    # upper > mid > lower
    assert (upper > mid).iloc[20:].all()
    assert (mid > lower).iloc[20:].all()
    # bandwidth > 0
    assert (bw > 0).iloc[20:].all()


def test_bollinger_pctb_high_when_price_near_upper():
    """close 가 upper band 에 가까우면 %B → 1 근접."""
    rng = np.random.default_rng(0)
    base = np.cumsum(rng.normal(0, 0.5, 100)) + 100.0
    # 마지막 봉만 강하게 위로
    base[-1] = base[-2] + 20.0
    close = _ts_series(base)
    _, _, _, pctb, _ = bollinger_bands(close, period=20)
    assert pctb.iloc[-1] > 0.9


def test_bollinger_pctb_low_when_price_near_lower():
    rng = np.random.default_rng(0)
    base = np.cumsum(rng.normal(0, 0.5, 100)) + 100.0
    base[-1] = base[-2] - 20.0
    close = _ts_series(base)
    _, _, _, pctb, _ = bollinger_bands(close, period=20)
    assert pctb.iloc[-1] < 0.1


def test_bollinger_constant_price_zero_std():
    close = _ts_series([10.0] * 30)
    mid, upper, lower, pctb, bw = bollinger_bands(close, period=20)
    # std=0 → upper=lower=mid → bandwidth NaN(/0), pctb NaN
    assert pd.isna(bw.iloc[-1]) or bw.iloc[-1] == 0.0
    assert pd.isna(pctb.iloc[-1])


def test_bollinger_invalid_params_raise():
    close = _ts_series([10.0] * 30)
    with pytest.raises(ValueError):
        bollinger_bands(close, period=0)
    with pytest.raises(ValueError):
        bollinger_bands(close, period=20, num_std=-1.0)


# ── RSI ───────────────────────────────────────────────────────────
def test_rsi_constant_price_returns_50():
    """변동 없으면 RSI = 50 (gain=loss=0 처리)."""
    close = _ts_series([10.0] * 30)
    r = rsi(close, period=14)
    # 첫 14 봉 NaN, 그 이후 50
    assert r.iloc[14:].between(49.9, 50.1).all() or r.iloc[14:].isna().all()


def test_rsi_strictly_increasing_returns_high():
    """단조 증가 → RSI 가 100 근접."""
    close = _ts_series(np.arange(1, 51, dtype=float))
    r = rsi(close, period=14)
    # 안정화된 후반부 RSI 가 매우 높음
    assert r.iloc[-1] > 90


def test_rsi_strictly_decreasing_returns_low():
    close = _ts_series(np.arange(50, 0, -1, dtype=float))
    r = rsi(close, period=14)
    assert r.iloc[-1] < 10


def test_rsi_oscillating_around_50():
    """sin oscillation → RSI 가 50 중심 진동."""
    n = 200
    base = 100.0 + 10.0 * np.sin(np.arange(n) * 2 * np.pi / 50)
    close = _ts_series(base)
    r = rsi(close, period=14).dropna()
    assert 30 < r.mean() < 70   # 평균이 중앙 근처


def test_rsi_first_period_bars_nan():
    close = _ts_series(np.arange(1, 30, dtype=float))
    r = rsi(close, period=14)
    # 처음 14 정도 NaN (Wilder ewm + min_periods=period)
    assert r.iloc[:14].isna().sum() >= 13


def test_rsi_invalid_period_raises():
    close = _ts_series([1.0, 2.0, 3.0])
    with pytest.raises(ValueError):
        rsi(close, period=0)


def test_rsi_returns_in_valid_range():
    """RSI 는 [0, 100] 범위(NaN 제외)."""
    rng = np.random.default_rng(42)
    close = _ts_series(np.cumsum(rng.normal(0, 1, 500)) + 100)
    r = rsi(close, period=14).dropna()
    assert (r >= 0).all() and (r <= 100).all()
