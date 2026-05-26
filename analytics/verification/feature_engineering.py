"""후보별 피처 + forward return (룩어헤드 차단)."""
from __future__ import annotations

from datetime import timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from strategy.breakout import adx as wilder_adx, atr as wilder_atr, ema as wilder_ema_last


def _ema_series(close: pd.Series, period: int) -> pd.Series:
    out = pd.Series(np.nan, index=close.index, dtype=float)
    vals = close.to_numpy(float)
    for i in range(len(vals)):
        v = wilder_ema_last(vals[: i + 1], period)
        if v is not None:
            out.iloc[i] = v
    return out


def _atr_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    out = pd.Series(np.nan, index=close.index, dtype=float)
    h, l, c = high.to_numpy(float), low.to_numpy(float), close.to_numpy(float)
    for i in range(len(c)):
        out.iloc[i] = wilder_atr(h[: i + 1], l[: i + 1], c[: i + 1], period)
    return out


def _adx_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    out = pd.Series(np.nan, index=close.index, dtype=float)
    h, l, c = high.to_numpy(float), low.to_numpy(float), close.to_numpy(float)
    for i in range(len(c)):
        out.iloc[i] = wilder_adx(h[: i + 1], l[: i + 1], c[: i + 1], period)
    return out


def forward_return_open(open_: pd.Series, bars: int) -> pd.Series:
    """t 시점 close까지 피처 → t+1 open 진입 → t+1+bars open 청산 수익률."""
    entry = open_.shift(-1)
    exit_ = open_.shift(-(1 + bars))
    return (exit_ / entry) - 1.0


def features_1d_breakout(df: pd.DataFrame) -> pd.DataFrame:
    """후보 1: 1d 돌파 확장 피처."""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]
    don_hi = high.shift(1).rolling(20, min_periods=20).max()
    atr20 = _atr_series(high, low, close, 14)
    ema200 = _ema_series(close, 200)
    adx14 = _adx_series(high, low, close, 14)
    out = pd.DataFrame(index=df.index)
    out["donchian_position"] = (close - don_hi) / atr20.replace(0, np.nan)
    out["ema_distance"] = (close - ema200) / ema200.replace(0, np.nan)
    out["adx_14"] = adx14
    out["volume_ratio"] = vol / vol.rolling(20, min_periods=20).mean().replace(0, np.nan)
    if "open" in df.columns:
        out["fwd_1d"] = forward_return_open(df["open"], 1)
        out["fwd_3d"] = forward_return_open(df["open"], 3)
        out["fwd_7d"] = forward_return_open(df["open"], 7)
    return out


def features_cross_sectional_mom(df: pd.DataFrame) -> pd.DataFrame:
    """후보 2: 횡단면 모멘텀 피처 (단일 심볼 시계열)."""
    close = df["close"]
    out = pd.DataFrame(index=df.index)
    out["momentum_30d"] = close / close.shift(30) - 1.0
    out["momentum_skip_30d"] = close.shift(7) / close.shift(30) - 1.0
    ret1d = close.pct_change()
    out["volatility_30d"] = ret1d.rolling(30, min_periods=30).std()
    out["risk_adjusted_momentum"] = (
        out["momentum_skip_30d"] / out["volatility_30d"].replace(0, np.nan)
    )
    if "open" in df.columns:
        out["fwd_7d"] = forward_return_open(df["open"], 7)
    return out


def panel_cross_sectional(
    data: Dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """심볼×ts 패널 — risk_adjusted_momentum + fwd_7d."""
    rows: List[pd.DataFrame] = []
    for sym, df in data.items():
        f = features_cross_sectional_mom(df)
        chunk = f[["risk_adjusted_momentum", "fwd_7d"]].copy()
        chunk["symbol"] = sym
        rows.append(chunk.reset_index().rename(columns={"ts": "ts"}))
    if not rows:
        return pd.DataFrame()
    panel = pd.concat(rows, ignore_index=True)
    return panel.dropna(subset=["risk_adjusted_momentum", "fwd_7d"])


def features_btc_beta_placeholder(df: pd.DataFrame) -> pd.DataFrame:
    """후보 3: 1h 데이터 필요 — 1d만 있으면 빈 프레임."""
    return pd.DataFrame(index=df.index)


def features_funding_placeholder(df: pd.DataFrame) -> pd.DataFrame:
    """후보 4: 1h funding+OI 필요."""
    return pd.DataFrame(index=df.index)


def features_listing_effect(
    df: pd.DataFrame,
    onboard: pd.Timestamp,
) -> pd.DataFrame:
    """후보 5: 신규 상장 이벤트 피처 (1d, 룩어헤드 차단).

    - days_since_listing: 상장일 0 = onboard 당일
    - return_since_listing: 첫 거래봉 close 대비 누적 수익
    - first_week_return: 상장 후 7일째 close / 첫 close - 1 (7일 미만 NaN)
    - volume_ratio_listing_week: volume / 상장 후 첫 7일 평균 volume
    """
    if df.empty or onboard is None:
        return pd.DataFrame(index=df.index)
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    onboard = onboard.tz_convert("UTC") if onboard.tz else onboard.replace(tzinfo=timezone.utc)
    days = pd.Series((idx - onboard).days, index=idx, dtype=float)
    post = days >= 0
    out = pd.DataFrame(index=idx)
    out["days_since_listing"] = days
    close = df["close"].astype(float)
    vol = df["volume"].astype(float)
    first_close = np.nan
    week_mean_vol = np.nan
    first_week_ret = pd.Series(np.nan, index=idx)
    ret_since = pd.Series(np.nan, index=idx)
    vol_ratio = pd.Series(np.nan, index=idx)
    if post.any():
        post_idx = idx[post]
        first_close = float(close.loc[post_idx[0]])
        if first_close > 0:
            ret_since = close / first_close - 1.0
        w7 = post_idx[: min(7, len(post_idx))]
        if len(w7) > 0:
            week_mean_vol = float(vol.loc[w7].mean())
            if len(w7) >= 7 and first_close > 0:
                fw = float(close.loc[w7[-1]] / first_close - 1.0)
                first_week_ret.loc[days >= 7] = fw
            if week_mean_vol > 0:
                vol_ratio = vol / week_mean_vol
    out["return_since_listing"] = ret_since
    out["first_week_return"] = first_week_ret
    out["volume_ratio_listing_week"] = vol_ratio
    if "open" in df.columns:
        out["fwd_1d"] = forward_return_open(df["open"], 1)
        out["fwd_7d"] = forward_return_open(df["open"], 7)
    # IC 샘플: 상장 후 90일 이내만
    mask = (days >= 1) & (days <= 90)
    return out.loc[mask]

