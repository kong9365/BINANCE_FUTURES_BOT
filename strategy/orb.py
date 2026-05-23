"""
strategy/orb.py
=====================================================================
Opening Range Breakout (ORB) — 인트라데이 세션-앵커 돌파 전략 (S2 / 검증 후보).

엣지 가설:
  미/유럽 세션 오버랩(14:00~15:00 UTC) 동안의 가격 레인지를 **앵커**로 잡고,
  세션 종료 이후 그 레인지 상단/하단 돌파의 단기 지속을 노린다. 일중 청산.
  Stoic Research 등이 unoptimized 룰에서 Sharpe ~1을 보고(decay 경계는 있음).

설계:
  - 신호는 **앵커 봉 종료 후**(hour > anchor_hour)부터 signal_window_bars 내 첫 breach만.
  - 룩어헤드 차단: 앵커 봉의 high/low 는 그 봉 마감(다음 봉 시작) 이후에만 사용.
    신호=마감봉 i; 진입=i+1 봉 시가(`portfolio_backtest` 규약과 동일).
  - signal_fn 인터페이스(`run_portfolio(signal_fn=...)`) 재사용 — 동일 비용/포트폴리오 모델.
  - 일별 단 1회 진입(첫 돌파). 후속 breach 는 0.
=====================================================================
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtesting.portfolio_backtest import atr_series


@dataclass
class ORBConfig:
    anchor_hour: int = 14            # UTC 시(미/유럽 오버랩 첫 시간)
    signal_window_bars: int = 6      # 앵커 종료 후 신호 허용 봉 수(1h 봉 기준 = 6h)
    atr_period: int = 14


def compute_orb_signals(df: pd.DataFrame, cfg: ORBConfig) -> pd.DataFrame:
    """ORB 신호(+1 LONG / -1 SHORT / 0) + atr 컬럼 추가.

    df: 1h UTC DatetimeIndex 권장(OHLC 필수). DatetimeIndex 가 아니면 무신호.
    """
    out = df.copy()
    n = len(df)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    atr = atr_series(h, l, c, cfg.atr_period)
    out["atr"] = atr
    sig = np.zeros(n, dtype=int)

    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        out["signal"] = sig
        return out

    hours = idx.hour.to_numpy()
    dates = np.array([t.date() for t in idx.to_pydatetime()])

    day_bars: dict = defaultdict(list)
    for i in range(n):
        day_bars[dates[i]].append(i)

    end_hour = cfg.anchor_hour + cfg.signal_window_bars
    for day, bars in day_bars.items():
        anchor_i = None
        for bi in bars:
            if hours[bi] == cfg.anchor_hour:
                anchor_i = bi
                break
        if anchor_i is None:
            continue
        rh = h[anchor_i]
        rl = l[anchor_i]
        for bi in bars:
            if bi <= anchor_i:
                continue
            if hours[bi] > end_hour:
                break
            if np.isnan(atr[bi]) or atr[bi] <= 0:
                continue
            if c[bi] > rh:
                sig[bi] = 1
                break
            if c[bi] < rl:
                sig[bi] = -1
                break

    out["signal"] = sig
    return out
