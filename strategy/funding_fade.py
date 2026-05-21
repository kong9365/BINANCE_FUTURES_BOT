"""
strategy/funding_fade.py
=====================================================================
펀딩 극단 역추세(평균회귀) 전략 — P3 / 검증 후보.

엣지 가설(구조적, docs/STRATEGY_REALISM_REVIEW.md §2 #1):
  펀딩비는 실재 현금흐름이다. 펀딩이 극단(한쪽 쏠림)이면 군중이 비용을 치르며
  과밀 포지션을 유지하는 상태 → 한계수요 소진 시 프리미엄 붕괴·되돌림. 따라서
  **과밀된 쪽을 페이드**한다(현 OI-급증/돌파의 '추종'과 정반대 메커니즘).

신호(저(低)노브 — 과적합 억제):
  - 펀딩 z-score = (fr − rolling_mean) / rolling_std (윈도 W).
  - z ≥ +zthr (롱 과밀, 펀딩 高) → **SHORT**(페이드).
  - z ≤ −zthr (숏 과밀, 펀딩 低/음) → **LONG**.
  - **레짐 게이트**: ADX < adx_max 인 비추세(횡보)에서만(추세장 페이드=knife-catch 회피).
  진입/청산 가격·TP/SL 은 호출자(포트폴리오 하네스)가 ATR 기반으로 처리.

설계: 순수 함수, 룩어헤드 없음(신호=마감봉 i, 진입=i+1). 지표는 portfolio_backtest
의 검증된 시리즈(strategy/breakout 와 등가)를 재사용.
=====================================================================
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtesting.portfolio_backtest import atr_series, adx_series


@dataclass
class FundingFadeConfig:
    z_window: int = 30          # 펀딩 z-score 롤링 윈도(봉)
    z_threshold: float = 1.5    # |z| 이 값 이상이면 극단
    adx_period: int = 14
    adx_max: float = 25.0       # 이 미만(비추세)에서만 페이드
    atr_period: int = 14


def compute_funding_fade_signals(df: pd.DataFrame, cfg: FundingFadeConfig) -> pd.DataFrame:
    """펀딩 페이드 신호 컬럼(signal: +1 LONG/−1 SHORT/0, atr) 추가.

    df 는 'funding_rate' 컬럼 필요(없으면 무신호). 신호는 마감봉 i 기준.
    """
    out = df.copy()
    n = len(df)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    atr = atr_series(h, l, c, cfg.atr_period)
    out["atr"] = atr

    if "funding_rate" not in df.columns:
        out["signal"] = 0
        return out

    adx = adx_series(h, l, c, cfg.adx_period)
    fr = df["funding_rate"].astype(float)
    mean = fr.rolling(cfg.z_window).mean()
    std = fr.rolling(cfg.z_window).std()
    z = ((fr - mean) / std).to_numpy()

    sig = np.zeros(n, dtype=int)
    valid = ~(np.isnan(z) | np.isnan(adx) | np.isnan(atr)) & (atr > 0) & (adx < cfg.adx_max)
    short_c = valid & (z >= cfg.z_threshold)     # 롱 과밀 → 페이드 SHORT
    long_c = valid & (z <= -cfg.z_threshold)      # 숏 과밀 → 페이드 LONG
    sig[short_c] = -1
    sig[long_c] = 1
    out["signal"] = sig
    return out
