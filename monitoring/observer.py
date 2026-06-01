"""
monitoring/observer.py
=====================================================================
관찰 지표(거래량·OI·추세·taker) 산출 + STRONG/WEAK 분류 — 순수·룩어헤드0.

★ 자동매매 아님 = *관찰 보고*. 방향 적중 확률은 절대 산출하지 않는다(가짜확률 금지).
  지표는 "정렬 상태"로만 본다. 신호는 5회 검증에서 무엣지로 확인된 그 신호들이다.

설계:
  - 순수 함수. 입력은 *마감봉 리스트*(현재 형성 중 봉은 호출자가 제외 = 룩어헤드0).
  - 마지막 원소 = 가장 최근 마감봉(= 신호봉). 지표는 그 봉 + 직전 봉들로만 계산.
  - 지표(EMA/ATR/Donchian)는 strategy.breakout 공유(중복 구현 금지).
  - OI 는 과거 데이터에 없을 수 있음 → None 이면 3지표(거래량·추세·taker)로 분류.

4지표(운영자 통찰: 거래량=실체, taker 불균형):
  1. 거래량: 현재봉 거래량 ÷ 직전 N봉 평균 (배수)
  2. OI: 미결제약정 변화율 (%) — *실시간 전용*(과거 없음). |변화|를 활발도로 봄(방향무관 확인지표).
  3. 추세: close vs EMA200 + Donchian20 돌파 (롱/숏 방향 앵커)
  4. taker: taker_buy 비율 (%) — 매수 공격성
=====================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from strategy.breakout import atr, donchian, ema
from config.settings import MONITORING_CONFIG, MonitoringConfig


@dataclass
class Bar:
    """마감봉 1개 (관찰용). taker_buy = taker 매수 체결량(base)."""
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy: float
    ts: object = None


@dataclass
class IndicatorState:
    """4지표 산출 상태 (정렬 판정 전 raw 값)."""
    vol_ratio: Optional[float]          # 현재봉/직전N평균
    taker_ratio: Optional[float]        # taker_buy/volume ∈ [0,1]
    trend_dir: Optional[str]            # "LONG"|"SHORT"|None (EMA200+Donchian)
    oi_change_pct: Optional[float]      # None 이면 OI 미사용(과거)
    ema_val: Optional[float]
    atr_val: float
    close: float


@dataclass
class Observation:
    """분류 결과. grade ∈ STRONG/WEAK/NONE. *방향확률 없음*(관찰 보고)."""
    grade: str
    direction: Optional[str]            # grade!=NONE 일 때 LONG/SHORT
    aligned: dict                       # {trend,volume,taker,oi: bool|None}
    state: IndicatorState


def compute_indicators(
    bars: List[Bar],
    oi_now: Optional[float] = None,
    oi_prev: Optional[float] = None,
    cfg: Optional[MonitoringConfig] = None,
) -> Optional[IndicatorState]:
    """마감봉 리스트(시간 오름차순, 현재 형성봉 제외)로 4지표 산출. 부족 시 None.

    마지막 bar = 신호봉. 룩어헤드0: 미래 봉을 보지 않는다(호출자가 마감봉만 전달).
    """
    cfg = cfg or MONITORING_CONFIG
    need = max(cfg.ema_period, cfg.donchian_period + 1, cfg.vol_avg_bars + 1, cfg.atr_period + 1)
    if len(bars) < need:
        return None
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]
    last = bars[-1]
    # 1. 거래량: 직전 N봉(현재봉 제외) 평균 대비 배수
    prior_vol = [b.volume for b in bars[-(cfg.vol_avg_bars + 1):-1]]
    vol_ma = (sum(prior_vol) / len(prior_vol)) if prior_vol else None
    vol_ratio = (last.volume / vol_ma) if (vol_ma and vol_ma > 0) else None
    # 4. taker 매수 비율
    taker_ratio = (last.taker_buy / last.volume) if last.volume > 0 else None
    # 3. 추세: EMA200 + Donchian20(직전 N봉, 현재봉 제외)
    ema_val = ema(closes, cfg.ema_period)
    ch = donchian(highs, lows, cfg.donchian_period)
    a = atr(highs, lows, closes, cfg.atr_period)
    trend_dir = None
    if ema_val is not None and ch is not None:
        up, dn = ch
        if last.close > ema_val and last.close > up:
            trend_dir = "LONG"
        elif last.close < ema_val and last.close < dn:
            trend_dir = "SHORT"
    # 2. OI 변화율 (실시간 전용; 과거 None)
    oi_change = None
    if oi_now is not None and oi_prev not in (None, 0):
        oi_change = (oi_now - oi_prev) / oi_prev * 100.0
    return IndicatorState(vol_ratio, taker_ratio, trend_dir, oi_change, ema_val, a, last.close)


def classify(state: IndicatorState, cfg: Optional[MonitoringConfig] = None) -> Observation:
    """STRONG(알림) / WEAK(로그) / NONE 분류.

    방향 앵커 = 추세(EMA200+Donchian). 추세 None 이면 신호 없음.
    STRONG: *가용 지표 전부* 강한 임계 정렬(라이브=4지표, 과거=3지표).
    WEAK  : 추세 포함 ≥3지표 완화 임계 정렬.
    OI: |변화|를 활발도로(방향무관 확인). taker: 방향별 임계. 거래량: 배수.
    ★ 확률/적중률은 반환하지 않는다.
    """
    cfg = cfg or MONITORING_CONFIG
    d = state.trend_dir
    if d is None:
        return Observation("NONE", None, {"trend": False}, state)
    oi_avail = state.oi_change_pct is not None
    vr = state.vol_ratio if state.vol_ratio is not None else 0.0
    tr = state.taker_ratio
    vol_w = vr >= cfg.vol_mult_weak
    vol_s = vr >= cfg.vol_mult_strong
    if d == "LONG":
        tak_w = tr is not None and tr >= cfg.taker_weak_long
        tak_s = tr is not None and tr >= cfg.taker_strong_long
    else:
        tak_w = tr is not None and tr <= cfg.taker_weak_short
        tak_s = tr is not None and tr <= cfg.taker_strong_short
    oi_w = oi_avail and abs(state.oi_change_pct) >= cfg.oi_change_weak
    oi_s = oi_avail and abs(state.oi_change_pct) >= cfg.oi_change_strong
    aligned = {"trend": True, "volume": vol_w, "taker": tak_w,
               "oi": (oi_w if oi_avail else None)}
    strong = vol_s and tak_s and (oi_s if oi_avail else True)
    weak_count = 1 + int(vol_w) + int(tak_w) + (int(oi_w) if oi_avail else 0)
    if strong:
        grade = "STRONG"
    elif weak_count >= 3:
        grade = "WEAK"
    else:
        grade = "NONE"
    return Observation(grade, d if grade != "NONE" else None, aligned, state)
