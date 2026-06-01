"""
strategy/breakout.py
=====================================================================
Donchian/ATR 돌파 전략 + ADX·200EMA 레짐 게이트 (P1).

배경 (docs/STRATEGY_REALISM_REVIEW.md §2):
  현 OI-급증 모멘텀은 끝난 스파이크를 추종(역선택)해 20일 표본 전 구간 음의 기대값.
  대체 전략 #1로 **추세 지속(time-series momentum)** 을 포착하는 Donchian 돌파를
  채택한다. 저승률·고R(우측꼬리) 프로파일이며 OHLCV 만으로 백테스트 가능하고
  스팟 레그가 필요 없어 보호자산 룰과 충돌하지 않는다.

엣지 가설:
  N기간 채널 상단 돌파 = 새 상승 추세 시작 신호. 추세장(ADX≥임계)에서만,
  그리고 가격이 200EMA 위(상승 편향)일 때만 LONG. 하락은 대칭(SHORT).
  → "추세가 아닐 때는 거래하지 않는다"가 휩쏘 방어의 핵심.

설계 원칙:
  - **순수 함수**: 입력은 마감봉 리스트(o,h,l,c,v,ts)뿐. 외부 상태·네트워크 없음.
    → 백테스트(BacktestEngine)와 라이브가 **동일 로직**을 공유(괴리 방지).
  - **룩어헤드 없음**: 호출자가 ts 이전 마감봉만 넘긴다(엔진 _get_candles_until).
    돌파 판정은 "직전 마감봉 종가가 그 이전 N봉 채널을 넘었는가"로, 진입은 다음
    봉 시가에서 일어난다(엔진이 처리).
  - 지표(EMA/ATR/ADX/Donchian)는 의존성 없이 직접 구현(검증 가능).
=====================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


@dataclass
class BreakoutConfig:
    """돌파 전략 파라미터. 기본값은 실무 컨센서스(Turtle/Donchian)에 기반."""

    donchian_entry: int = 20      # 진입 채널 기간(직전 N봉 고/저)
    donchian_exit: int = 10       # 청산 채널 기간(반대 방향, 트레일용 — 엔진 확장 예약)
    adx_period: int = 14
    adx_trend_min: float = 25.0   # 이 미만이면 추세 아님 → 무거래
    ema_period: int = 200         # 장기 추세 편향 필터
    atr_period: int = 14
    atr_stop_mult: float = 2.0    # 초기 손절 = 진입 ∓ 2·ATR
    atr_target_mult: float = 4.0  # 목표 = 진입 ± 4·ATR (R:R 2:1)

    def min_bars(self) -> int:
        """신호 평가에 필요한 최소 마감봉 수."""
        return max(
            self.ema_period,
            self.donchian_entry + 1,
            2 * self.adx_period + 1,
            self.atr_period + 1,
        )


@dataclass
class BreakoutSignal:
    """돌파 신호 1건 (방향 + 지표값). 진입가/TP/SL 산출은 호출자(엔진) 책임."""

    action: str        # "LONG" | "SHORT"
    atr: float
    adx: float
    ema: float
    level: float       # 돌파한 채널 경계(상단=LONG, 하단=SHORT)


# ─────────────────────────────────────────────────────
# 지표 (순수 함수, 마감봉 리스트 입력)
# ─────────────────────────────────────────────────────
def ema(values: Sequence[float], period: int) -> Optional[float]:
    """마지막 EMA 값. SMA(period)로 시드 후 표준 평활(2/(period+1)). 부족 시 None."""
    n = len(values)
    if period <= 0 or n < period:
        return None
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int) -> float:
    """Wilder ATR (마지막 값). period+1 봉 미만이면 0.0."""
    n = len(closes)
    if n < period + 1:
        return 0.0
    trs: List[float] = []
    for i in range(1, n):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int) -> float:
    """Wilder ADX (마지막 값). 2·period+1 봉 미만이면 0.0.

    +DM/-DM/TR 을 Wilder 평활 → +DI/-DI → DX → DX 의 Wilder 평균 = ADX.
    """
    n = len(closes)
    if n < 2 * period + 1:
        return 0.0
    plus_dm: List[float] = []
    minus_dm: List[float] = []
    trs: List[float] = []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))

    def _wilder(arr: List[float]) -> List[float]:
        s = sum(arr[:period])
        out = [s]
        for x in arr[period:]:
            s = s - s / period + x
            out.append(s)
        return out

    tr_s = _wilder(trs)
    plus_s = _wilder(plus_dm)
    minus_s = _wilder(minus_dm)

    dx: List[float] = []
    for trv, pv, mv in zip(tr_s, plus_s, minus_s):
        if trv <= 0:
            dx.append(0.0)
            continue
        pdi = 100.0 * pv / trv
        mdi = 100.0 * mv / trv
        denom = pdi + mdi
        dx.append(100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0)

    if len(dx) < period:
        return 0.0
    a = sum(dx[:period]) / period
    for x in dx[period:]:
        a = (a * (period - 1) + x) / period
    return a


def donchian(highs: Sequence[float], lows: Sequence[float],
             period: int) -> Optional[Tuple[float, float]]:
    """직전 N봉(마지막 봉 제외)의 (상단=최고가, 하단=최저가). 부족 시 None.

    돌파 기준선은 '현재(마지막) 마감봉을 제외한' 직전 N봉으로 잡아야 한다
    (마지막 봉의 종가가 이 채널을 넘었는지 판정).
    """
    if len(highs) < period + 1:
        return None
    window_high = highs[-(period + 1):-1]
    window_low = lows[-(period + 1):-1]
    return max(window_high), min(window_low)


def sma(values: Sequence[float], period: int) -> Optional[float]:
    """마지막 단순이동평균(직전 period봉 종가 평균). 부족 시 None."""
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def rolling_return_sign(closes: Sequence[float], lookback: int) -> int:
    """closes[-1] 대비 lookback봉 전 수익률 부호: +1(상승)/-1(하락)/0(무변·부족).

    TSMOM(time-series momentum) 다중 기간 앙상블의 단위 신호.
    """
    if lookback <= 0 or len(closes) < lookback + 1:
        return 0
    prev = closes[-1 - lookback]
    if prev <= 0:
        return 0
    r = closes[-1] / prev - 1.0
    return 1 if r > 0 else (-1 if r < 0 else 0)


def rsi_wilder(closes: Sequence[float], period: int) -> Optional[float]:
    """마지막 RSI(Wilder) ∈ [0,100]. period+1봉 미만이면 None.

    indicators.rsi(pandas) 와 *수치 일치*: ewm(alpha=1/period, adjust=False) 와 동일하게
    첫 델타로 시드 후 재귀 평활(고전 SMA-시드 아님). avg_loss==0 시 RSI=100(상승만)/50(무변).
    순수 리스트판(평가기는 List[tuple] 소비) — parity 테스트로 드리프트 차단(§4).
    """
    n = len(closes)
    if period <= 0 or n < period + 1:
        return None
    alpha = 1.0 / period
    ag: Optional[float] = None
    al = 0.0
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        g = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        if ag is None:                       # 첫 델타 시드 (adjust=False)
            ag, al = g, loss
        else:
            ag = (1 - alpha) * ag + alpha * g
            al = (1 - alpha) * al + alpha * loss
    if al == 0:
        return 100.0 if (ag or 0.0) > 0 else 50.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


def stdev(values: Sequence[float], period: int) -> Optional[float]:
    """직전 period봉 표본표준편차(ddof=1). 볼린저밴드용 — indicators.bollinger_bands
    (pandas .std(ddof=1))와 동일 정의. period≤1 또는 부족 시 None.
    """
    if period <= 1 or len(values) < period:
        return None
    w = values[-period:]
    m = sum(w) / period
    var = sum((x - m) ** 2 for x in w) / (period - 1)
    return var ** 0.5


# ─────────────────────────────────────────────────────
# 신호 평가
# ─────────────────────────────────────────────────────
def evaluate_breakout(
    closed: List[tuple],
    cfg: Optional[BreakoutConfig] = None,
) -> Optional[BreakoutSignal]:
    """마감봉 리스트로 돌파 신호 평가. 신호 없으면 None.

    Args:
        closed: (open, high, low, close, volume, timestamp) 튜플 리스트(시간 오름차순,
            모두 평가 시점 ts '미만'의 마감봉). 엔진의 _get_candles_until 출력 형식.
        cfg: BreakoutConfig (None 이면 기본값).

    Returns:
        BreakoutSignal | None.

    로직(AND):
        LONG  = 직전 마감봉 종가 > 직전 N봉 채널 상단  AND  종가 > 200EMA  AND  ADX ≥ 임계
        SHORT = 직전 마감봉 종가 < 직전 N봉 채널 하단  AND  종가 < 200EMA  AND  ADX ≥ 임계
    """
    cfg = cfg or BreakoutConfig()
    if len(closed) < cfg.min_bars():
        return None

    highs = [c[1] for c in closed]
    lows = [c[2] for c in closed]
    closes = [c[3] for c in closed]
    last_close = closes[-1]

    adx_val = adx(highs, lows, closes, cfg.adx_period)
    if adx_val < cfg.adx_trend_min:        # 추세 아님 → 무거래(휩쏘 방어)
        return None

    ema_val = ema(closes, cfg.ema_period)
    if ema_val is None:
        return None

    ch = donchian(highs, lows, cfg.donchian_entry)
    if ch is None:
        return None
    upper, lower = ch

    a = atr(highs, lows, closes, cfg.atr_period)
    if a <= 0:
        return None

    if last_close > upper and last_close > ema_val:
        return BreakoutSignal("LONG", a, adx_val, ema_val, upper)
    if last_close < lower and last_close < ema_val:
        return BreakoutSignal("SHORT", a, adx_val, ema_val, lower)
    return None
