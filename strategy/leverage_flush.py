"""
strategy/leverage_flush.py
=====================================================================
Leverage Flush 6-조건 동시 base trigger (CCS-Lite v1.1).

근거 (docs/HANDOFF.md CCS-Lite v1.1 §설계):
  단일 신호(OI/vol/price만)은 알트 뉴스 1방·신규상장·밈펌프에 너무 자주 false
  event 를 만든다. 마이크로구조 확인 3개를 *추가* AND 결합해 *진짜* 청산 캐스
  케이드 직후 매도세 소진 + 일시 매수 우세 + BTC 동조 위험 낮음의 셋을 모두
  만족할 때만 1개 이벤트로 인정.

6 조건 (모두 AND):
  1) OI 변화 ≤ -3%               (15~60분 윈도 lookback_bars)
  2) 거래량 비율 ≥ 3×            (24h 평균 대비)
  3) 가격 변화 ≤ -2%             (이벤트 봉)
  4) CVD 방향이 가격과 일치     (kline taker buy < total/2 = 매도 우세)
  5) taker_buy_ratio > 0.55     (회복 봉에서 매수 우세 — 신호 봉 자체)
  6) BTC 상관 < 0.7              (롤링 30봉 BTC return 상관)

설계 원칙:
  - 순수 함수 (DataFrame/Series 입력, 출력 DataFrame). I/O 없음.
  - 룩어헤드 차단: 모든 지표는 마감봉 i 기준 *직전까지*의 정보만 사용.
  - 백테스트(과거)와 라이브(실시간)가 동일 함수 호출 — 괴리 방지.
  - 조건 4·5 는 Binance kline 의 taker_buy_base_asset_volume 컬럼 사용.
    (없으면 fallback: 가격 모멘텀으로 근사 — 명시적으로 표시).
=====================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class LeverageFlushConfig:
    """6 조건 임계 (사전확정, ±10% 보정도 안 함 — 자유도 0)."""

    # 조건 1: OI 변화
    oi_drop_threshold: float = -0.03           # ≤ -3%
    oi_lookback_bars: int = 1                  # 직전 1봉 (1h interval → 1h 변화)

    # 조건 2: 거래량 비율
    vol_ratio_threshold: float = 3.0
    vol_avg_lookback: int = 24                 # 24봉 평균(1h × 24 = 24h)

    # 조건 3: 가격 변화
    price_drop_threshold: float = -0.02

    # 조건 4: CVD 방향 일치
    cvd_aligned_required: bool = True

    # 조건 5: taker_buy_ratio 회복 봉
    taker_buy_ratio_min: float = 0.55

    # 조건 6: BTC 상관
    btc_corr_max: float = 0.70
    btc_corr_window: int = 30                  # 롤링 30봉 상관(1h × 30 = 30h)


@dataclass
class LFEvent:
    """Leverage Flush 이벤트 1건.

    Attributes:
        ts: 이벤트 시각(마감봉 시각, UTC).
        symbol: 심볼.
        oi_change_pct: OI 변화율.
        vol_ratio: 거래량 비율.
        price_change_pct: 가격 변화율.
        cvd_aligned: CVD 방향 일치 여부.
        taker_buy_ratio: 이벤트 봉의 매수 taker 비율.
        btc_correlation: 이벤트 시점 직전까지의 BTC 상관.
    """

    ts: pd.Timestamp
    symbol: str
    oi_change_pct: float
    vol_ratio: float
    price_change_pct: float
    cvd_aligned: bool
    taker_buy_ratio: float
    btc_correlation: float


def _compute_taker_buy_ratio(
    taker_buy_volume: Optional[pd.Series],
    total_volume: pd.Series,
) -> pd.Series:
    """봉별 매수 taker 비율 = taker_buy / total_volume.

    Binance kline 의 'taker_buy_base_asset_volume' 컬럼 사용 가정. None 이면
    NaN 시리즈 반환(호출자가 fallback).
    """
    if taker_buy_volume is None:
        return pd.Series(np.nan, index=total_volume.index)
    safe_total = total_volume.replace(0.0, np.nan)
    return (taker_buy_volume / safe_total).clip(lower=0.0, upper=1.0)


def _compute_cvd_aligned(
    taker_buy_ratio: pd.Series,
    price_change: pd.Series,
) -> pd.Series:
    """CVD(누적 거래량 델타) 방향이 가격과 일치하는지(매도세 우세인 하락봉).

    근사: taker_buy_ratio < 0.5 (매도 taker > 매수 taker) AND price_change < 0
    → 청산 캐스케이드 특징(공격적 매도자 우세). 둘 다 모두 같은 방향(음)이면 True.
    상승 봉(가설 LONG 진입은 하락 봉에서만이라 사용 안 함)에는 False.
    """
    sell_pressure = taker_buy_ratio < 0.5
    price_falling = price_change < 0
    return (sell_pressure & price_falling).fillna(False)


def _compute_btc_correlation(
    symbol_returns: pd.Series,
    btc_returns: pd.Series,
    window: int,
) -> pd.Series:
    """롤링 window 봉 BTC return 상관(Pearson). NaN 자동 처리."""
    if btc_returns is None or len(btc_returns) == 0:
        return pd.Series(np.nan, index=symbol_returns.index)
    aligned = btc_returns.reindex(symbol_returns.index)
    return symbol_returns.rolling(window).corr(aligned)


def detect_events(
    ohlcv: pd.DataFrame,
    oi: Optional[pd.Series],
    btc_returns: Optional[pd.Series],
    symbol: str = "",
    cfg: Optional[LeverageFlushConfig] = None,
) -> pd.DataFrame:
    """1개 심볼의 1h ohlcv 시리즈에서 LF 이벤트 라벨링.

    Args:
        ohlcv: index=ts(UTC) 정렬, cols 최소 [open, high, low, close, volume,
               taker_buy_base_asset_volume]. taker 컬럼이 없으면 cvd_aligned 와
               taker_buy_ratio 조건은 *자동 실패* 처리(보수적).
        oi: open_interest Series (ohlcv 와 동일 index). None 이면 빈 결과.
        btc_returns: BTC 1h pct_change Series. None 이면 조건 6 무시(통과 처리).
        symbol: 심볼 라벨링용.
        cfg: 설정.

    Returns:
        DataFrame index=event_ts, cols=[symbol, oi_change_pct, vol_ratio,
        price_change_pct, cvd_aligned, taker_buy_ratio, btc_correlation].
        조건 6 모두 만족한 봉만 포함.

    룩어헤드 차단:
        - 모든 rolling/pct_change 는 봉 i 기준 *그 봉까지*의 데이터만 사용.
        - 호출자는 이벤트 봉 *다음* 봉 시가에 진입 가정으로 forward return 측정.
    """
    cfg = cfg or LeverageFlushConfig()

    empty_cols = [
        "symbol", "oi_change_pct", "vol_ratio", "price_change_pct",
        "cvd_aligned", "taker_buy_ratio", "btc_correlation",
    ]
    if ohlcv is None or ohlcv.empty:
        return pd.DataFrame(columns=empty_cols)

    min_bars = max(cfg.vol_avg_lookback + 2, cfg.btc_corr_window + 2, cfg.oi_lookback_bars + 2)
    if len(ohlcv) < min_bars:
        return pd.DataFrame(columns=empty_cols)

    close = ohlcv["close"]
    vol = ohlcv["volume"]
    taker_buy = ohlcv["taker_buy_base_asset_volume"] if "taker_buy_base_asset_volume" in ohlcv.columns else None

    # 조건 3: 가격 변화 (직전 봉 대비, 1h 변화)
    price_change = close.pct_change()

    # 조건 2: 거래량 비율 (현재 봉 vs 직전 N봉 평균; 직전까지 평균 = shift(1))
    vol_avg = vol.rolling(cfg.vol_avg_lookback).mean().shift(1)
    vol_ratio = (vol / vol_avg).replace([np.inf, -np.inf], np.nan)

    # 조건 1: OI 변화 (lookback_bars 봉 전 대비)
    if oi is not None:
        oi_aligned = oi.reindex(ohlcv.index)
        oi_change = oi_aligned.pct_change(periods=cfg.oi_lookback_bars)
    else:
        return pd.DataFrame(columns=empty_cols)

    # 조건 4·5: taker_buy_ratio & CVD
    taker_ratio = _compute_taker_buy_ratio(taker_buy, vol)
    cvd_aligned = _compute_cvd_aligned(taker_ratio, price_change)

    # 조건 6: BTC 상관 (롤링 윈도)
    if btc_returns is not None:
        btc_corr = _compute_btc_correlation(price_change, btc_returns, cfg.btc_corr_window)
    else:
        # BTC 데이터 없으면 조건 6 자동 통과(보수적 0.0)
        btc_corr = pd.Series(0.0, index=ohlcv.index)

    # 6 조건 AND
    c1 = oi_change <= cfg.oi_drop_threshold
    c2 = vol_ratio >= cfg.vol_ratio_threshold
    c3 = price_change <= cfg.price_drop_threshold
    if cfg.cvd_aligned_required:
        c4 = cvd_aligned
    else:
        c4 = pd.Series(True, index=ohlcv.index)
    c5 = taker_ratio > cfg.taker_buy_ratio_min
    c6 = btc_corr < cfg.btc_corr_max

    mask = c1 & c2 & c3 & c4 & c5 & c6
    mask = mask.fillna(False)

    if not mask.any():
        return pd.DataFrame(columns=empty_cols)

    out = pd.DataFrame({
        "symbol": symbol,
        "oi_change_pct": oi_change,
        "vol_ratio": vol_ratio,
        "price_change_pct": price_change,
        "cvd_aligned": cvd_aligned,
        "taker_buy_ratio": taker_ratio,
        "btc_correlation": btc_corr,
    }, index=ohlcv.index)
    return out[mask].copy()


def evaluate_latest(
    ohlcv: pd.DataFrame,
    oi: Optional[pd.Series],
    btc_returns: Optional[pd.Series],
    symbol: str = "",
    cfg: Optional[LeverageFlushConfig] = None,
) -> Optional[LFEvent]:
    """가장 최근 마감봉 1개에 대한 LF 트리거 평가(라이브용).

    Returns:
        조건 만족 시 LFEvent, 아니면 None.
    """
    events = detect_events(ohlcv, oi, btc_returns, symbol=symbol, cfg=cfg)
    if events.empty:
        return None
    last_ts = ohlcv.index[-1]
    if last_ts not in events.index:
        return None
    row = events.loc[last_ts]
    return LFEvent(
        ts=last_ts,
        symbol=symbol,
        oi_change_pct=float(row["oi_change_pct"]),
        vol_ratio=float(row["vol_ratio"]),
        price_change_pct=float(row["price_change_pct"]),
        cvd_aligned=bool(row["cvd_aligned"]),
        taker_buy_ratio=float(row["taker_buy_ratio"]),
        btc_correlation=float(row["btc_correlation"]),
    )
