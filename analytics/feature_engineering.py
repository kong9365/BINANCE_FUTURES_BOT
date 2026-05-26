"""
analytics/feature_engineering.py
=====================================================================
ML EV Engine R0 — state vector X 생성 (35~45 피처).

설계 원칙 (절대):
  - 룩어헤드 차단: 시점 t 의 모든 피처는 t 봉 close 까지의 데이터만 사용.
  - 모든 rolling/ewm 은 shift(0) 기본 (그 봉까지 포함) → target 은 *t+1 진입* 가정으로
    별도 계산하여 leakage 회피.
  - I/O 없음. DataFrame in, DataFrame out.

피처 카테고리:
  1. 가격 구조: EMA 거리·기울기, VWMA-100, BB%B, BB폭
  2. 변동성: ATR%, ATR Δ%
  3. 모멘텀: RSI 14/6
  4. 수급: OI Δ% (sparse 처리), funding 현재·z, funding_remaining, taker_buy_ratio, volume_ratio
  5. 거시: BTC 4h trend, BTC 상관도, dominance Δ%, F&G
  6. 횡단면: universe 내 percentile (수익률·vol·OI·taker)
  7. 시장 레짐: 6-state one-hot
  8. 시간 구조: hour_of_day (sin/cos), day_of_week (sin/cos), is_weekend

REUSE:
  - strategy/breakout.py: ema, atr_series (via portfolio_backtest), adx_series
  - strategy/indicators.py: vwma, bollinger_bands, rsi
  - analytics/regime_detector_v2.py: detect_regimes
=====================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from strategy.indicators import bollinger_bands, rsi, vwma


@dataclass
class FeatureConfig:
    ema_periods: tuple = (7, 15, 50, 100, 200)
    ema_slope_periods: tuple = (7, 50, 200)
    ema_slope_lookback: int = 5
    vwma_period: int = 100
    bb_period: int = 20
    bb_std: float = 2.0
    atr_period: int = 14
    rsi_periods: tuple = (14, 6)
    volume_ratio_lookback: int = 24
    btc_corr_window: int = 30
    funding_z_window: int = 90   # 30일 × 8h = 90 펀딩
    cross_sectional_periods: tuple = (1, 24)  # 1h, 24h return percentile


# ── helper: EMA / ATR (numpy) ─────────────────────────────────────
def _ema_series(values: pd.Series, period: int) -> pd.Series:
    """Pandas ewm EMA. period 봉 미만은 NaN."""
    if period <= 0:
        return pd.Series(np.nan, index=values.index)
    return values.ewm(span=period, min_periods=period, adjust=False).mean()


def _atr_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Wilder ATR."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    # Wilder = ewm with alpha=1/period, adjust=False
    return tr.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()


# ── 메인 ──────────────────────────────────────────────────────────
def build_features(
    ohlcv: pd.DataFrame,
    btc_ohlcv: pd.DataFrame,
    regimes: Optional[pd.DataFrame] = None,
    oi: Optional[pd.Series] = None,
    funding: Optional[pd.DataFrame] = None,
    market_global: Optional[pd.DataFrame] = None,
    cross_sectional_returns: Optional[pd.DataFrame] = None,
    cfg: Optional[FeatureConfig] = None,
) -> pd.DataFrame:
    """단일 심볼의 시점별 피처 벡터 생성.

    Args:
        ohlcv: 대상 심볼 1h ohlcv (UTC, cols [open,high,low,close,volume,
               taker_buy_base_asset_volume(optional)]).
        btc_ohlcv: BTC 1h ohlcv (거시·상관 계산).
        regimes: regime_detector_v2.detect_regimes(btc_ohlcv) 결과. None 이면 regime 컬럼 0.
        oi: open_interest Series (sparse 가능). None 이면 OI 피처 0.
        funding: cols [ts, funding_rate]. None 이면 funding 피처 0.
        market_global: cols [ts, btc_dominance, fear_greed_index]. None 이면 거시 피처 0.
        cross_sectional_returns: DataFrame (cols=symbol, idx=ts) — universe 1h return.
                                 None 이면 cross-sec percentile 0.5 (중립).
        cfg: 임계.

    Returns:
        DataFrame (index=ts UTC, cols=feature 이름). NaN 봉은 마지막 dropna() 호출자 책임.
    """
    cfg = cfg or FeatureConfig()
    if ohlcv.empty:
        return pd.DataFrame()

    feats: Dict[str, pd.Series] = {}
    close = ohlcv["close"].astype(float)
    high = ohlcv["high"].astype(float)
    low = ohlcv["low"].astype(float)
    volume = ohlcv["volume"].astype(float)

    # 1. EMA 거리·기울기
    for p in cfg.ema_periods:
        e = _ema_series(close, p)
        feats[f"ema_{p}_dist"] = (close / e - 1.0).rename(None)
    for p in cfg.ema_slope_periods:
        e = _ema_series(close, p)
        slope = (e - e.shift(cfg.ema_slope_lookback)) / e.shift(cfg.ema_slope_lookback)
        feats[f"ema_{p}_slope"] = slope

    # 2. VWMA-100 거리·기울기
    v = vwma(close, volume, cfg.vwma_period)
    feats["vwma_dist"] = (close / v - 1.0)
    feats["vwma_slope"] = (v - v.shift(cfg.ema_slope_lookback)) / v.shift(cfg.ema_slope_lookback)

    # 3. Bollinger
    _, _, _, pctb, bw = bollinger_bands(close, cfg.bb_period, cfg.bb_std)
    feats["bb_pctb"] = pctb
    feats["bb_width"] = bw

    # 4. ATR%
    atr = _atr_series(high, low, close, cfg.atr_period)
    feats["atr_pct"] = atr / close
    feats["atr_delta"] = atr.pct_change(cfg.ema_slope_lookback)

    # 5. RSI
    for p in cfg.rsi_periods:
        feats[f"rsi_{p}"] = rsi(close, p)

    # 6. Volume ratio
    vol_avg = volume.rolling(cfg.volume_ratio_lookback, min_periods=cfg.volume_ratio_lookback).mean().shift(1)
    feats["volume_ratio"] = volume / vol_avg

    # 7. Taker buy ratio
    if "taker_buy_base_asset_volume" in ohlcv.columns:
        tb = ohlcv["taker_buy_base_asset_volume"].astype(float)
        feats["taker_buy_ratio"] = (tb / volume.replace(0, np.nan)).clip(0, 1)
        feats["taker_buy_ratio_4h"] = feats["taker_buy_ratio"].rolling(4).mean()
    else:
        feats["taker_buy_ratio"] = pd.Series(0.5, index=close.index)
        feats["taker_buy_ratio_4h"] = pd.Series(0.5, index=close.index)

    # 8. OI sparse
    if oi is not None and not oi.empty:
        oi_aligned = oi.reindex(close.index).astype(float)
        feats["oi_change_1h"] = oi_aligned.pct_change(1)
        feats["oi_change_4h"] = oi_aligned.pct_change(4)
    else:
        feats["oi_change_1h"] = pd.Series(0.0, index=close.index)
        feats["oi_change_4h"] = pd.Series(0.0, index=close.index)

    # 9. Funding
    if funding is not None and not funding.empty:
        # funding ts → 가장 가까운 *이전* funding 값 사용 (룩어헤드 차단)
        f_df = funding.copy()
        f_df["ts"] = pd.to_datetime(f_df["ts"], utc=True)
        f_df = f_df.sort_values("ts").set_index("ts")
        # close.index 의 각 시각에 대해 그 이전 funding 가져옴
        fr = f_df["funding_rate"].reindex(close.index, method="ffill")
        feats["funding_rate"] = fr.astype(float)
        # z-30d (직전 90 펀딩의 mean/std)
        mu = fr.rolling(cfg.funding_z_window, min_periods=10).mean()
        sd = fr.rolling(cfg.funding_z_window, min_periods=10).std()
        feats["funding_z"] = (fr - mu) / sd.replace(0, np.nan)
        # 다음 funding 까지 시간 (시간 단위)
        # funding 은 8h 마다 정산 (0, 8, 16h UTC 기준)
        hours_remaining = close.index.map(_hours_to_next_funding)
        feats["funding_remaining_h"] = pd.Series(hours_remaining, index=close.index)
    else:
        feats["funding_rate"] = pd.Series(0.0, index=close.index)
        feats["funding_z"] = pd.Series(0.0, index=close.index)
        feats["funding_remaining_h"] = pd.Series(4.0, index=close.index)  # 중간값

    # 10. 거시 — BTC 4h trend, BTC 30-bar 상관
    btc_close = btc_ohlcv["close"].astype(float)
    btc_ret = btc_close.pct_change()
    btc_aligned = btc_close.reindex(close.index, method="ffill")
    btc_ret_aligned = btc_aligned.pct_change()
    sym_ret = close.pct_change()
    feats["btc_corr_30"] = sym_ret.rolling(cfg.btc_corr_window).corr(btc_ret_aligned)
    # BTC 4h close/EMA200 4h
    btc_4h = btc_close.resample("4h", label="right", closed="left").last().dropna()
    btc_ema200_4h = _ema_series(btc_4h, 200)
    btc_trend_4h = (btc_4h / btc_ema200_4h - 1.0).reindex(close.index, method="ffill")
    feats["btc_trend_4h_dist"] = btc_trend_4h

    # 11. market_global (dominance Δ%, F&G)
    if market_global is not None and not market_global.empty:
        mg = market_global.copy()
        mg["ts"] = pd.to_datetime(mg["ts"], utc=True)
        mg = mg.sort_values("ts").set_index("ts")
        if "btc_dominance" in mg.columns:
            dom = mg["btc_dominance"].astype(float).reindex(close.index, method="ffill")
            feats["btc_dominance_change_24h"] = dom.pct_change(24)
        else:
            feats["btc_dominance_change_24h"] = pd.Series(0.0, index=close.index)
        if "fear_greed_index" in mg.columns:
            fg = mg["fear_greed_index"].astype(float).reindex(close.index, method="ffill")
            feats["fear_greed"] = fg / 100.0   # 정규화
        else:
            feats["fear_greed"] = pd.Series(0.5, index=close.index)
    else:
        feats["btc_dominance_change_24h"] = pd.Series(0.0, index=close.index)
        feats["fear_greed"] = pd.Series(0.5, index=close.index)

    # 12. 횡단면 percentile
    if cross_sectional_returns is not None and not cross_sectional_returns.empty:
        sym_name = ohlcv.attrs.get("symbol", None) or close.name
        for p in cfg.cross_sectional_periods:
            # universe 의 모든 심볼 p-bar return → 동 시점 percentile
            csr = cross_sectional_returns.pct_change(p)
            if sym_name in csr.columns:
                # rank pct on rows (each ts) — pandas Series.rolling X; need row-wise rank
                rank_pct = csr.rank(axis=1, pct=True)
                feats[f"xs_ret_{p}h_pct"] = rank_pct[sym_name].reindex(close.index)
            else:
                feats[f"xs_ret_{p}h_pct"] = pd.Series(0.5, index=close.index)
    else:
        for p in cfg.cross_sectional_periods:
            feats[f"xs_ret_{p}h_pct"] = pd.Series(0.5, index=close.index)

    # 13. Regime one-hot
    if regimes is not None and not regimes.empty:
        regime_aligned = regimes.reindex(close.index, method="ffill").fillna(0).astype(int)
        for col in regime_aligned.columns:
            feats[f"regime_{col}"] = regime_aligned[col]
    else:
        from analytics.regime_detector_v2 import ALL_REGIMES
        for r in ALL_REGIMES:
            feats[f"regime_{r}"] = pd.Series(0, index=close.index)

    # 14. 시간 구조 (cyclic encoding)
    hours = close.index.hour.to_numpy(dtype=float)
    feats["hour_sin"] = pd.Series(np.sin(2 * np.pi * hours / 24.0), index=close.index)
    feats["hour_cos"] = pd.Series(np.cos(2 * np.pi * hours / 24.0), index=close.index)
    days = close.index.dayofweek.to_numpy(dtype=float)
    feats["dow_sin"] = pd.Series(np.sin(2 * np.pi * days / 7.0), index=close.index)
    feats["dow_cos"] = pd.Series(np.cos(2 * np.pi * days / 7.0), index=close.index)
    feats["is_weekend"] = pd.Series((days >= 5).astype(int), index=close.index)

    out = pd.DataFrame(feats, index=close.index)
    # inf → NaN 변환(scaler robust)
    return out.replace([np.inf, -np.inf], np.nan)


def _hours_to_next_funding(ts: pd.Timestamp) -> float:
    """다음 funding 정산(0/8/16h UTC)까지 시간."""
    hour = ts.hour + ts.minute / 60.0 + ts.second / 3600.0
    # 다음 정산 시각: 8h 단위
    next_settle = (int(hour // 8) + 1) * 8
    if next_settle >= 24:
        next_settle = 24   # 다음 날 00:00
    return float(next_settle - hour)


def build_target(
    ohlcv: pd.DataFrame, horizon_bars: int = 4,
) -> pd.Series:
    """forward N-bar log return target.

    target_t = log(open_{t+1+horizon}) - log(open_{t+1})  (진입 t+1 시가 → 청산 t+1+horizon 시가)

    룩어헤드 차단: 호출자가 *학습 시* feature 와 target 을 같이 join 한 뒤,
    *예측 시* 에는 target 모름.

    Returns:
        Series (NaN: index 끝 horizon+1 봉).
    """
    opens = np.log(ohlcv["open"].astype(float))
    entry = opens.shift(-1)
    exit_ = opens.shift(-(1 + horizon_bars))
    return (exit_ - entry).rename(f"target_{horizon_bars}h_logret")
