"""
analytics/regime_detector_v2.py
=====================================================================
시장 레짐 분류기 v2 — 6-state one-hot (ML EV Engine R0 feature).

기존 strategy/regime_detector.py 는 5 regime (TREND_UP/TREND_DOWN/RANGING/
HIGH_VOL/UNCERTAIN) 의 *상태 변화 알림용* 모듈. 본 v2 는 *ML feature 용*
시점별 6-state one-hot 으로 더 단순·robust 한 분류.

6 states (one-hot, 시점 t 의 직전 데이터만 사용):
  - TREND      : BTC 4h ADX > 25 AND 직전 24h |return| > 1% (방향 무관 추세)
  - RANGE      : BTC 4h ADX < 20 AND ATR%(1h) < 0.5%
  - HIGH_VOL   : 30-bar realized vol percentile > 0.9 (트레일링)
  - LOW_VOL    : 30-bar realized vol percentile < 0.1
  - PANIC      : BTC 1h drop ≤ −1.5% AND 24h drop ≤ −5%
  - RECOVERY   : 직전 48h 내 PANIC 발생 + 그 후 BTC 4h 회복 ≥ +1%

설계:
  - 순수 함수, OHLCV DataFrame 입력. I/O 없음.
  - 룩어헤드 차단: 시점 t 의 상태는 t 봉까지의 데이터만.
  - 한 봉이 여러 state 동시 만족 가능(예: PANIC + HIGH_VOL). one-hot 이지만
    *복수 활성* 허용 → ML feature 로 6-dim 부울 벡터.
  - REUSE: strategy/breakout.py 의 ema/adx 함수.
=====================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd


# regime 상수
REGIME_TREND = "TREND"
REGIME_RANGE = "RANGE"
REGIME_HIGH_VOL = "HIGH_VOL"
REGIME_LOW_VOL = "LOW_VOL"
REGIME_PANIC = "PANIC"
REGIME_RECOVERY = "RECOVERY"

ALL_REGIMES = (REGIME_TREND, REGIME_RANGE, REGIME_HIGH_VOL,
                REGIME_LOW_VOL, REGIME_PANIC, REGIME_RECOVERY)


@dataclass
class RegimeDetectorConfig:
    # ADX (BTC 4h 봉, breakout.adx 재사용)
    adx_period: int = 14
    adx_trend_threshold: float = 25.0
    adx_range_threshold: float = 20.0
    # ATR % (BTC 1h)
    atr_period: int = 14
    atr_range_threshold: float = 0.005
    # 24h return
    trend_24h_return_threshold: float = 0.01
    # vol percentile (트레일링 윈도)
    vol_window: int = 30
    vol_percentile_window: int = 720    # 30일 1h
    vol_high_pct: float = 0.90
    vol_low_pct: float = 0.10
    # Panic
    panic_1h_drop: float = -0.015
    panic_24h_drop: float = -0.05
    # Recovery
    recovery_lookback_hours: int = 48
    recovery_btc_4h_return: float = 0.01


# ── ADX (간소화 자체 구현 — 외부 의존 회피) ───────────────────────
def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Average Directional Index. 첫 2×period 봉은 NaN."""
    n = len(high)
    if n < 2 * period + 1:
        return np.full(n, np.nan)
    up = high[1:] - high[:-1]
    dn = low[:-1] - low[1:]
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr1 = high[1:] - low[1:]
    tr2 = np.abs(high[1:] - close[:-1])
    tr3 = np.abs(low[1:] - close[:-1])
    tr = np.maximum(np.maximum(tr1, tr2), tr3)
    # Wilder smoothing
    def _wilder(arr, p):
        out = np.full_like(arr, np.nan, dtype=float)
        if len(arr) < p:
            return out
        out[p-1] = arr[:p].sum()
        for i in range(p, len(arr)):
            out[i] = out[i-1] - out[i-1]/p + arr[i]
        return out
    smooth_tr = _wilder(tr, period)
    smooth_plus = _wilder(plus_dm, period)
    smooth_minus = _wilder(minus_dm, period)
    plus_di = 100 * smooth_plus / np.where(smooth_tr == 0, np.nan, smooth_tr)
    minus_di = 100 * smooth_minus / np.where(smooth_tr == 0, np.nan, smooth_tr)
    dx = 100 * np.abs(plus_di - minus_di) / np.where((plus_di + minus_di) == 0, np.nan,
                                                       plus_di + minus_di)
    adx_out = np.full(n, np.nan)
    if len(dx) < 2 * period:
        return adx_out
    # ADX = Wilder smoothing of DX
    start = 2 * period - 1
    adx_out[start + 1] = np.nanmean(dx[period-1:start+1])
    for i in range(start + 2, n):
        prev = adx_out[i-1]
        cur = dx[i-1]
        if np.isnan(prev) or np.isnan(cur):
            continue
        adx_out[i] = (prev * (period - 1) + cur) / period
    return adx_out


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(high)
    if n < period + 1:
        return np.full(n, np.nan)
    tr1 = high[1:] - low[1:]
    tr2 = np.abs(high[1:] - close[:-1])
    tr3 = np.abs(low[1:] - close[:-1])
    tr = np.maximum(np.maximum(tr1, tr2), tr3)
    atr = np.full(n, np.nan)
    atr[period] = tr[:period].mean()
    for i in range(period + 1, n):
        atr[i] = (atr[i-1] * (period - 1) + tr[i-1]) / period
    return atr


