"""
strategy/indicators.py
=====================================================================
공용 기술지표 라이브러리 — VWMA, Bollinger Bands, RSI.

ML EV Engine R0 의 feature engineering 에서 사용. EMA/ATR/ADX 는 이미
`strategy/breakout.py` 에 있어 별도. 본 모듈은 *추가 지표만*.

설계:
  - 순수 함수, pandas Series/DataFrame 입력 출력. I/O 없음.
  - 룩어헤드 차단: 모든 rolling 은 *그 봉까지의 데이터만* 사용.
  - numpy 만으로 구현(외부 ta 라이브러리 의존 없음 — 디버깅·검증 가능).
=====================================================================
"""

from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd


def vwma(close: pd.Series, volume: pd.Series, period: int) -> pd.Series:
    """Volume-Weighted Moving Average.

    VWMA_t = Σ(close_i × volume_i) / Σ(volume_i)  for i ∈ [t-period+1, t].

    Args:
        close: tz-aware UTC index 시계열, 가격.
        volume: same index, base asset volume.
        period: 윈도 (예 100).

    Returns:
        VWMA Series (같은 index). 첫 period-1 봉은 NaN.

    Raises:
        ValueError: 길이 불일치 또는 period<=0.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if len(close) != len(volume):
        raise ValueError("close/volume length mismatch")
    pv = close * volume
    sum_pv = pv.rolling(period, min_periods=period).sum()
    sum_v = volume.rolling(period, min_periods=period).sum()
    # 0 volume 봉(매우 드묾) 보호
    return (sum_pv / sum_v.replace(0, np.nan)).rename(f"vwma_{period}")


def bollinger_bands(
    close: pd.Series, period: int = 20, num_std: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands (mid SMA + ±N std).

    Returns:
        (mid, upper, lower, percent_b, bandwidth)
            mid       — period SMA
            upper     — mid + num_std × σ
            lower     — mid − num_std × σ
            percent_b — (close − lower) / (upper − lower)  ∈ [0,1] 보통
            bandwidth — (upper − lower) / mid  (% 변동성 proxy)
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if num_std <= 0:
        raise ValueError("num_std must be positive")
    mid = close.rolling(period, min_periods=period).mean()
    std = close.rolling(period, min_periods=period).std(ddof=1)
    upper = mid + num_std * std
    lower = mid - num_std * std
    band_range = (upper - lower).replace(0, np.nan)
    percent_b = (close - lower) / band_range
    bandwidth = (upper - lower) / mid.replace(0, np.nan)
    return (
        mid.rename(f"bb_mid_{period}"),
        upper.rename(f"bb_upper_{period}"),
        lower.rename(f"bb_lower_{period}"),
        percent_b.rename(f"bb_pctb_{period}"),
        bandwidth.rename(f"bb_width_{period}"),
    )


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder smoothing).

    RSI = 100 − 100 / (1 + RS), RS = avg_gain / avg_loss (Wilder EMA, period).

    Args:
        close: 가격 series.
        period: 14 또는 6 (단기) 권장.

    Returns:
        RSI Series ∈ [0, 100]. 첫 period 봉은 NaN.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder smoothing — alpha = 1/period
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    r = 100 - 100 / (1 + rs)
    # avg_loss == 0 시 분자만 큰 양수 → r → 100; .replace 로 NaN 처리됐으니 직접 처리
    r = r.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    r = r.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)
    return r.rename(f"rsi_{period}")
