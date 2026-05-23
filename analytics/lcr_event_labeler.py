"""
analytics/lcr_event_labeler.py
=====================================================================
LCR(Liquidity Cascade Reaction) 셋업 프록시 라벨링 + forward-return 측정.

목적 (Phase 0 sanity check):
  forceOrder 스트림은 실시간만 받을 수 있지만, **OI 급감 + 거래량 폭발 + 가격 급변** 은
  과거 데이터에서 청산 캐스케이드의 *프록시* 로 쓸 수 있다. 이 프록시 이벤트의 forward
  return 을 측정해 "LCR 셋업 구조 자체에 통계적 신호가 있는가?" 를 3개월 paper 들어가기
  전에 1세션에 답한다.

  - 셋업 A 프록시(Long Flush Reversal): OI Δ ≤ -3% AND price Δ ≤ -2% AND vol ratio ≥ 3
  - 셋업 B 프록시(Short Squeeze Continuation): OI Δ ≤ -3% AND price Δ ≥ +2% AND vol ratio ≥ 3
  - BTC 동반 급락 분리(셋업 C 정당성 검증): BTC 1h Δ ≤ -1.2% 인 경우 vs 단독.

룩어헤드 차단: 신호는 마감봉 i 기준, 진입은 봉 i+1 시가. forward return 도 i+1 시가 기준.
=====================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class LCRLabelConfig:
    oi_drop_threshold: float = -0.03         # OI 변화 ≤ -3%
    vol_ratio_threshold: float = 3.0         # 현재 봉 vol / trailing avg
    vol_avg_lookback: int = 24               # 1h × 24 = 24h 평균
    price_drop_threshold: float = -0.02      # 셋업 A 가격 임계 (-2%)
    price_rise_threshold: float = 0.02       # 셋업 B 가격 임계 (+2%)
    btc_coincident_threshold: float = -0.012  # BTC 1h Δ ≤ -1.2% 면 동반 급락


def label_events(
    ohlcv: pd.DataFrame,
    oi: Optional[pd.Series],
    btc_returns: Optional[pd.Series],
    cfg: Optional[LCRLabelConfig] = None,
) -> pd.DataFrame:
    """심볼 1종의 LCR 셋업 프록시 이벤트 라벨링.

    Args:
        ohlcv: index=ts(UTC), cols=open/high/low/close/volume.
        oi: open_interest Series (ohlcv 와 동일 index). None 이면 OI 게이트 비활성.
        btc_returns: BTC 1h pct_change Series. None 이면 BTC 동반 컬럼 NaN.
        cfg: LCRLabelConfig.

    Returns:
        DataFrame index=event_ts, cols=[setup, oi_delta, vol_ratio, price_delta,
        btc_delta, btc_coincident].
    """
    cfg = cfg or LCRLabelConfig()
    if len(ohlcv) < cfg.vol_avg_lookback + 2:
        return pd.DataFrame(columns=[
            "setup", "oi_delta", "vol_ratio", "price_delta", "btc_delta", "btc_coincident"
        ])

    close = ohlcv["close"]
    vol = ohlcv["volume"]
    price_delta = close.pct_change()
    vol_avg = vol.rolling(cfg.vol_avg_lookback).mean().shift(1)  # 직전까지 평균(현재 봉 제외)
    vol_ratio = vol / vol_avg

    if oi is not None:
        oi_aligned = oi.reindex(ohlcv.index)
        oi_delta = oi_aligned.pct_change()
        oi_mask = oi_delta <= cfg.oi_drop_threshold
    else:
        oi_delta = pd.Series(np.nan, index=ohlcv.index)
        oi_mask = pd.Series(True, index=ohlcv.index)   # OI 없으면 가격+볼만으로

    vol_mask = vol_ratio >= cfg.vol_ratio_threshold
    a_mask = oi_mask & vol_mask & (price_delta <= cfg.price_drop_threshold)
    b_mask = oi_mask & vol_mask & (price_delta >= cfg.price_rise_threshold)

    if btc_returns is not None:
        btc_aligned = btc_returns.reindex(ohlcv.index)
    else:
        btc_aligned = pd.Series(np.nan, index=ohlcv.index)

    out = pd.DataFrame({
        "setup": np.where(a_mask, "A", np.where(b_mask, "B", "")),
        "oi_delta": oi_delta,
        "vol_ratio": vol_ratio,
        "price_delta": price_delta,
        "btc_delta": btc_aligned,
        "btc_coincident": btc_aligned <= cfg.btc_coincident_threshold,
    }, index=ohlcv.index)
    return out[out["setup"] != ""].copy()


def compute_forward_returns(
    events: pd.DataFrame,
    ohlcv: pd.DataFrame,
    horizons_bars: tuple = (1, 4, 24),
) -> pd.DataFrame:
    """이벤트별 forward return(% — 봉 i+1 시가 기준 long).

    셋업 A/B 모두 LONG 가설(A=반등, B=지속). 양(+) 이면 가설 지지.
    """
    if events.empty:
        return events.assign(**{f"fwd_{h}h": [] for h in horizons_bars})
    res = events.copy()
    idx_pos = {ts: i for i, ts in enumerate(ohlcv.index)}
    opens = ohlcv["open"].to_numpy(float)
    closes = ohlcv["close"].to_numpy(float)
    n = len(ohlcv)

    for h in horizons_bars:
        col = f"fwd_{h}h"
        vals = []
        for ts in res.index:
            i = idx_pos.get(ts)
            if i is None or i + 1 >= n or i + h >= n:
                vals.append(np.nan)
                continue
            entry = opens[i + 1]
            exit_p = closes[i + h]
            if entry <= 0:
                vals.append(np.nan)
            else:
                vals.append((exit_p - entry) / entry)
        res[col] = vals
    return res


def summarize(events_with_fwd: pd.DataFrame, horizon_col: str) -> Dict[str, float]:
    """이벤트 집합의 forward return 요약 통계."""
    v = events_with_fwd[horizon_col].dropna()
    if v.empty:
        return {"n": 0, "mean": 0.0, "median": 0.0, "pos_share": 0.0}
    return {
        "n": int(len(v)),
        "mean": float(v.mean()),
        "median": float(v.median()),
        "pos_share": float((v > 0).mean()),
    }


def per_symbol_decomposition(
    all_events: pd.DataFrame, horizon_col: str
) -> Dict[str, Dict[str, float]]:
    """심볼별 평균 fwd return + 건수. all_events 는 'symbol' 컬럼 포함 가정."""
    out: Dict[str, Dict[str, float]] = {}
    if "symbol" not in all_events.columns or all_events.empty:
        return out
    for sym, g in all_events.groupby("symbol"):
        s = summarize(g, horizon_col)
        out[sym] = {"n": s["n"], "mean": s["mean"], "pos_share": s["pos_share"]}
    return out


def concentration_share(all_events: pd.DataFrame, horizon_col: str) -> float:
    """단일 종목 평균기여 비율(상위 종목 평균 fwd return × 그 종목 비중)."""
    if "symbol" not in all_events.columns or all_events.empty:
        return 0.0
    # 단순 단일종목 기여 = max(symbol_count * mean_return) / sum(symbol_count * mean_return) — 절댓값
    contrib = {}
    total = 0.0
    for sym, g in all_events.groupby("symbol"):
        m = g[horizon_col].dropna().sum()
        contrib[sym] = m
        total += abs(m)
    if total <= 0 or not contrib:
        return 0.0
    return max(abs(v) for v in contrib.values()) / total
