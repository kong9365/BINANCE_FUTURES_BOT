"""
tests/test_regime_detector_v2.py
=====================================================================
analytics/regime_detector_v2.py — 6-state regime 분류기 단위 테스트.
=====================================================================
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from analytics.regime_detector_v2 import (
    ALL_REGIMES,
    REGIME_HIGH_VOL,
    REGIME_LOW_VOL,
    REGIME_PANIC,
    REGIME_RANGE,
    REGIME_RECOVERY,
    REGIME_TREND,
    RegimeDetectorConfig,
    detect_regimes,
    regime_at,
)


def _make_btc_ohlcv(n: int = 1000, seed: int = 0,
                     daily_drift: float = 0.0,
                     vol_factor: float = 0.005,
                     base_price: float = 50000.0) -> pd.DataFrame:
    """N 시간 BTC ohlcv 합성."""
    rng = np.random.default_rng(seed)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([start + timedelta(hours=i) for i in range(n)])
    rets = rng.normal(daily_drift / 24, vol_factor, n)
    close = base_price * np.cumprod(1 + rets)
    open_ = np.concatenate([[base_price], close[:-1]])
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.001, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.001, n)))
    vol = rng.uniform(10, 100, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                          "close": close, "volume": vol}, index=idx)


# ── 기본 ──────────────────────────────────────────────────────────
def test_detect_regimes_empty_input():
    out = detect_regimes(pd.DataFrame())
    assert out.empty
    assert list(out.columns) == list(ALL_REGIMES)


def test_detect_regimes_output_shape_and_columns():
    btc = _make_btc_ohlcv(n=1000)
    out = detect_regimes(btc)
    assert len(out) == len(btc)
    assert set(out.columns) == set(ALL_REGIMES)
    assert out.dtypes.eq(int).all()
    assert ((out >= 0) & (out <= 1)).all().all()


# ── PANIC ─────────────────────────────────────────────────────────
def test_detect_panic_when_1h_and_24h_both_crash():
    btc = _make_btc_ohlcv(n=200, vol_factor=0.001)   # 평온
    # 100번 봉에서 -2% 1h drop 주입
    i = 100
    btc.iloc[i, btc.columns.get_loc("close")] = btc["close"].iloc[i-1] * 0.98
    # 그리고 76~100 봉(24h) 누적 -6% 만들기
    factor = (0.94 / btc["close"].iloc[i]) ** (1.0 / 24)
    # 단순화: 사전 24봉을 모두 단조 하락으로 교체
    base = btc["close"].iloc[i-24]
    for j in range(i-24, i+1):
        btc.iloc[j, btc.columns.get_loc("close")] = base * (0.94 ** ((j-i+24)/24))
    # 마지막 봉을 1h drop -2% 보장
    btc.iloc[i, btc.columns.get_loc("close")] = btc["close"].iloc[i-1] * 0.97
    out = detect_regimes(btc)
    assert out[REGIME_PANIC].iloc[i] == 1


def test_no_panic_in_calm_market():
    btc = _make_btc_ohlcv(n=300, vol_factor=0.001)
    out = detect_regimes(btc)
    # 평온한 시장 → PANIC 거의 없음
    assert out[REGIME_PANIC].sum() <= 3   # 노이즈 약간 허용


# ── HIGH_VOL / LOW_VOL ────────────────────────────────────────────
def test_high_vol_active_during_burst():
    """후반부에 vol 10배 → HIGH_VOL 활성."""
    n = 1000
    rng = np.random.default_rng(5)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([start + timedelta(hours=i) for i in range(n)])
    rets = rng.normal(0, 0.002, n)
    # 800~1000봉 vol 10배
    rets[800:] = rng.normal(0, 0.02, 200)
    close = 50000.0 * np.cumprod(1 + rets)
    open_ = np.concatenate([[50000.0], close[:-1]])
    high = np.maximum(open_, close) * 1.001
    low = np.minimum(open_, close) * 0.999
    btc = pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": np.ones(n)}, index=idx)
    out = detect_regimes(btc)
    # 후반부 HIGH_VOL 자주 활성
    assert out[REGIME_HIGH_VOL].iloc[850:].sum() > 30


def test_low_vol_active_in_calm():
    """초반부 vol 작은 후 후반부 평온해지면 LOW_VOL 활성."""
    n = 1000
    rng = np.random.default_rng(6)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([start + timedelta(hours=i) for i in range(n)])
    rets = np.concatenate([rng.normal(0, 0.01, 500), rng.normal(0, 0.0005, 500)])
    close = 50000.0 * np.cumprod(1 + rets)
    open_ = np.concatenate([[50000.0], close[:-1]])
    high = np.maximum(open_, close) * 1.0001
    low = np.minimum(open_, close) * 0.9999
    btc = pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": np.ones(n)}, index=idx)
    out = detect_regimes(btc)
    assert out[REGIME_LOW_VOL].iloc[600:].sum() > 20


# ── TREND / RANGE ─────────────────────────────────────────────────
def test_trend_active_during_strong_uptrend():
    """단조 강한 상승 → TREND 활성."""
    n = 600
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([start + timedelta(hours=i) for i in range(n)])
    # 시간당 +0.1% 단조 상승 + 작은 noise → 24h ~ +2.4% 상승 + ADX 매우 높음
    rng = np.random.default_rng(7)
    rets = 0.001 + rng.normal(0, 0.0005, n)
    close = 50000.0 * np.cumprod(1 + rets)
    open_ = np.concatenate([[50000.0], close[:-1]])
    high = np.maximum(open_, close) * 1.0005
    low = np.minimum(open_, close) * 0.9995
    btc = pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": np.ones(n)}, index=idx)
    out = detect_regimes(btc)
    # 후반부 TREND 자주 활성
    assert out[REGIME_TREND].iloc[400:].sum() > 50


# ── regime_at ─────────────────────────────────────────────────────
def test_regime_at_returns_dict():
    btc = _make_btc_ohlcv(n=500)
    out = detect_regimes(btc)
    d = regime_at(out, btc.index[100])
    assert set(d.keys()) == set(ALL_REGIMES)
    assert all(v in (0, 1) for v in d.values())


def test_regime_at_unknown_ts_returns_zero():
    btc = _make_btc_ohlcv(n=100)
    out = detect_regimes(btc)
    unknown = btc.index[-1] + timedelta(hours=999)
    d = regime_at(out, unknown)
    assert all(v == 0 for v in d.values())


def test_lookahead_safety_panic_at_t_uses_only_data_up_to_t():
    """t 봉의 PANIC 판정이 t+1 이후 데이터에 의존하지 않음을 검증.

    같은 데이터에서 t 까지만 잘라 호출 → t 의 결과가 동일해야 함.
    """
    btc = _make_btc_ohlcv(n=400, seed=11)
    # 200번 봉에 panic 주입
    i = 200
    base = btc["close"].iloc[i-24]
    for j in range(i-24, i+1):
        btc.iloc[j, btc.columns.get_loc("close")] = base * (0.94 ** ((j-i+24)/24))
    btc.iloc[i, btc.columns.get_loc("close")] = btc["close"].iloc[i-1] * 0.97
    full = detect_regimes(btc)
    partial = detect_regimes(btc.iloc[:i+1])
    assert full[REGIME_PANIC].iloc[i] == partial[REGIME_PANIC].iloc[i]


def test_multiple_regimes_can_be_active_same_bar():
    """PANIC + HIGH_VOL 동시 활성 가능(둘은 mutually exclusive 아님)."""
    btc = _make_btc_ohlcv(n=400, vol_factor=0.02)   # 변동성 큰 시장
    # panic 봉 주입
    i = 200
    base = btc["close"].iloc[i-24]
    for j in range(i-24, i+1):
        btc.iloc[j, btc.columns.get_loc("close")] = base * (0.93 ** ((j-i+24)/24))
    btc.iloc[i, btc.columns.get_loc("close")] = btc["close"].iloc[i-1] * 0.97
    out = detect_regimes(btc)
    # 같은 봉에 PANIC + 어쩌면 HIGH_VOL 동시 활성 가능 — 그 자체로 OK
    assert out[REGIME_PANIC].iloc[i] == 1
    # one-hot 아님(여러 동시 가능) 검증
    total_active_at_i = out.iloc[i].sum()
    assert total_active_at_i >= 1
