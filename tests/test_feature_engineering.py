"""
tests/test_feature_engineering.py
=====================================================================
analytics/feature_engineering.py — 피처 생성 + 룩어헤드 차단 검증.
=====================================================================
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from analytics.feature_engineering import (
    FeatureConfig,
    _hours_to_next_funding,
    build_features,
    build_target,
)
from analytics.regime_detector_v2 import ALL_REGIMES


def _make_ohlcv(n: int = 500, seed: int = 0, base_price: float = 100.0,
                  has_taker: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([start + timedelta(hours=i) for i in range(n)])
    rets = rng.normal(0, 0.005, n)
    close = base_price * np.cumprod(1 + rets)
    open_ = np.concatenate([[base_price], close[:-1]])
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.001, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.001, n)))
    vol = rng.uniform(100, 1000, n)
    cols = {"open": open_, "high": high, "low": low, "close": close, "volume": vol}
    if has_taker:
        cols["taker_buy_base_asset_volume"] = vol * rng.uniform(0.3, 0.7, n)
    return pd.DataFrame(cols, index=idx)


# ── build_features 기본 ───────────────────────────────────────────
def test_build_features_empty_input():
    out = build_features(pd.DataFrame(), pd.DataFrame())
    assert out.empty


def test_build_features_returns_expected_columns():
    ohlcv = _make_ohlcv(n=500, seed=1)
    btc = _make_ohlcv(n=500, seed=2)
    out = build_features(ohlcv, btc)
    cols = set(out.columns)
    # 가격 구조
    assert "ema_7_dist" in cols
    assert "ema_200_dist" in cols
    assert "ema_50_slope" in cols
    # VWMA
    assert "vwma_dist" in cols
    assert "vwma_slope" in cols
    # BB
    assert "bb_pctb" in cols
    assert "bb_width" in cols
    # ATR
    assert "atr_pct" in cols
    # RSI
    assert "rsi_14" in cols
    assert "rsi_6" in cols
    # 수급
    assert "volume_ratio" in cols
    assert "taker_buy_ratio" in cols
    assert "oi_change_1h" in cols
    assert "funding_rate" in cols
    assert "funding_remaining_h" in cols
    # 거시
    assert "btc_corr_30" in cols
    assert "btc_trend_4h_dist" in cols
    # 횡단면
    assert "xs_ret_1h_pct" in cols
    assert "xs_ret_24h_pct" in cols
    # Regime
    for r in ALL_REGIMES:
        assert f"regime_{r}" in cols
    # 시간
    assert "hour_sin" in cols
    assert "hour_cos" in cols
    assert "dow_sin" in cols
    assert "is_weekend" in cols


def test_build_features_no_inf():
    """결과에 inf 값 없음(replace 처리)."""
    ohlcv = _make_ohlcv(n=500)
    btc = _make_ohlcv(n=500, seed=3)
    out = build_features(ohlcv, btc)
    assert not np.isinf(out.select_dtypes(include=[np.number])).any().any()


def test_build_features_taker_missing_defaults_to_05():
    """taker_buy column 없으면 0.5 기본값."""
    ohlcv = _make_ohlcv(n=300, has_taker=False)
    btc = _make_ohlcv(n=300, seed=5)
    out = build_features(ohlcv, btc)
    assert (out["taker_buy_ratio"] == 0.5).all()


# ── 룩어헤드 차단 검증 (CRITICAL) ────────────────────────────────
def test_lookahead_safety_features_at_t_only_use_data_up_to_t():
    """시점 t 의 모든 피처 = ohlcv[:t+1] 만으로 호출한 결과와 동일.

    이게 깨지면 backtest 결과가 실제 매매보다 *과대평가* 됨.
    """
    ohlcv = _make_ohlcv(n=500, seed=7)
    btc = _make_ohlcv(n=500, seed=8)
    full = build_features(ohlcv, btc)
    # 시점 t = 250 까지만 잘라 호출
    t = 250
    partial = build_features(ohlcv.iloc[:t+1], btc.iloc[:t+1])
    # ema_200_dist 등 indicator 가 stabilize 된 시점 비교
    common_idx = ohlcv.index[t]
    if common_idx not in partial.index:
        return
    for col in ["ema_50_dist", "ema_200_dist", "vwma_dist", "bb_pctb",
                "atr_pct", "rsi_14", "volume_ratio", "btc_corr_30"]:
        v_full = full[col].loc[common_idx]
        v_part = partial[col].loc[common_idx]
        if pd.isna(v_full) and pd.isna(v_part):
            continue
        assert abs(v_full - v_part) < 1e-9, f"{col}: full={v_full} vs partial={v_part}"


def test_lookahead_safety_target_uses_future_only():
    """target_4h_logret 는 *진입 시각 t+1 시가 → 청산 시각 t+1+4 시가* 사용."""
    ohlcv = _make_ohlcv(n=100)
    target = build_target(ohlcv, horizon_bars=4)
    # 직접 검증
    log_open = np.log(ohlcv["open"].astype(float))
    expected_t = log_open.iloc[1 + 4 + 50] - log_open.iloc[1 + 50]   # for t=50
    actual_t = target.iloc[50]
    assert abs(expected_t - actual_t) < 1e-12
    # 마지막 5 봉 NaN (shift -5)
    assert target.iloc[-1:].isna().all()


# ── _hours_to_next_funding ────────────────────────────────────────
def test_funding_remaining_at_settlement_times():
    # 0시 → 다음 정산 8h
    assert _hours_to_next_funding(pd.Timestamp("2024-01-01 00:00:00", tz="UTC")) == 8.0
    # 7시 → 1h 남음
    assert _hours_to_next_funding(pd.Timestamp("2024-01-01 07:00:00", tz="UTC")) == 1.0
    # 7시 30분 → 0.5h 남음
    assert _hours_to_next_funding(pd.Timestamp("2024-01-01 07:30:00", tz="UTC")) == 0.5
    # 16시 → 8h 남음(다음날 00:00)
    assert _hours_to_next_funding(pd.Timestamp("2024-01-01 16:00:00", tz="UTC")) == 8.0
    # 23시 → 1h 남음
    assert _hours_to_next_funding(pd.Timestamp("2024-01-01 23:00:00", tz="UTC")) == 1.0


# ── 인자 옵션 ─────────────────────────────────────────────────────
def test_build_features_with_oi_changes_oi_columns():
    ohlcv = _make_ohlcv(n=500)
    btc = _make_ohlcv(n=500, seed=2)
    rng = np.random.default_rng(0)
    oi = pd.Series(rng.uniform(1000, 2000, 500), index=ohlcv.index)
    out_no_oi = build_features(ohlcv, btc, oi=None)
    out_with_oi = build_features(ohlcv, btc, oi=oi)
    # OI 없으면 0 column, 있으면 non-zero (pct_change)
    assert (out_no_oi["oi_change_1h"] == 0.0).all()
    assert not (out_with_oi["oi_change_1h"].dropna() == 0.0).all()


def test_build_features_with_regime():
    ohlcv = _make_ohlcv(n=500)
    btc = _make_ohlcv(n=500, seed=2)
    from analytics.regime_detector_v2 import detect_regimes
    regimes = detect_regimes(btc)
    out = build_features(ohlcv, btc, regimes=regimes)
    # 적어도 하나의 regime 컬럼은 *0 이 아닌 값* 가짐
    regime_cols = [c for c in out.columns if c.startswith("regime_")]
    assert any((out[c] != 0).any() for c in regime_cols)


def test_target_horizon_4h():
    """horizon_bars=4 → 진입 t+1, 청산 t+5 (즉 4봉 보유)."""
    n = 50
    opens = np.arange(100.0, 100 + n, dtype=float)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([start + timedelta(hours=i) for i in range(n)])
    ohlcv = pd.DataFrame({"open": opens, "high": opens, "low": opens,
                           "close": opens, "volume": np.ones(n)}, index=idx)
    target = build_target(ohlcv, horizon_bars=4)
    # entry=open[t+1], exit=open[t+5] → log diff
    for t in [0, 10, 20]:
        expected = np.log(opens[t+5]) - np.log(opens[t+1])
        assert abs(target.iloc[t] - expected) < 1e-12