def _resample_4h_close(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """1h ohlcv → 4h ohlc (label='right', closed='left' → 룩어헤드 안전)."""
    o = ohlcv["open"].resample("4h", label="right", closed="left").first()
    h = ohlcv["high"].resample("4h", label="right", closed="left").max()
    l = ohlcv["low"].resample("4h", label="right", closed="left").min()
    c = ohlcv["close"].resample("4h", label="right", closed="left").last()
    return pd.concat([o, h, l, c], axis=1).dropna()


# ── 메인 ──────────────────────────────────────────────────────────
def detect_regimes(
    btc_ohlcv_1h: pd.DataFrame,
    cfg: Optional[RegimeDetectorConfig] = None,
) -> pd.DataFrame:
    """BTC 1h ohlcv → 시점별 6-state regime one-hot DataFrame.

    Args:
        btc_ohlcv_1h: index=ts(UTC tz-aware), cols [open, high, low, close, volume].
        cfg: 임계.

    Returns:
        DataFrame index=ts(BTC 1h 그대로), cols=ALL_REGIMES (bool/int 0~1).
        한 봉이 여러 regime 동시 만족 가능. NaN 봉(데이터 부족)은 모두 0.
    """
    cfg = cfg or RegimeDetectorConfig()
    if btc_ohlcv_1h.empty:
        return pd.DataFrame(columns=list(ALL_REGIMES))

    out = pd.DataFrame(False, index=btc_ohlcv_1h.index, columns=list(ALL_REGIMES))

    # ── 1h based: PANIC, HIGH_VOL, LOW_VOL ───────────────────────
    close = btc_ohlcv_1h["close"].astype(float)
    ret_1h = close.pct_change()
    ret_24h = close.pct_change(24)
    realized_vol = ret_1h.rolling(cfg.vol_window).std()
    vol_pct = realized_vol.rolling(cfg.vol_percentile_window, min_periods=cfg.vol_window).rank(pct=True)
    high = btc_ohlcv_1h["high"].astype(float).to_numpy()
    low = btc_ohlcv_1h["low"].astype(float).to_numpy()
    close_arr = close.to_numpy()
    atr = _atr(high, low, close_arr, cfg.atr_period)
    atr_pct = pd.Series(atr / close_arr, index=close.index)

    out[REGIME_PANIC] = (ret_1h <= cfg.panic_1h_drop) & (ret_24h <= cfg.panic_24h_drop)
    out[REGIME_HIGH_VOL] = vol_pct > cfg.vol_high_pct
    out[REGIME_LOW_VOL] = vol_pct < cfg.vol_low_pct

    # ── 4h based: TREND, RANGE (BTC 4h ADX) ──────────────────────
    btc_4h = _resample_4h_close(btc_ohlcv_1h)
    adx_4h = _adx(
        btc_4h["high"].to_numpy(),
        btc_4h["low"].to_numpy(),
        btc_4h["close"].to_numpy(),
        cfg.adx_period,
    )
    adx_4h_series = pd.Series(adx_4h, index=btc_4h.index)
    # 1h index 로 forward-fill (마지막 4h adx 값을 다음 4h 동안 사용)
    adx_1h = adx_4h_series.reindex(btc_ohlcv_1h.index, method="ffill")

    trend_mask = (adx_1h > cfg.adx_trend_threshold) & (ret_24h.abs() > cfg.trend_24h_return_threshold)
    range_mask = (adx_1h < cfg.adx_range_threshold) & (atr_pct < cfg.atr_range_threshold)
    out[REGIME_TREND] = trend_mask.fillna(False)
    out[REGIME_RANGE] = range_mask.fillna(False)

    # ── RECOVERY: 직전 48h 내 PANIC 발생 + 그 후 4h return ≥ +1% ──
    panic_in_window = out[REGIME_PANIC].rolling(cfg.recovery_lookback_hours).max().fillna(0).astype(bool)
    ret_4h = close.pct_change(4)
    recovery_mask = panic_in_window & (ret_4h >= cfg.recovery_btc_4h_return)
    out[REGIME_RECOVERY] = recovery_mask.fillna(False)

    # NaN 봉은 False 처리(이미 fillna(False)로 처리됨)
    return out.fillna(False).astype(int)


def regime_at(
    regimes_df: pd.DataFrame, ts: pd.Timestamp
) -> Dict[str, int]:
    """단일 시점의 regime one-hot dict.

    Args:
        regimes_df: detect_regimes 결과.
        ts: 조회 시각.

    Returns:
        {regime_name: 0/1} dict. ts 가 없으면 모두 0.
    """
    if ts not in regimes_df.index:
        return {r: 0 for r in ALL_REGIMES}
    row = regimes_df.loc[ts]
    return {r: int(row[r]) for r in ALL_REGIMES}
